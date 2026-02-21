from __future__ import annotations

import json
from typing import Any, Literal

from ..types.group_metadata import (
    GroupCreateInput,
    GroupDescriptionUpdateInput,
    GroupJoinApprovalModeInput,
    GroupMemberAddModeInput,
    GroupMetadata,
    GroupParticipant,
    GroupParticipantsUpdateInput,
    GroupRequestParticipantsUpdateInput,
    GroupSettingUpdateInput,
    GroupSubjectUpdateInput,
    GroupToggleEphemeralInput,
    ParticipantAction,
)
from ..types.message import WAMessageStubType
from ..utils.generics import generate_message_id_v2, unix_timestamp_seconds
from ..wabinary import BinaryNode
from ..wabinary import (
    get_binary_node_child,
    get_binary_node_child_string,
    get_binary_node_children,
    is_lid_user,
    is_pn_user,
    jid_encode,
    jid_normalized_user,
)
from .chats import ChatsSocket


def _to_int(value: Any, default: int | None = None) -> int | None:
    try:
        return int(value)
    except Exception:
        return default


def extract_group_metadata(result: BinaryNode) -> GroupMetadata:
    group = get_binary_node_child(result, "group")
    if not group:
        raise ValueError("group metadata node missing <group> child")

    desc_child = get_binary_node_child(group, "description")
    desc = get_binary_node_child_string(desc_child, "body") if desc_child else None
    desc_owner = jid_normalized_user(desc_child.attrs.get("participant")) if desc_child and desc_child.attrs.get("participant") else None
    desc_owner_pn = jid_normalized_user(desc_child.attrs.get("participant_pn")) if desc_child and desc_child.attrs.get("participant_pn") else None
    desc_time = _to_int(desc_child.attrs.get("t")) if desc_child else None
    desc_id = desc_child.attrs.get("id") if desc_child else None

    raw_id = group.attrs.get("id") or ""
    group_id = raw_id if "@" in raw_id else jid_encode(raw_id, "g.us")
    eph = get_binary_node_child(group, "ephemeral")
    member_add_mode = get_binary_node_child_string(group, "member_add_mode") == "all_member_add"

    participants: list[GroupParticipant] = []
    for part in get_binary_node_children(group, "participant"):
        jid = part.attrs.get("jid", "")
        participants.append(
            GroupParticipant(
                id=jid,
                phoneNumber=part.attrs.get("phone_number") if is_lid_user(jid) and is_pn_user(part.attrs.get("phone_number")) else None,
                lid=part.attrs.get("lid") if is_pn_user(jid) and is_lid_user(part.attrs.get("lid")) else None,
                admin=part.attrs.get("type"),
            )
        )

    return GroupMetadata(
        id=group_id,
        notify=group.attrs.get("notify"),
        addressingMode="lid" if group.attrs.get("addressing_mode") == "lid" else "pn",
        subject=group.attrs.get("subject", ""),
        subjectOwner=group.attrs.get("s_o"),
        subjectOwnerPn=group.attrs.get("s_o_pn"),
        subjectTime=_to_int(group.attrs.get("s_t")),
        size=_to_int(group.attrs.get("size"), len(participants)),
        creation=_to_int(group.attrs.get("creation")),
        owner=jid_normalized_user(group.attrs.get("creator")) if group.attrs.get("creator") else None,
        ownerPn=jid_normalized_user(group.attrs.get("creator_pn")) if group.attrs.get("creator_pn") else None,
        owner_country_code=group.attrs.get("creator_country_code"),
        desc=desc,
        descId=desc_id,
        descOwner=desc_owner,
        descOwnerPn=desc_owner_pn,
        descTime=desc_time,
        linkedParent=get_binary_node_child(group, "linked_parent").attrs.get("jid") if get_binary_node_child(group, "linked_parent") else None,
        restrict=bool(get_binary_node_child(group, "locked")),
        announce=bool(get_binary_node_child(group, "announcement")),
        isCommunity=bool(get_binary_node_child(group, "parent")),
        isCommunityAnnounce=bool(get_binary_node_child(group, "default_sub_group")),
        joinApprovalMode=bool(get_binary_node_child(group, "membership_approval_mode")),
        memberAddMode=member_add_mode,
        participants=participants,
        ephemeralDuration=_to_int(eph.attrs.get("expiration")) if eph else None,
    )


class GroupsSocket(ChatsSocket):
    _group_dirty_handler_attached: bool = False
    _group_accept_invite_v4_buffered: Any = None

    async def connect(self) -> None:
        await super().connect()
        if not self._group_dirty_handler_attached:
            self.ev.on("node:ib", self._handle_group_dirty)
            self._group_dirty_handler_attached = True

    def _get_group_accept_invite_v4_runner(self) -> Any:
        if self._group_accept_invite_v4_buffered is not None:
            return self._group_accept_invite_v4_buffered

        runner: Any = self._group_accept_invite_v4_impl
        maker = getattr(self.ev, "create_buffered_function", None)
        if not callable(maker):
            maker = getattr(self.ev, "createBufferedFunction", None)
        if callable(maker):
            runner = maker(runner)

        self._group_accept_invite_v4_buffered = runner
        return runner

    async def _handle_group_dirty(self, node: BinaryNode) -> None:
        dirty = get_binary_node_child(node, "dirty")
        if not dirty:
            return
        if dirty.attrs.get("type") != "groups":
            return
        await self.group_fetch_all_participating()
        await self.clean_dirty_bits("groups")

    async def group_query(self, jid: str, type: Literal["get", "set"], content: list[BinaryNode]) -> BinaryNode:
        resolved = self.resolve_chat_jid(jid) if jid != "@g.us" else jid
        return await self.query_node(
            BinaryNode(
                tag="iq",
                attrs={"type": type, "xmlns": "w:g2", "to": resolved},
                content=content,
            )
        )

    async def group_metadata(self, jid: str) -> GroupMetadata:
        result = await self.group_query(jid, "get", [BinaryNode(tag="query", attrs={"request": "interactive"})])
        return extract_group_metadata(result)

    async def group_fetch_all_participating(self) -> dict[str, dict[str, Any]]:
        result = await self.query_node(
            BinaryNode(
                tag="iq",
                attrs={"to": "@g.us", "xmlns": "w:g2", "type": "get"},
                content=[
                    BinaryNode(
                        tag="participating",
                        attrs={},
                        content=[BinaryNode(tag="participants", attrs={}), BinaryNode(tag="description", attrs={})],
                    )
                ],
            )
        )
        data: dict[str, dict[str, Any]] = {}
        groups_child = get_binary_node_child(result, "groups")
        if groups_child:
            groups = get_binary_node_children(groups_child, "group")
            for group_node in groups:
                meta = extract_group_metadata(BinaryNode(tag="result", attrs={}, content=[group_node]))
                data[meta.id] = meta.model_dump(by_alias=True, exclude_none=True)

        await self.ev.emit("groups.update", list(data.values()))
        return data

    async def group_create(self, subject: str, participants: list[str]) -> GroupMetadata:
        key = generate_message_id_v2()
        result = await self.group_query(
            "@g.us",
            "set",
            [
                BinaryNode(
                    tag="create",
                    attrs={"subject": subject, "key": key},
                    content=[BinaryNode(tag="participant", attrs={"jid": self.resolve_chat_jid(jid)}) for jid in participants],
                )
            ],
        )
        return extract_group_metadata(result)

    async def group_leave(self, id: str) -> None:
        await self.group_query(
            "@g.us",
            "set",
            [BinaryNode(tag="leave", attrs={}, content=[BinaryNode(tag="group", attrs={"id": id})])],
        )

    async def group_update_subject(self, jid: str, subject: str) -> None:
        await self.group_query(jid, "set", [BinaryNode(tag="subject", attrs={}, content=subject.encode("utf-8"))])

    async def group_request_participants_list(self, jid: str) -> list[dict[str, str]]:
        result = await self.group_query(jid, "get", [BinaryNode(tag="membership_approval_requests", attrs={})])
        node = get_binary_node_child(result, "membership_approval_requests")
        participants = get_binary_node_children(node, "membership_approval_request")
        return [dict(v.attrs) for v in participants]

    async def group_request_participants_update(
        self,
        jid: str,
        participants: list[str],
        action: Literal["approve", "reject"],
    ) -> list[dict[str, str]]:
        result = await self.group_query(
            jid,
            "set",
            [
                BinaryNode(
                    tag="membership_requests_action",
                    attrs={},
                    content=[
                        BinaryNode(
                            tag=action,
                            attrs={},
                            content=[BinaryNode(tag="participant", attrs={"jid": self.resolve_chat_jid(item)}) for item in participants],
                        )
                    ],
                )
            ],
        )
        node = get_binary_node_child(result, "membership_requests_action")
        node_action = get_binary_node_child(node, action)
        participants_affected = get_binary_node_children(node_action, "participant")
        return [{"status": p.attrs.get("error", "200"), "jid": p.attrs.get("jid", "")} for p in participants_affected]

    async def group_participants_update(self, jid: str, participants: list[str], action: ParticipantAction) -> list[dict[str, Any]]:
        result = await self.group_query(
            jid,
            "set",
            [
                BinaryNode(
                    tag=action,
                    attrs={},
                    content=[BinaryNode(tag="participant", attrs={"jid": self.resolve_chat_jid(item)}) for item in participants],
                )
            ],
        )
        node = get_binary_node_child(result, action)
        participants_affected = get_binary_node_children(node, "participant")
        return [{"status": p.attrs.get("error", "200"), "jid": p.attrs.get("jid", ""), "content": p} for p in participants_affected]

    async def group_update_description(self, jid: str, description: str | None = None) -> None:
        metadata = await self.group_metadata(jid)
        prev = metadata.desc_id
        attrs: dict[str, str] = {"id": generate_message_id_v2()} if description else {"delete": "true"}
        if prev:
            attrs["prev"] = prev
        await self.group_query(
            jid,
            "set",
            [
                BinaryNode(
                    tag="description",
                    attrs=attrs,
                    content=[BinaryNode(tag="body", attrs={}, content=description.encode("utf-8"))] if description else None,
                )
            ],
        )

    async def group_invite_code(self, jid: str) -> str | None:
        result = await self.group_query(jid, "get", [BinaryNode(tag="invite", attrs={})])
        invite_node = get_binary_node_child(result, "invite")
        return invite_node.attrs.get("code") if invite_node else None

    async def group_revoke_invite(self, jid: str) -> str | None:
        result = await self.group_query(jid, "set", [BinaryNode(tag="invite", attrs={})])
        invite_node = get_binary_node_child(result, "invite")
        return invite_node.attrs.get("code") if invite_node else None

    async def group_accept_invite(self, code: str) -> str | None:
        results = await self.group_query("@g.us", "set", [BinaryNode(tag="invite", attrs={"code": code})])
        result = get_binary_node_child(results, "group")
        return result.attrs.get("jid") if result else None

    async def group_revoke_invite_v4(self, group_jid: str, invited_jid: str) -> bool:
        result = await self.group_query(
            group_jid,
            "set",
            [BinaryNode(tag="revoke", attrs={}, content=[BinaryNode(tag="participant", attrs={"jid": self.resolve_chat_jid(invited_jid)})])],
        )
        return bool(result)

    async def group_accept_invite_v4(self, key: str | dict[str, Any], invite_message: dict[str, Any]) -> str | None:
        runner = self._get_group_accept_invite_v4_runner()
        return await runner(key, invite_message)

    async def _group_accept_invite_v4_impl(self, key: str | dict[str, Any], invite_message: dict[str, Any]) -> str | None:
        key_obj = {"remoteJid": key} if isinstance(key, str) else dict(key)
        group_jid = invite_message.get("groupJid") or invite_message.get("group_jid")
        invite_code = invite_message.get("inviteCode") or invite_message.get("invite_code")
        invite_expiration = invite_message.get("inviteExpiration") or invite_message.get("invite_expiration")
        if not group_jid or not invite_code or invite_expiration is None:
            raise ValueError("invite_message must include groupJid/inviteCode/inviteExpiration")

        results = await self.group_query(
            group_jid,
            "set",
            [
                BinaryNode(
                    tag="accept",
                    attrs={
                        "code": str(invite_code),
                        "expiration": str(invite_expiration),
                        "admin": str(key_obj.get("remoteJid", "")),
                    },
                )
            ],
        )

        if key_obj.get("id"):
            expired_invite = dict(invite_message)
            expired_invite["inviteExpiration"] = 0
            expired_invite["inviteCode"] = ""
            await self.ev.emit("messages.update", [{"key": key_obj, "update": {"message": {"groupInviteMessage": expired_invite}}}])

        await self.upsert_message(
            {
                "key": {
                    "remoteJid": group_jid,
                    "id": generate_message_id_v2(self._me_info().get("id")),
                    "fromMe": False,
                    "participant": key_obj.get("remoteJid"),
                },
                "messageStubType": int(WAMessageStubType.GROUP_PARTICIPANT_ADD),
                "messageStubParameters": [json.dumps(self._me_info())],
                "participant": key_obj.get("remoteJid"),
                "messageTimestamp": unix_timestamp_seconds(),
            },
            "notify",
        )

        return results.attrs.get("from")

    async def group_get_invite_info(self, code: str) -> GroupMetadata:
        results = await self.group_query("@g.us", "get", [BinaryNode(tag="invite", attrs={"code": code})])
        return extract_group_metadata(results)

    async def group_toggle_ephemeral(self, jid: str, ephemeral_expiration: int) -> None:
        content = (
            BinaryNode(tag="ephemeral", attrs={"expiration": str(ephemeral_expiration)})
            if ephemeral_expiration
            else BinaryNode(tag="not_ephemeral", attrs={})
        )
        await self.group_query(jid, "set", [content])

    async def group_setting_update(self, jid: str, setting: Literal["announcement", "not_announcement", "locked", "unlocked"]) -> None:
        await self.group_query(jid, "set", [BinaryNode(tag=setting, attrs={})])

    async def group_member_add_mode(self, jid: str, mode: Literal["admin_add", "all_member_add"]) -> None:
        await self.group_query(jid, "set", [BinaryNode(tag="member_add_mode", attrs={}, content=mode)])

    async def group_join_approval_mode(self, jid: str, mode: Literal["on", "off"]) -> None:
        await self.group_query(
            jid,
            "set",
            [BinaryNode(tag="membership_approval_mode", attrs={}, content=[BinaryNode(tag="group_join", attrs={"state": mode})])],
        )

    # typed convenience interfaces
    async def create_group(self, request: GroupCreateInput | dict[str, Any]) -> GroupMetadata:
        payload = request if isinstance(request, GroupCreateInput) else GroupCreateInput.model_validate(request)
        return await self.group_create(payload.subject, payload.participants)

    async def update_group_participants(
        self, request: GroupParticipantsUpdateInput | dict[str, Any]
    ) -> list[dict[str, Any]]:
        payload = (
            request
            if isinstance(request, GroupParticipantsUpdateInput)
            else GroupParticipantsUpdateInput.model_validate(request)
        )
        return await self.group_participants_update(payload.jid, payload.participants, payload.action)

    async def update_group_requests(
        self, request: GroupRequestParticipantsUpdateInput | dict[str, Any]
    ) -> list[dict[str, str]]:
        payload = (
            request
            if isinstance(request, GroupRequestParticipantsUpdateInput)
            else GroupRequestParticipantsUpdateInput.model_validate(request)
        )
        return await self.group_request_participants_update(payload.jid, payload.participants, payload.action)

    async def update_group_subject(self, request: GroupSubjectUpdateInput | dict[str, Any]) -> None:
        payload = (
            request
            if isinstance(request, GroupSubjectUpdateInput)
            else GroupSubjectUpdateInput.model_validate(request)
        )
        await self.group_update_subject(payload.jid, payload.subject)

    async def update_group_description(self, request: GroupDescriptionUpdateInput | dict[str, Any]) -> None:
        payload = (
            request
            if isinstance(request, GroupDescriptionUpdateInput)
            else GroupDescriptionUpdateInput.model_validate(request)
        )
        await self.group_update_description(payload.jid, payload.description)

    async def update_group_setting(self, request: GroupSettingUpdateInput | dict[str, Any]) -> None:
        payload = (
            request
            if isinstance(request, GroupSettingUpdateInput)
            else GroupSettingUpdateInput.model_validate(request)
        )
        await self.group_setting_update(payload.jid, payload.setting)

    async def update_group_member_add_mode(self, request: GroupMemberAddModeInput | dict[str, Any]) -> None:
        payload = (
            request
            if isinstance(request, GroupMemberAddModeInput)
            else GroupMemberAddModeInput.model_validate(request)
        )
        await self.group_member_add_mode(payload.jid, payload.mode)

    async def update_group_join_approval_mode(self, request: GroupJoinApprovalModeInput | dict[str, Any]) -> None:
        payload = (
            request
            if isinstance(request, GroupJoinApprovalModeInput)
            else GroupJoinApprovalModeInput.model_validate(request)
        )
        await self.group_join_approval_mode(payload.jid, payload.mode)

    async def update_group_ephemeral(self, request: GroupToggleEphemeralInput | dict[str, Any]) -> None:
        payload = (
            request
            if isinstance(request, GroupToggleEphemeralInput)
            else GroupToggleEphemeralInput.model_validate(request)
        )
        await self.group_toggle_ephemeral(payload.jid, payload.ephemeral_expiration)

    # camelCase aliases for Baileys parity
    groupQuery = group_query
    groupMetadata = group_metadata
    groupFetchAllParticipating = group_fetch_all_participating
    groupCreate = group_create
    groupLeave = group_leave
    groupUpdateSubject = group_update_subject
    groupRequestParticipantsList = group_request_participants_list
    groupRequestParticipantsUpdate = group_request_participants_update
    groupParticipantsUpdate = group_participants_update
    groupUpdateDescription = group_update_description
    groupInviteCode = group_invite_code
    groupRevokeInvite = group_revoke_invite
    groupAcceptInvite = group_accept_invite
    groupRevokeInviteV4 = group_revoke_invite_v4
    groupAcceptInviteV4 = group_accept_invite_v4
    groupGetInviteInfo = group_get_invite_info
    groupToggleEphemeral = group_toggle_ephemeral
    groupSettingUpdate = group_setting_update
    groupMemberAddMode = group_member_add_mode
    groupJoinApprovalMode = group_join_approval_mode
    createGroup = create_group
    updateGroupParticipants = update_group_participants
    updateGroupRequests = update_group_requests
    updateGroupSubject = update_group_subject
    updateGroupDescription = update_group_description
    updateGroupSetting = update_group_setting
    updateGroupMemberAddMode = update_group_member_add_mode
    updateGroupJoinApprovalMode = update_group_join_approval_mode
    updateGroupEphemeral = update_group_ephemeral
