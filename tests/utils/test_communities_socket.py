from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from wassupweb.socket.communities import CommunitiesSocket
from wassupweb.types.group_metadata import GroupMetadata
from wassupweb.types.message import WAMessageStubType
from wassupweb.wabinary import BinaryNode


class _EventBus:
    def __init__(self) -> None:
        self.events: list[tuple[str, Any]] = []

    async def emit(self, event: str, payload: Any) -> None:
        self.events.append((event, payload))


@pytest.mark.asyncio
async def test_parse_group_result_uses_group_metadata() -> None:
    class _Harness:
        def __init__(self) -> None:
            self.calls: list[str] = []

        async def group_metadata(self, jid: str) -> GroupMetadata:
            self.calls.append(jid)
            return GroupMetadata(id=jid, subject="G", participants=[])

        async def community_metadata(self, _jid: str) -> GroupMetadata:
            raise AssertionError("community_metadata should not be used here")

    obj = _Harness()
    result = await CommunitiesSocket._parse_group_result(
        obj,  # type: ignore[arg-type]
        BinaryNode(tag="iq", attrs={}, content=[BinaryNode(tag="group", attrs={"id": "123456"})]),
    )

    assert obj.calls == ["123456@g.us"]
    assert result is not None
    assert result["id"] == "123456@g.us"


@pytest.mark.asyncio
async def test_community_fetch_linked_groups_uses_group_metadata_parent_resolution() -> None:
    class _Harness:
        def __init__(self) -> None:
            self.query_calls: list[tuple[str, str]] = []
            self.metadata_calls: list[str] = []

        def resolve_chat_jid(self, jid: str) -> str:
            return jid

        async def group_metadata(self, jid: str) -> GroupMetadata:
            self.metadata_calls.append(jid)
            return GroupMetadata(id=jid, subject="S", linkedParent="parent@g.us", participants=[])

        async def community_query(self, jid: str, type: str, _content: list[BinaryNode]) -> BinaryNode:
            self.query_calls.append((jid, type))
            return BinaryNode(
                tag="iq",
                attrs={"type": "result"},
                content=[
                    BinaryNode(
                        tag="sub_groups",
                        attrs={},
                        content=[BinaryNode(tag="group", attrs={"id": "111111", "subject": "Child", "creation": "2", "size": "3"})],
                    )
                ],
            )

    obj = _Harness()
    data = await CommunitiesSocket.community_fetch_linked_groups(obj, "subgroup@g.us")  # type: ignore[arg-type]

    assert obj.metadata_calls == ["subgroup@g.us"]
    assert obj.query_calls == [("parent@g.us", "get")]
    assert data["communityJid"] == "parent@g.us"
    assert data["isCommunity"] is False
    assert data["linkedGroups"][0]["id"] == "111111@g.us"
    assert data["linkedGroups"][0]["subject"] == "Child"


@pytest.mark.asyncio
async def test_community_accept_invite_v4_prefers_upsert_message_method() -> None:
    class _Harness:
        def __init__(self) -> None:
            self.ev = _EventBus()
            self.config = SimpleNamespace(auth=SimpleNamespace(creds=SimpleNamespace(me={"id": "me@s.whatsapp.net"})))
            self.user = {"id": "me@s.whatsapp.net"}
            self.upserts: list[tuple[dict[str, Any], str]] = []
            self.query_nodes: list[BinaryNode] = []
            self._community_accept_invite_v4_buffered = None
            self._get_community_accept_invite_v4_runner = CommunitiesSocket._get_community_accept_invite_v4_runner.__get__(self, _Harness)
            self._community_accept_invite_v4_impl = CommunitiesSocket._community_accept_invite_v4_impl.__get__(self, _Harness)

        async def community_query(self, jid: str, type: str, content: list[BinaryNode]) -> BinaryNode:
            self.query_nodes.append(BinaryNode(tag="trace", attrs={"jid": jid, "type": type}, content=content))
            return BinaryNode(tag="iq", attrs={"type": "result", "from": "group@g.us"}, content=[])

        async def upsert_message(self, msg: dict[str, Any], upsert_type: str) -> None:
            self.upserts.append((msg, upsert_type))

    obj = _Harness()
    result = await CommunitiesSocket.community_accept_invite_v4(  # type: ignore[arg-type]
        obj,
        {"remoteJid": "admin@s.whatsapp.net", "id": "msg-1"},
        {"groupJid": "group@g.us", "inviteCode": "abc", "inviteExpiration": 123},
    )

    assert result == "group@g.us"
    assert obj.upserts and obj.upserts[0][1] == "notify"
    stub_msg = obj.upserts[0][0]
    assert stub_msg["messageStubType"] == int(WAMessageStubType.GROUP_PARTICIPANT_ADD)
    assert stub_msg["key"]["remoteJid"] == "group@g.us"
    assert stub_msg["key"]["participant"] == "admin@s.whatsapp.net"
    assert any(event == "messages.update" for event, _ in obj.ev.events)
    assert all(event != "messages.upsert" for event, _ in obj.ev.events)


@pytest.mark.asyncio
async def test_community_accept_invite_v4_uses_buffered_runner_once() -> None:
    class _BufferedEventBus(_EventBus):
        def __init__(self) -> None:
            super().__init__()
            self.created = 0
            self.called = 0

        def create_buffered_function(self, fn: Any) -> Any:
            self.created += 1

            async def _runner(*args: Any, **kwargs: Any) -> Any:
                self.called += 1
                return await fn(*args, **kwargs)

            return _runner

    class _Harness:
        def __init__(self) -> None:
            self.ev = _BufferedEventBus()
            self.config = SimpleNamespace(auth=SimpleNamespace(creds=SimpleNamespace(me={"id": "me@s.whatsapp.net"})))
            self.user = {"id": "me@s.whatsapp.net"}
            self.upserts: list[tuple[dict[str, Any], str]] = []
            self._community_accept_invite_v4_buffered = None
            self._get_community_accept_invite_v4_runner = CommunitiesSocket._get_community_accept_invite_v4_runner.__get__(self, _Harness)
            self._community_accept_invite_v4_impl = CommunitiesSocket._community_accept_invite_v4_impl.__get__(self, _Harness)

        async def community_query(self, _jid: str, _type: str, _content: list[BinaryNode]) -> BinaryNode:
            return BinaryNode(tag="iq", attrs={"type": "result", "from": "group@g.us"}, content=[])

        async def upsert_message(self, msg: dict[str, Any], upsert_type: str) -> None:
            self.upserts.append((msg, upsert_type))

    obj = _Harness()
    await CommunitiesSocket.community_accept_invite_v4(  # type: ignore[arg-type]
        obj,
        "admin@s.whatsapp.net",
        {"groupJid": "group@g.us", "inviteCode": "abc", "inviteExpiration": 123},
    )
    await CommunitiesSocket.community_accept_invite_v4(  # type: ignore[arg-type]
        obj,
        "admin@s.whatsapp.net",
        {"groupJid": "group@g.us", "inviteCode": "abc", "inviteExpiration": 123},
    )

    assert obj.ev.created == 1
    assert obj.ev.called == 2


@pytest.mark.asyncio
async def test_community_typed_create_and_link_interfaces() -> None:
    class _Harness:
        async def community_create(self, subject: str, body: str) -> dict[str, Any]:
            return {"subject": subject, "body": body}

        async def community_create_group(
            self, subject: str, participants: list[str], parent_community_jid: str
        ) -> dict[str, Any]:
            return {"subject": subject, "participants": participants, "parent": parent_community_jid}

        async def community_link_group(self, group_jid: str, parent_community_jid: str) -> None:
            self.linked = (group_jid, parent_community_jid)  # type: ignore[attr-defined]

        async def community_unlink_group(self, group_jid: str, parent_community_jid: str) -> None:
            self.unlinked = (group_jid, parent_community_jid)  # type: ignore[attr-defined]

    obj = _Harness()
    created = await CommunitiesSocket.create_community(  # type: ignore[arg-type]
        obj,
        {"subject": "My Community", "body": "hello"},
    )
    created_group = await CommunitiesSocket.create_community_group(  # type: ignore[arg-type]
        obj,
        {"subject": "Sub", "participants": ["a@s.whatsapp.net"], "parentCommunityJid": "c@g.us"},
    )
    await CommunitiesSocket.link_community_group(  # type: ignore[arg-type]
        obj,
        {"groupJid": "g@g.us", "parentCommunityJid": "c@g.us"},
    )
    await CommunitiesSocket.unlink_community_group(  # type: ignore[arg-type]
        obj,
        {"groupJid": "g@g.us", "parentCommunityJid": "c@g.us"},
    )

    assert created == {"subject": "My Community", "body": "hello"}
    assert created_group == {"subject": "Sub", "participants": ["a@s.whatsapp.net"], "parent": "c@g.us"}
    assert obj.linked == ("g@g.us", "c@g.us")  # type: ignore[attr-defined]
    assert obj.unlinked == ("g@g.us", "c@g.us")  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_community_typed_update_and_invite_interfaces() -> None:
    class _Harness:
        def __init__(self) -> None:
            self.calls: list[tuple[str, Any]] = []

        async def community_leave(self, id: str) -> None:
            self.calls.append(("leave", id))

        async def community_update_subject(self, jid: str, subject: str) -> None:
            self.calls.append(("subject", (jid, subject)))

        async def community_fetch_linked_groups(self, jid: str) -> dict[str, Any]:
            self.calls.append(("fetch_linked", jid))
            return {"communityJid": jid, "linkedGroups": []}

        async def community_request_participants_update(self, jid: str, participants: list[str], action: str) -> list[dict[str, str]]:
            self.calls.append(("req_update", (jid, participants, action)))
            return [{"jid": jid, "status": "200"}]

        async def community_participants_update(self, jid: str, participants: list[str], action: str) -> list[dict[str, Any]]:
            self.calls.append(("part_update", (jid, participants, action)))
            return [{"jid": jid, "status": "200"}]

        async def community_update_description(self, jid: str, description: str | None = None) -> None:
            self.calls.append(("desc", (jid, description)))

        async def community_invite_code(self, jid: str) -> str | None:
            self.calls.append(("invite_code", jid))
            return "abc"

        async def community_revoke_invite(self, jid: str) -> str | None:
            self.calls.append(("revoke_invite", jid))
            return "new"

        async def community_accept_invite(self, code: str) -> str | None:
            self.calls.append(("accept_invite", code))
            return "c@g.us"

        async def community_revoke_invite_v4(self, community_jid: str, invited_jid: str) -> bool:
            self.calls.append(("revoke_invite_v4", (community_jid, invited_jid)))
            return True

        async def community_accept_invite_v4(self, key: str | dict[str, Any], invite_message: dict[str, Any]) -> str | None:
            self.calls.append(("accept_invite_v4", (key, invite_message)))
            return "c@g.us"

        async def community_get_invite_info(self, code: str) -> dict[str, Any]:
            self.calls.append(("invite_info", code))
            return {"id": "c@g.us"}

        async def community_toggle_ephemeral(self, jid: str, ephemeral_expiration: int) -> None:
            self.calls.append(("ephemeral", (jid, ephemeral_expiration)))

        async def community_setting_update(self, jid: str, setting: str) -> None:
            self.calls.append(("setting", (jid, setting)))

        async def community_member_add_mode(self, jid: str, mode: str) -> None:
            self.calls.append(("member_add", (jid, mode)))

        async def community_join_approval_mode(self, jid: str, mode: str) -> None:
            self.calls.append(("join_approval", (jid, mode)))

    obj = _Harness()
    await CommunitiesSocket.leave_community(obj, {"id": "c@g.us"})  # type: ignore[arg-type]
    await CommunitiesSocket.update_community_subject(obj, {"jid": "c@g.us", "subject": "S"})  # type: ignore[arg-type]
    linked = await CommunitiesSocket.fetch_community_linked_groups(obj, {"jid": "c@g.us"})  # type: ignore[arg-type]
    await CommunitiesSocket.update_community_requests(  # type: ignore[arg-type]
        obj, {"jid": "c@g.us", "participants": ["a@s.whatsapp.net"], "action": "approve"}
    )
    await CommunitiesSocket.update_community_participants(  # type: ignore[arg-type]
        obj, {"jid": "c@g.us", "participants": ["a@s.whatsapp.net"], "action": "remove"}
    )
    await CommunitiesSocket.update_community_description(obj, {"jid": "c@g.us", "description": "D"})  # type: ignore[arg-type]
    code = await CommunitiesSocket.get_community_invite_code(obj, {"jid": "c@g.us"})  # type: ignore[arg-type]
    revoked = await CommunitiesSocket.revoke_community_invite(obj, {"jid": "c@g.us"})  # type: ignore[arg-type]
    accepted = await CommunitiesSocket.accept_community_invite(obj, {"code": "abc"})  # type: ignore[arg-type]
    revoked_v4 = await CommunitiesSocket.revoke_community_invite_v4(  # type: ignore[arg-type]
        obj, {"communityJid": "c@g.us", "invitedJid": "a@s.whatsapp.net"}
    )
    accepted_v4 = await CommunitiesSocket.accept_community_invite_v4(  # type: ignore[arg-type]
        obj,
        {
            "key": "a@s.whatsapp.net",
            "inviteMessage": {"groupJid": "c@g.us", "inviteCode": "abc", "inviteExpiration": 123},
        },
    )
    info = await CommunitiesSocket.get_community_invite_info(obj, {"code": "abc"})  # type: ignore[arg-type]
    await CommunitiesSocket.update_community_ephemeral(  # type: ignore[arg-type]
        obj, {"jid": "c@g.us", "ephemeralExpiration": 3600}
    )
    await CommunitiesSocket.update_community_setting(obj, {"jid": "c@g.us", "setting": "locked"})  # type: ignore[arg-type]
    await CommunitiesSocket.update_community_member_add_mode(  # type: ignore[arg-type]
        obj, {"jid": "c@g.us", "mode": "admin_add"}
    )
    await CommunitiesSocket.update_community_join_approval_mode(  # type: ignore[arg-type]
        obj, {"jid": "c@g.us", "mode": "on"}
    )

    assert linked == {"communityJid": "c@g.us", "linkedGroups": []}
    assert code == "abc"
    assert revoked == "new"
    assert accepted == "c@g.us"
    assert revoked_v4 is True
    assert accepted_v4 == "c@g.us"
    assert info == {"id": "c@g.us"}
    assert ("ephemeral", ("c@g.us", 3600)) in obj.calls
