from __future__ import annotations

from typing import Any

import pytest

from wassupweb.socket.groups import GroupsSocket, extract_group_metadata
from wassupweb.wabinary import BinaryNode


class _EventBus:
    def __init__(self) -> None:
        self.events: list[tuple[str, Any]] = []

    async def emit(self, event: str, payload: Any) -> None:
        self.events.append((event, payload))


def _group_node(group_id: str) -> BinaryNode:
    return BinaryNode(
        tag="group",
        attrs={"id": group_id, "subject": "G", "s_t": "10", "creation": "1"},
        content=[
            BinaryNode(tag="participant", attrs={"jid": "123@s.whatsapp.net", "type": "admin"}),
            BinaryNode(
                tag="description",
                attrs={"id": "desc-1", "participant": "123@s.whatsapp.net", "t": "11"},
                content=[BinaryNode(tag="body", attrs={}, content=b"hello")],
            ),
            BinaryNode(tag="member_add_mode", attrs={}, content="all_member_add"),
        ],
    )


def test_extract_group_metadata_parses_basic_fields() -> None:
    meta = extract_group_metadata(BinaryNode(tag="result", attrs={}, content=[_group_node("123456")]))
    assert meta.id == "123456@g.us"
    assert meta.subject == "G"
    assert meta.desc == "hello"
    assert meta.member_add_mode is True
    assert meta.participants[0].id == "123@s.whatsapp.net"
    assert meta.participants[0].admin == "admin"


@pytest.mark.asyncio
async def test_group_fetch_all_participating_emits_groups_update() -> None:
    class _Harness:
        def __init__(self) -> None:
            self.ev = _EventBus()

        async def query_node(self, _node: BinaryNode) -> BinaryNode:
            return BinaryNode(
                tag="iq",
                attrs={"type": "result"},
                content=[BinaryNode(tag="groups", attrs={}, content=[_group_node("123456")])],
            )

    obj = _Harness()
    result = await GroupsSocket.group_fetch_all_participating(obj)  # type: ignore[arg-type]
    assert "123456@g.us" in result
    assert obj.ev.events
    assert obj.ev.events[0][0] == "groups.update"


@pytest.mark.asyncio
async def test_group_request_participants_update_returns_statuses() -> None:
    class _Harness:
        def resolve_chat_jid(self, jid: str) -> str:
            return jid

        async def group_query(self, _jid: str, _type: str, _content: list[BinaryNode]) -> BinaryNode:
            return BinaryNode(
                tag="iq",
                attrs={"type": "result"},
                content=[
                    BinaryNode(
                        tag="membership_requests_action",
                        attrs={},
                        content=[
                            BinaryNode(
                                tag="approve",
                                attrs={},
                                content=[
                                    BinaryNode(tag="participant", attrs={"jid": "a@s.whatsapp.net"}),
                                    BinaryNode(tag="participant", attrs={"jid": "b@s.whatsapp.net", "error": "403"}),
                                ],
                            )
                        ],
                    )
                ],
            )

    obj = _Harness()
    result = await GroupsSocket.group_request_participants_update(  # type: ignore[arg-type]
        obj,
        "123@g.us",
        ["a@s.whatsapp.net", "b@s.whatsapp.net"],
        "approve",
    )
    assert result == [
        {"status": "200", "jid": "a@s.whatsapp.net"},
        {"status": "403", "jid": "b@s.whatsapp.net"},
    ]


@pytest.mark.asyncio
async def test_group_accept_invite_v4_emits_update_and_upsert() -> None:
    class _Harness:
        def __init__(self) -> None:
            self.ev = _EventBus()
            self.upserts: list[tuple[dict[str, Any], str]] = []
            self._group_accept_invite_v4_buffered = None
            self._get_group_accept_invite_v4_runner = GroupsSocket._get_group_accept_invite_v4_runner.__get__(self, _Harness)
            self._group_accept_invite_v4_impl = GroupsSocket._group_accept_invite_v4_impl.__get__(self, _Harness)

        async def group_query(self, _jid: str, _type: str, _content: list[BinaryNode]) -> BinaryNode:
            return BinaryNode(tag="iq", attrs={"type": "result", "from": "group@g.us"}, content=[])

        async def upsert_message(self, msg: dict[str, Any], upsert_type: str) -> None:
            self.upserts.append((msg, upsert_type))

        def _me_info(self) -> dict[str, Any]:
            return {"id": "me@s.whatsapp.net", "name": "Me"}

    obj = _Harness()
    result = await GroupsSocket.group_accept_invite_v4(  # type: ignore[arg-type]
        obj,
        {"remoteJid": "admin@s.whatsapp.net", "id": "msg-1"},
        {"groupJid": "group@g.us", "inviteCode": "abc", "inviteExpiration": 123},
    )
    assert result == "group@g.us"
    assert obj.upserts and obj.upserts[0][1] == "notify"
    assert any(event == "messages.update" for event, _ in obj.ev.events)


@pytest.mark.asyncio
async def test_group_accept_invite_v4_uses_buffered_runner_once() -> None:
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
            self.upserts: list[tuple[dict[str, Any], str]] = []
            self._group_accept_invite_v4_buffered = None
            self._get_group_accept_invite_v4_runner = GroupsSocket._get_group_accept_invite_v4_runner.__get__(self, _Harness)
            self._group_accept_invite_v4_impl = GroupsSocket._group_accept_invite_v4_impl.__get__(self, _Harness)

        async def group_query(self, _jid: str, _type: str, _content: list[BinaryNode]) -> BinaryNode:
            return BinaryNode(tag="iq", attrs={"type": "result", "from": "group@g.us"}, content=[])

        async def upsert_message(self, msg: dict[str, Any], upsert_type: str) -> None:
            self.upserts.append((msg, upsert_type))

        def _me_info(self) -> dict[str, Any]:
            return {"id": "me@s.whatsapp.net", "name": "Me"}

    obj = _Harness()
    await GroupsSocket.group_accept_invite_v4(  # type: ignore[arg-type]
        obj,
        "admin@s.whatsapp.net",
        {"groupJid": "group@g.us", "inviteCode": "abc", "inviteExpiration": 123},
    )
    await GroupsSocket.group_accept_invite_v4(  # type: ignore[arg-type]
        obj,
        "admin@s.whatsapp.net",
        {"groupJid": "group@g.us", "inviteCode": "abc", "inviteExpiration": 123},
    )

    assert obj.ev.created == 1
    assert obj.ev.called == 2


@pytest.mark.asyncio
async def test_handle_group_dirty_triggers_refresh_and_clean() -> None:
    class _Harness:
        def __init__(self) -> None:
            self.calls: list[str] = []

        async def group_fetch_all_participating(self) -> None:
            self.calls.append("fetch")

        async def clean_dirty_bits(self, dirty_type: str) -> None:
            self.calls.append(f"clean:{dirty_type}")

    obj = _Harness()
    await GroupsSocket._handle_group_dirty(  # type: ignore[arg-type]
        obj,
        BinaryNode(tag="ib", attrs={}, content=[BinaryNode(tag="dirty", attrs={"type": "groups"})]),
    )
    assert obj.calls == ["fetch", "clean:groups"]


@pytest.mark.asyncio
async def test_group_typed_create_and_participant_update_interfaces() -> None:
    class _Harness:
        async def group_create(self, subject: str, participants: list[str]) -> dict[str, Any]:
            return {"subject": subject, "participants": participants}

        async def group_participants_update(
            self, jid: str, participants: list[str], action: str
        ) -> list[dict[str, str]]:
            return [{"jid": jid, "participants": ",".join(participants), "action": action}]

    obj = _Harness()
    created = await GroupsSocket.create_group(  # type: ignore[arg-type]
        obj,
        {"subject": "Team", "participants": ["a@s.whatsapp.net", "b@s.whatsapp.net"]},
    )
    updated = await GroupsSocket.update_group_participants(  # type: ignore[arg-type]
        obj,
        {"jid": "1@g.us", "participants": ["a@s.whatsapp.net"], "action": "add"},
    )

    assert created["subject"] == "Team"
    assert created["participants"] == ["a@s.whatsapp.net", "b@s.whatsapp.net"]
    assert updated == [{"jid": "1@g.us", "participants": "a@s.whatsapp.net", "action": "add"}]


@pytest.mark.asyncio
async def test_group_typed_update_interfaces_forward_expected_values() -> None:
    class _Harness:
        def __init__(self) -> None:
            self.calls: list[tuple[str, Any]] = []

        async def group_request_participants_update(self, jid: str, participants: list[str], action: str) -> list[dict[str, str]]:
            self.calls.append(("requests", (jid, participants, action)))
            return [{"jid": jid, "status": "200"}]

        async def group_update_subject(self, jid: str, subject: str) -> None:
            self.calls.append(("subject", (jid, subject)))

        async def group_update_description(self, jid: str, description: str | None) -> None:
            self.calls.append(("description", (jid, description)))

        async def group_setting_update(self, jid: str, setting: str) -> None:
            self.calls.append(("setting", (jid, setting)))

        async def group_member_add_mode(self, jid: str, mode: str) -> None:
            self.calls.append(("member_add_mode", (jid, mode)))

        async def group_join_approval_mode(self, jid: str, mode: str) -> None:
            self.calls.append(("join_approval_mode", (jid, mode)))

        async def group_toggle_ephemeral(self, jid: str, expiration: int) -> None:
            self.calls.append(("ephemeral", (jid, expiration)))

    obj = _Harness()
    await GroupsSocket.update_group_requests(  # type: ignore[arg-type]
        obj,
        {"jid": "1@g.us", "participants": ["a@s.whatsapp.net"], "action": "approve"},
    )
    await GroupsSocket.update_group_subject(  # type: ignore[arg-type]
        obj,
        {"jid": "1@g.us", "subject": "New Subject"},
    )
    await GroupsSocket.update_group_description(  # type: ignore[arg-type]
        obj,
        {"jid": "1@g.us", "description": "Desc"},
    )
    await GroupsSocket.update_group_setting(  # type: ignore[arg-type]
        obj,
        {"jid": "1@g.us", "setting": "announcement"},
    )
    await GroupsSocket.update_group_member_add_mode(  # type: ignore[arg-type]
        obj,
        {"jid": "1@g.us", "mode": "all_member_add"},
    )
    await GroupsSocket.update_group_join_approval_mode(  # type: ignore[arg-type]
        obj,
        {"jid": "1@g.us", "mode": "on"},
    )
    await GroupsSocket.update_group_ephemeral(  # type: ignore[arg-type]
        obj,
        {"jid": "1@g.us", "ephemeralExpiration": 86400},
    )

    assert ("requests", ("1@g.us", ["a@s.whatsapp.net"], "approve")) in obj.calls
    assert ("subject", ("1@g.us", "New Subject")) in obj.calls
    assert ("description", ("1@g.us", "Desc")) in obj.calls
    assert ("setting", ("1@g.us", "announcement")) in obj.calls
    assert ("member_add_mode", ("1@g.us", "all_member_add")) in obj.calls
    assert ("join_approval_mode", ("1@g.us", "on")) in obj.calls
    assert ("ephemeral", ("1@g.us", 86400)) in obj.calls
