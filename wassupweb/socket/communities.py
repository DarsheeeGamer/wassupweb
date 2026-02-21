from __future__ import annotations

import json
from typing import Any, Literal

from ..types.community import (
    CommunityAcceptInviteInput,
    CommunityAcceptInviteV4Input,
    CommunityCreateGroupInput,
    CommunityCreateInput,
    CommunityDescriptionUpdateInput,
    CommunityFetchLinkedGroupsInput,
    CommunityInviteCodeInput,
    CommunityInviteInfoInput,
    CommunityJoinApprovalModeInput,
    CommunityLeaveInput,
    CommunityLinkGroupInput,
    CommunityMemberAddModeInput,
    CommunityParticipantsUpdateInput,
    CommunityRequestParticipantsUpdateInput,
    CommunityRevokeInviteInput,
    CommunityRevokeInviteV4Input,
    CommunitySettingUpdateInput,
    CommunitySubjectUpdateInput,
    CommunityToggleEphemeralInput,
    CommunityUnlinkGroupInput,
)
from ..types.group_metadata import GroupMetadata, GroupParticipant
from ..types.message import WAMessageStubType
from ..utils.generics import generate_message_id, generate_message_id_v2, unix_timestamp_seconds
from ..wabinary import BinaryNode
from ..wabinary import (
    get_binary_node_child,
    get_binary_node_child_string,
    get_binary_node_children,
    jid_encode,
    jid_normalized_user,
)
from .business import BusinessSocket


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def extract_community_metadata(result: BinaryNode) -> GroupMetadata:
    community = get_binary_node_child(result, "community")
    if not community:
        raise ValueError("community metadata node missing <community> child")

    desc_child = get_binary_node_child(community, "description")
    desc = get_binary_node_child_string(desc_child, "body") if desc_child else None
    desc_id = desc_child.attrs.get("id") if desc_child else None

    raw_id = community.attrs.get("id") or ""
    community_id = raw_id if "@" in raw_id else jid_encode(raw_id, "g.us")
    eph_node = get_binary_node_child(community, "ephemeral")
    member_add_mode = get_binary_node_child_string(community, "member_add_mode") == "all_member_add"

    participant_nodes = get_binary_node_children(community, "participant")
    participants: list[GroupParticipant] = [
        GroupParticipant(
            id=node.attrs.get("jid", ""),
            admin=node.attrs.get("type"),
        )
        for node in participant_nodes
    ]

    creator = community.attrs.get("creator")
    linked_parent_node = get_binary_node_child(community, "linked_parent")

    metadata = GroupMetadata(
        id=community_id,
        subject=community.attrs.get("subject", ""),
        subjectOwner=community.attrs.get("s_o"),
        subjectTime=_to_int(community.attrs.get("s_t"), 0),
        size=len(participant_nodes),
        creation=_to_int(community.attrs.get("creation"), 0),
        owner=jid_normalized_user(creator) if creator else None,
        desc=desc,
        descId=desc_id,
        linkedParent=linked_parent_node.attrs.get("jid") if linked_parent_node else None,
        restrict=bool(get_binary_node_child(community, "locked")),
        announce=bool(get_binary_node_child(community, "announcement")),
        isCommunity=bool(get_binary_node_child(community, "parent")),
        isCommunityAnnounce=bool(get_binary_node_child(community, "default_sub_community")),
        joinApprovalMode=bool(get_binary_node_child(community, "membership_approval_mode")),
        memberAddMode=member_add_mode,
        participants=participants,
        ephemeralDuration=_to_int(eph_node.attrs.get("expiration"), 0) if eph_node else None,
        addressingMode=get_binary_node_child_string(community, "addressing_mode"),
    )
    return metadata


class CommunitiesSocket(BusinessSocket):
    _community_dirty_handler_attached: bool = False
    _community_accept_invite_v4_buffered: Any = None

    async def connect(self) -> None:
        await super().connect()
        if not self._community_dirty_handler_attached:
            self.ev.on("node:ib", self._handle_ib_dirty)
            self._community_dirty_handler_attached = True

    def _get_community_accept_invite_v4_runner(self) -> Any:
        if self._community_accept_invite_v4_buffered is not None:
            return self._community_accept_invite_v4_buffered

        runner: Any = self._community_accept_invite_v4_impl
        maker = getattr(self.ev, "create_buffered_function", None)
        if not callable(maker):
            maker = getattr(self.ev, "createBufferedFunction", None)
        if callable(maker):
            runner = maker(runner)

        self._community_accept_invite_v4_buffered = runner
        return runner

    async def _handle_ib_dirty(self, node: BinaryNode) -> None:
        dirty = get_binary_node_child(node, "dirty")
        if not dirty:
            return
        if dirty.attrs.get("type") != "communities":
            return
        await self.community_fetch_all_participating()
        await self.clean_dirty_bits("groups")

    async def community_query(self, jid: str, type: Literal["get", "set"], content: list[BinaryNode]) -> BinaryNode:
        return await self.query_node(
            BinaryNode(
                tag="iq",
                attrs={"type": type, "xmlns": "w:g2", "to": jid},
                content=content,
            )
        )

    async def community_metadata(self, jid: str) -> GroupMetadata:
        resolved_jid = self.resolve_chat_jid(jid)
        result = await self.community_query(
            resolved_jid,
            "get",
            [BinaryNode(tag="query", attrs={"request": "interactive"})],
        )
        return extract_community_metadata(result)

    async def community_fetch_all_participating(self) -> dict[str, dict[str, Any]]:
        result = await self.query_node(
            BinaryNode(
                tag="iq",
                attrs={"to": "@g.us", "xmlns": "w:g2", "type": "get"},
                content=[
                    BinaryNode(
                        tag="participating",
                        attrs={},
                        content=[
                            BinaryNode(tag="participants", attrs={}),
                            BinaryNode(tag="description", attrs={}),
                        ],
                    )
                ],
            )
        )

        data: dict[str, dict[str, Any]] = {}
        communities_child = get_binary_node_child(result, "communities")
        if communities_child:
            communities = get_binary_node_children(communities_child, "community")
            for community_node in communities:
                meta = extract_community_metadata(
                    BinaryNode(tag="result", attrs={}, content=[community_node]),
                )
                data[meta.id] = meta.model_dump(by_alias=True, exclude_none=True)

        await self.ev.emit("groups.update", list(data.values()))
        return data

    async def _parse_group_result(self, node: BinaryNode) -> dict[str, Any] | None:
        group_node = get_binary_node_child(node, "group")
        if not group_node:
            return None

        group_id = group_node.attrs.get("id")
        if not group_id:
            return None
        group_jid = group_id if "@" in group_id else f"{group_id}@g.us"

        try:
            metadata = await self.group_metadata(group_jid)
            return metadata.model_dump(by_alias=True, exclude_none=True)
        except Exception:
            return None

    async def community_create(self, subject: str, body: str) -> dict[str, Any] | None:
        description_id = generate_message_id()[:12]
        result = await self.community_query(
            "@g.us",
            "set",
            [
                BinaryNode(
                    tag="create",
                    attrs={"subject": subject},
                    content=[
                        BinaryNode(
                            tag="description",
                            attrs={"id": description_id},
                            content=[
                                BinaryNode(
                                    tag="body",
                                    attrs={},
                                    content=(body or "").encode("utf-8"),
                                )
                            ],
                        ),
                        BinaryNode(
                            tag="parent",
                            attrs={"default_membership_approval_mode": "request_required"},
                        ),
                        BinaryNode(tag="allow_non_admin_sub_group_creation", attrs={}),
                        BinaryNode(tag="create_general_chat", attrs={}),
                    ],
                )
            ],
        )
        return await self._parse_group_result(result)

    async def community_create_group(self, subject: str, participants: list[str], parent_community_jid: str) -> dict[str, Any] | None:
        key = generate_message_id_v2()
        parent = self.resolve_chat_jid(parent_community_jid)
        result = await self.community_query(
            "@g.us",
            "set",
            [
                BinaryNode(
                    tag="create",
                    attrs={"subject": subject, "key": key},
                    content=[
                        *[
                            BinaryNode(tag="participant", attrs={"jid": self.resolve_chat_jid(jid)})
                            for jid in participants
                        ],
                        BinaryNode(tag="linked_parent", attrs={"jid": parent}),
                    ],
                )
            ],
        )
        return await self._parse_group_result(result)

    async def community_leave(self, id: str) -> None:
        await self.community_query(
            "@g.us",
            "set",
            [
                BinaryNode(
                    tag="leave",
                    attrs={},
                    content=[BinaryNode(tag="community", attrs={"id": id})],
                )
            ],
        )

    async def community_update_subject(self, jid: str, subject: str) -> None:
        resolved_jid = self.resolve_chat_jid(jid)
        await self.community_query(
            resolved_jid,
            "set",
            [BinaryNode(tag="subject", attrs={}, content=subject.encode("utf-8"))],
        )

    async def community_link_group(self, group_jid: str, parent_community_jid: str) -> None:
        resolved_group = self.resolve_chat_jid(group_jid)
        resolved_parent = self.resolve_chat_jid(parent_community_jid)
        await self.community_query(
            resolved_parent,
            "set",
            [
                BinaryNode(
                    tag="links",
                    attrs={},
                    content=[
                        BinaryNode(
                            tag="link",
                            attrs={"link_type": "sub_group"},
                            content=[BinaryNode(tag="group", attrs={"jid": resolved_group})],
                        )
                    ],
                )
            ],
        )

    async def community_unlink_group(self, group_jid: str, parent_community_jid: str) -> None:
        resolved_group = self.resolve_chat_jid(group_jid)
        resolved_parent = self.resolve_chat_jid(parent_community_jid)
        await self.community_query(
            resolved_parent,
            "set",
            [
                BinaryNode(
                    tag="unlink",
                    attrs={"unlink_type": "sub_group"},
                    content=[BinaryNode(tag="group", attrs={"jid": resolved_group})],
                )
            ],
        )

    async def community_fetch_linked_groups(self, jid: str) -> dict[str, Any]:
        community_jid = self.resolve_chat_jid(jid)
        is_community = False

        metadata = await self.group_metadata(community_jid)
        if metadata.linked_parent:
            community_jid = metadata.linked_parent
        else:
            is_community = True

        result = await self.community_query(
            community_jid,
            "get",
            [BinaryNode(tag="sub_groups", attrs={})],
        )

        linked_groups_data: list[dict[str, Any]] = []
        sub_groups_node = get_binary_node_child(result, "sub_groups")
        if sub_groups_node:
            group_nodes = get_binary_node_children(sub_groups_node, "group")
            for group_node in group_nodes:
                gid = group_node.attrs.get("id")
                creator = group_node.attrs.get("creator")
                linked_groups_data.append(
                    {
                        "id": jid_encode(gid, "g.us") if gid else None,
                        "subject": group_node.attrs.get("subject", ""),
                        "creation": _to_int(group_node.attrs.get("creation")) if group_node.attrs.get("creation") else None,
                        "owner": jid_normalized_user(creator) if creator else None,
                        "size": _to_int(group_node.attrs.get("size")) if group_node.attrs.get("size") else None,
                    }
                )

        return {
            "communityJid": community_jid,
            "isCommunity": is_community,
            "linkedGroups": linked_groups_data,
        }

    async def community_request_participants_list(self, jid: str) -> list[dict[str, str]]:
        resolved_jid = self.resolve_chat_jid(jid)
        result = await self.community_query(
            resolved_jid,
            "get",
            [BinaryNode(tag="membership_approval_requests", attrs={})],
        )
        node = get_binary_node_child(result, "membership_approval_requests")
        participants = get_binary_node_children(node, "membership_approval_request")
        return [dict(v.attrs) for v in participants]

    async def community_request_participants_update(
        self,
        jid: str,
        participants: list[str],
        action: Literal["approve", "reject"],
    ) -> list[dict[str, str]]:
        resolved_jid = self.resolve_chat_jid(jid)
        result = await self.community_query(
            resolved_jid,
            "set",
            [
                BinaryNode(
                    tag="membership_requests_action",
                    attrs={},
                    content=[
                        BinaryNode(
                            tag=action,
                            attrs={},
                            content=[
                                BinaryNode(tag="participant", attrs={"jid": self.resolve_chat_jid(item)})
                                for item in participants
                            ],
                        )
                    ],
                )
            ],
        )
        node = get_binary_node_child(result, "membership_requests_action")
        node_action = get_binary_node_child(node, action)
        participants_affected = get_binary_node_children(node_action, "participant")
        return [{"status": p.attrs.get("error", "200"), "jid": p.attrs.get("jid", "")} for p in participants_affected]

    async def community_participants_update(self, jid: str, participants: list[str], action: str) -> list[dict[str, Any]]:
        resolved_jid = self.resolve_chat_jid(jid)
        result = await self.community_query(
            resolved_jid,
            "set",
            [
                BinaryNode(
                    tag=action,
                    attrs={"linked_groups": "true"} if action == "remove" else {},
                    content=[
                        BinaryNode(tag="participant", attrs={"jid": self.resolve_chat_jid(item)})
                        for item in participants
                    ],
                )
            ],
        )
        node = get_binary_node_child(result, action)
        participants_affected = get_binary_node_children(node, "participant")
        return [
            {
                "status": p.attrs.get("error", "200"),
                "jid": p.attrs.get("jid", ""),
                "content": p,
            }
            for p in participants_affected
        ]

    async def community_update_description(self, jid: str, description: str | None = None) -> None:
        resolved_jid = self.resolve_chat_jid(jid)
        metadata = await self.community_metadata(resolved_jid)
        prev = metadata.desc_id

        attrs: dict[str, str] = {}
        if description:
            attrs["id"] = generate_message_id()
        else:
            attrs["delete"] = "true"
        if prev:
            attrs["prev"] = prev

        await self.community_query(
            resolved_jid,
            "set",
            [
                BinaryNode(
                    tag="description",
                    attrs=attrs,
                    content=(
                        [BinaryNode(tag="body", attrs={}, content=description.encode("utf-8"))]
                        if description
                        else None
                    ),
                )
            ],
        )

    async def community_invite_code(self, jid: str) -> str | None:
        resolved_jid = self.resolve_chat_jid(jid)
        result = await self.community_query(
            resolved_jid,
            "get",
            [BinaryNode(tag="invite", attrs={})],
        )
        invite_node = get_binary_node_child(result, "invite")
        return invite_node.attrs.get("code") if invite_node else None

    async def community_revoke_invite(self, jid: str) -> str | None:
        resolved_jid = self.resolve_chat_jid(jid)
        result = await self.community_query(
            resolved_jid,
            "set",
            [BinaryNode(tag="invite", attrs={})],
        )
        invite_node = get_binary_node_child(result, "invite")
        return invite_node.attrs.get("code") if invite_node else None

    async def community_accept_invite(self, code: str) -> str | None:
        results = await self.community_query(
            "@g.us",
            "set",
            [BinaryNode(tag="invite", attrs={"code": code})],
        )
        result = get_binary_node_child(results, "community")
        return result.attrs.get("jid") if result else None

    async def community_revoke_invite_v4(self, community_jid: str, invited_jid: str) -> bool:
        resolved_community = self.resolve_chat_jid(community_jid)
        resolved_invited = self.resolve_chat_jid(invited_jid)
        result = await self.community_query(
            resolved_community,
            "set",
            [
                BinaryNode(
                    tag="revoke",
                    attrs={},
                    content=[BinaryNode(tag="participant", attrs={"jid": resolved_invited})],
                )
            ],
        )
        return bool(result)

    async def community_accept_invite_v4(
        self,
        key: str | dict[str, Any],
        invite_message: dict[str, Any],
    ) -> str | None:
        runner = self._get_community_accept_invite_v4_runner()
        return await runner(key, invite_message)

    async def _community_accept_invite_v4_impl(
        self,
        key: str | dict[str, Any],
        invite_message: dict[str, Any],
    ) -> str | None:
        key_obj = {"remoteJid": key} if isinstance(key, str) else dict(key)

        group_jid = invite_message.get("groupJid") or invite_message.get("group_jid")
        invite_code = invite_message.get("inviteCode") or invite_message.get("invite_code")
        invite_expiration = invite_message.get("inviteExpiration") or invite_message.get("invite_expiration")
        if not group_jid or not invite_code or invite_expiration is None:
            raise ValueError("invite_message must include groupJid/inviteCode/inviteExpiration")

        results = await self.community_query(
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
            await self.ev.emit(
                "messages.update",
                [
                    {
                        "key": key_obj,
                        "update": {"message": {"groupInviteMessage": expired_invite}},
                    }
                ],
            )

        auth = getattr(self.config, "auth", None)
        me = getattr(getattr(auth, "creds", None), "me", None) if auth else None
        me_payload = me.model_dump(by_alias=True, exclude_none=True) if hasattr(me, "model_dump") else (me or {})
        user = getattr(self, "user", None)
        user_id = user.get("id") if isinstance(user, dict) else getattr(user, "id", None)
        generated_stub_message = {
            "key": {
                "remoteJid": group_jid,
                "id": generate_message_id_v2(user_id),
                "fromMe": False,
                "participant": key_obj.get("remoteJid"),
            },
            "messageStubType": int(WAMessageStubType.GROUP_PARTICIPANT_ADD),
            "messageStubParameters": [json.dumps(me_payload)],
            "participant": key_obj.get("remoteJid"),
            "messageTimestamp": unix_timestamp_seconds(),
        }

        upsert_message = getattr(self, "upsert_message", None)
        if callable(upsert_message):
            await upsert_message(generated_stub_message, "notify")
        else:
            await self.ev.emit(
                "messages.upsert",
                {
                    "messages": [generated_stub_message],
                    "type": "notify",
                },
            )

        return results.attrs.get("from")

    async def community_get_invite_info(self, code: str) -> dict[str, Any]:
        results = await self.community_query(
            "@g.us",
            "get",
            [BinaryNode(tag="invite", attrs={"code": code})],
        )
        return extract_community_metadata(results).model_dump(by_alias=True, exclude_none=True)

    async def community_toggle_ephemeral(self, jid: str, ephemeral_expiration: int) -> None:
        resolved_jid = self.resolve_chat_jid(jid)
        content = (
            BinaryNode(tag="ephemeral", attrs={"expiration": str(ephemeral_expiration)})
            if ephemeral_expiration
            else BinaryNode(tag="not_ephemeral", attrs={})
        )
        await self.community_query(resolved_jid, "set", [content])

    async def community_setting_update(
        self,
        jid: str,
        setting: Literal["announcement", "not_announcement", "locked", "unlocked"],
    ) -> None:
        resolved_jid = self.resolve_chat_jid(jid)
        await self.community_query(
            resolved_jid,
            "set",
            [BinaryNode(tag=setting, attrs={})],
        )

    async def community_member_add_mode(self, jid: str, mode: Literal["admin_add", "all_member_add"]) -> None:
        resolved_jid = self.resolve_chat_jid(jid)
        await self.community_query(
            resolved_jid,
            "set",
            [BinaryNode(tag="member_add_mode", attrs={}, content=mode)],
        )

    async def community_join_approval_mode(self, jid: str, mode: Literal["on", "off"]) -> None:
        resolved_jid = self.resolve_chat_jid(jid)
        await self.community_query(
            resolved_jid,
            "set",
            [
                BinaryNode(
                    tag="membership_approval_mode",
                    attrs={},
                    content=[BinaryNode(tag="community_join", attrs={"state": mode})],
                )
            ],
        )

    async def get_communities(self) -> BinaryNode:
        node = BinaryNode(
            tag="iq",
            attrs={"to": "s.whatsapp.net", "type": "get", "xmlns": "w:g2"},
            content=[BinaryNode(tag="communities", attrs={"query": "all"})],
        )
        return await self.query_node(node)

    async def link_group_to_community(self, community_jid: str, group_jid: str) -> BinaryNode:
        resolved_community = self.resolve_chat_jid(community_jid)
        resolved_group = self.resolve_chat_jid(group_jid)
        node = BinaryNode(
            tag="iq",
            attrs={"to": resolved_community, "type": "set", "xmlns": "w:g2"},
            content=[BinaryNode(tag="link_group", attrs={"jid": resolved_group})],
        )
        return await self.query_node(node)

    # typed convenience interfaces
    async def create_community(self, request: CommunityCreateInput | dict[str, Any]) -> dict[str, Any] | None:
        payload = request if isinstance(request, CommunityCreateInput) else CommunityCreateInput.model_validate(request)
        return await self.community_create(payload.subject, payload.body)

    async def create_community_group(self, request: CommunityCreateGroupInput | dict[str, Any]) -> dict[str, Any] | None:
        payload = (
            request if isinstance(request, CommunityCreateGroupInput) else CommunityCreateGroupInput.model_validate(request)
        )
        return await self.community_create_group(payload.subject, payload.participants, payload.parent_community_jid)

    async def leave_community(self, request: CommunityLeaveInput | dict[str, Any]) -> None:
        payload = request if isinstance(request, CommunityLeaveInput) else CommunityLeaveInput.model_validate(request)
        await self.community_leave(payload.id)

    async def update_community_subject(self, request: CommunitySubjectUpdateInput | dict[str, Any]) -> None:
        payload = (
            request if isinstance(request, CommunitySubjectUpdateInput) else CommunitySubjectUpdateInput.model_validate(request)
        )
        await self.community_update_subject(payload.jid, payload.subject)

    async def link_community_group(self, request: CommunityLinkGroupInput | dict[str, Any]) -> None:
        payload = request if isinstance(request, CommunityLinkGroupInput) else CommunityLinkGroupInput.model_validate(request)
        await self.community_link_group(payload.group_jid, payload.parent_community_jid)

    async def unlink_community_group(self, request: CommunityUnlinkGroupInput | dict[str, Any]) -> None:
        payload = (
            request if isinstance(request, CommunityUnlinkGroupInput) else CommunityUnlinkGroupInput.model_validate(request)
        )
        await self.community_unlink_group(payload.group_jid, payload.parent_community_jid)

    async def fetch_community_linked_groups(
        self, request: CommunityFetchLinkedGroupsInput | dict[str, Any]
    ) -> dict[str, Any]:
        payload = (
            request
            if isinstance(request, CommunityFetchLinkedGroupsInput)
            else CommunityFetchLinkedGroupsInput.model_validate(request)
        )
        return await self.community_fetch_linked_groups(payload.jid)

    async def update_community_requests(
        self, request: CommunityRequestParticipantsUpdateInput | dict[str, Any]
    ) -> list[dict[str, str]]:
        payload = (
            request
            if isinstance(request, CommunityRequestParticipantsUpdateInput)
            else CommunityRequestParticipantsUpdateInput.model_validate(request)
        )
        return await self.community_request_participants_update(payload.jid, payload.participants, payload.action)

    async def update_community_participants(
        self, request: CommunityParticipantsUpdateInput | dict[str, Any]
    ) -> list[dict[str, Any]]:
        payload = (
            request
            if isinstance(request, CommunityParticipantsUpdateInput)
            else CommunityParticipantsUpdateInput.model_validate(request)
        )
        return await self.community_participants_update(payload.jid, payload.participants, payload.action)

    async def update_community_description(self, request: CommunityDescriptionUpdateInput | dict[str, Any]) -> None:
        payload = (
            request
            if isinstance(request, CommunityDescriptionUpdateInput)
            else CommunityDescriptionUpdateInput.model_validate(request)
        )
        await self.community_update_description(payload.jid, payload.description)

    async def get_community_invite_code(self, request: CommunityInviteCodeInput | dict[str, Any]) -> str | None:
        payload = request if isinstance(request, CommunityInviteCodeInput) else CommunityInviteCodeInput.model_validate(request)
        return await self.community_invite_code(payload.jid)

    async def revoke_community_invite(self, request: CommunityRevokeInviteInput | dict[str, Any]) -> str | None:
        payload = (
            request if isinstance(request, CommunityRevokeInviteInput) else CommunityRevokeInviteInput.model_validate(request)
        )
        return await self.community_revoke_invite(payload.jid)

    async def accept_community_invite(self, request: CommunityAcceptInviteInput | dict[str, Any]) -> str | None:
        payload = request if isinstance(request, CommunityAcceptInviteInput) else CommunityAcceptInviteInput.model_validate(request)
        return await self.community_accept_invite(payload.code)

    async def revoke_community_invite_v4(self, request: CommunityRevokeInviteV4Input | dict[str, Any]) -> bool:
        payload = (
            request if isinstance(request, CommunityRevokeInviteV4Input) else CommunityRevokeInviteV4Input.model_validate(request)
        )
        return await self.community_revoke_invite_v4(payload.community_jid, payload.invited_jid)

    async def accept_community_invite_v4(self, request: CommunityAcceptInviteV4Input | dict[str, Any]) -> str | None:
        payload = (
            request if isinstance(request, CommunityAcceptInviteV4Input) else CommunityAcceptInviteV4Input.model_validate(request)
        )
        return await self.community_accept_invite_v4(payload.key, payload.invite_message)

    async def get_community_invite_info(self, request: CommunityInviteInfoInput | dict[str, Any]) -> dict[str, Any]:
        payload = request if isinstance(request, CommunityInviteInfoInput) else CommunityInviteInfoInput.model_validate(request)
        return await self.community_get_invite_info(payload.code)

    async def update_community_ephemeral(self, request: CommunityToggleEphemeralInput | dict[str, Any]) -> None:
        payload = (
            request if isinstance(request, CommunityToggleEphemeralInput) else CommunityToggleEphemeralInput.model_validate(request)
        )
        await self.community_toggle_ephemeral(payload.jid, payload.ephemeral_expiration)

    async def update_community_setting(self, request: CommunitySettingUpdateInput | dict[str, Any]) -> None:
        payload = (
            request if isinstance(request, CommunitySettingUpdateInput) else CommunitySettingUpdateInput.model_validate(request)
        )
        await self.community_setting_update(payload.jid, payload.setting)

    async def update_community_member_add_mode(
        self, request: CommunityMemberAddModeInput | dict[str, Any]
    ) -> None:
        payload = (
            request if isinstance(request, CommunityMemberAddModeInput) else CommunityMemberAddModeInput.model_validate(request)
        )
        await self.community_member_add_mode(payload.jid, payload.mode)

    async def update_community_join_approval_mode(
        self, request: CommunityJoinApprovalModeInput | dict[str, Any]
    ) -> None:
        payload = (
            request
            if isinstance(request, CommunityJoinApprovalModeInput)
            else CommunityJoinApprovalModeInput.model_validate(request)
        )
        await self.community_join_approval_mode(payload.jid, payload.mode)

    # camelCase aliases for Baileys parity
    communityQuery = community_query
    communityMetadata = community_metadata
    communityFetchAllParticipating = community_fetch_all_participating
    communityCreate = community_create
    communityCreateGroup = community_create_group
    communityLeave = community_leave
    communityUpdateSubject = community_update_subject
    communityLinkGroup = community_link_group
    communityUnlinkGroup = community_unlink_group
    communityFetchLinkedGroups = community_fetch_linked_groups
    communityRequestParticipantsList = community_request_participants_list
    communityRequestParticipantsUpdate = community_request_participants_update
    communityParticipantsUpdate = community_participants_update
    communityUpdateDescription = community_update_description
    communityInviteCode = community_invite_code
    communityRevokeInvite = community_revoke_invite
    communityAcceptInvite = community_accept_invite
    communityRevokeInviteV4 = community_revoke_invite_v4
    communityAcceptInviteV4 = community_accept_invite_v4
    communityGetInviteInfo = community_get_invite_info
    communityToggleEphemeral = community_toggle_ephemeral
    communitySettingUpdate = community_setting_update
    communityMemberAddMode = community_member_add_mode
    communityJoinApprovalMode = community_join_approval_mode
    createCommunity = create_community
    createCommunityGroup = create_community_group
    leaveCommunity = leave_community
    updateCommunitySubject = update_community_subject
    linkCommunityGroup = link_community_group
    unlinkCommunityGroup = unlink_community_group
    fetchCommunityLinkedGroups = fetch_community_linked_groups
    updateCommunityRequests = update_community_requests
    updateCommunityParticipants = update_community_participants
    updateCommunityDescription = update_community_description
    getCommunityInviteCode = get_community_invite_code
    revokeCommunityInvite = revoke_community_invite
    acceptCommunityInvite = accept_community_invite
    revokeCommunityInviteV4 = revoke_community_invite_v4
    acceptCommunityInviteV4 = accept_community_invite_v4
    getCommunityInviteInfo = get_community_invite_info
    updateCommunityEphemeral = update_community_ephemeral
    updateCommunitySetting = update_community_setting
    updateCommunityMemberAddMode = update_community_member_add_mode
    updateCommunityJoinApprovalMode = update_community_join_approval_mode
