from __future__ import annotations

import asyncio
import base64
from types import SimpleNamespace
from typing import Any

import pytest

import wassupweb.socket.chats as chats_mod
from wassupweb.socket.chats import ChatsSocket
from wassupweb.types.business import QuickReplyAction
from wassupweb.types.label import LabelActionBody
from wassupweb.wabinary import jid_normalized_user
from wassupweb.wabinary import BinaryNode


class _Mutex:
    async def mutex(self, fn: Any) -> Any:
        return await fn()


class _EventBus:
    def __init__(self) -> None:
        self.events: list[tuple[str, Any]] = []

    async def emit(self, event: str, payload: Any) -> None:
        self.events.append((event, payload))


class _KeyStore:
    def __init__(self, app_state_key_id: str, app_state_key_data: bytes) -> None:
        self._state: dict[str, dict[str, Any]] = {
            "app-state-sync-key": {app_state_key_id: {"keyData": app_state_key_data}},
            "app-state-sync-version": {},
            "tctoken": {},
        }

    async def get(self, key_type: str, ids: list[str]) -> dict[str, Any]:
        store = self._state.setdefault(key_type, {})
        return {item_id: store[item_id] for item_id in ids if item_id in store}

    async def set(self, data: dict[str, dict[str, Any]]) -> None:
        for key_type, mapping in data.items():
            store = self._state.setdefault(key_type, {})
            for item_id, value in mapping.items():
                if value is None:
                    store.pop(item_id, None)
                else:
                    store[item_id] = value


class _PatchHarness:
    def __init__(self) -> None:
        key_id = base64.b64encode(b"\x22" * 32).decode("ascii")
        keys = _KeyStore(key_id, b"\x11" * 32)
        creds = SimpleNamespace(
            my_app_state_key_id=key_id,
            last_prop_hash=None,
            account_sync_counter=0,
        )
        self.config = SimpleNamespace(
            auth=SimpleNamespace(creds=creds, keys=keys),
            emit_own_events=False,
            options={},
            app_state_mac_verification={"patch": False, "snapshot": False},
            placeholder_resend_cache=None,
            fire_init_queries=False,
            mark_online_on_connect=True,
            should_sync_history_message=lambda _: True,
            get_message=None,
            should_ignore_jid=lambda _: False,
        )
        self.ev = _EventBus()
        self._logger = SimpleNamespace(info=lambda *_a, **_k: None)
        self.app_state_patch_mutex = _Mutex()
        self.queries: list[BinaryNode] = []

    def _require_auth(self) -> Any:
        return self.config.auth

    async def _run_keys_transaction(self, work: Any, _key: str) -> Any:
        return await work()

    async def resync_app_state(self, _collections: list[str], _is_initial_sync: bool) -> None:
        return None

    async def _get_app_state_sync_key(self, key_id: str) -> dict[str, Any] | None:
        result = await self.config.auth.keys.get("app-state-sync-key", [key_id])
        return result.get(key_id)

    async def query_node(self, node: BinaryNode, timeout_ms: int | None = None) -> BinaryNode:
        _ = timeout_ms
        self.queries.append(node)
        return BinaryNode(tag="iq", attrs={"type": "result"}, content=[])

    def _me_info(self) -> dict[str, Any]:
        return {"id": "me@s.whatsapp.net", "name": "Me"}


class _WrapperHarness:
    def __init__(self) -> None:
        self.mod: dict[str, Any] | None = None
        self.jid: str | None = None

    async def chat_modify(self, mod: dict[str, Any], jid: str) -> BinaryNode:
        self.mod = mod
        self.jid = jid
        return BinaryNode(tag="iq", attrs={"type": "result"}, content=[])


class _ChatModifyHarness:
    def __init__(self) -> None:
        self.patch: dict[str, Any] | None = None

    def resolve_chat_jid(self, jid: str) -> str:
        return jid

    async def app_patch(self, patch: dict[str, Any]) -> None:
        self.patch = patch


class _PresenceHarness:
    def __init__(self) -> None:
        key_id = base64.b64encode(b"\x44" * 32).decode("ascii")
        keys = _KeyStore(key_id, b"\x33" * 32)
        keys._state["tctoken"]["123@s.whatsapp.net"] = {"token": b"abc"}
        self.config = SimpleNamespace(auth=SimpleNamespace(keys=keys))
        self.sent: list[BinaryNode] = []

    def generate_message_tag(self) -> str:
        return "tag-1"

    async def send_node(self, node: BinaryNode) -> None:
        self.sent.append(node)


class _CallLinkHarness:
    def __init__(self) -> None:
        self.queries: list[BinaryNode] = []

    def generate_message_tag(self) -> str:
        return "tag-call-1"

    async def query_node(self, node: BinaryNode, timeout_ms: int | None = None) -> BinaryNode:
        _ = timeout_ms
        self.queries.append(node)
        return BinaryNode(
            tag="call",
            attrs={},
            content=[BinaryNode(tag="link_create", attrs={"token": "voice-token"})],
        )


class _BusinessHarness:
    def resolve_chat_jid(self, jid: str) -> str:
        return jid

    async def query_node(self, _node: BinaryNode, timeout_ms: int | None = None) -> BinaryNode:
        _ = timeout_ms
        return BinaryNode(
            tag="iq",
            attrs={"type": "result"},
            content=[
                BinaryNode(
                    tag="business_profile",
                    attrs={},
                    content=[
                        BinaryNode(
                            tag="profile",
                            attrs={"jid": "123@s.whatsapp.net"},
                            content=[
                                BinaryNode(tag="address", attrs={}, content=b"123 main"),
                                BinaryNode(tag="description", attrs={}, content=b"desc"),
                                BinaryNode(tag="website", attrs={}, content=b"https://example.com"),
                                BinaryNode(tag="email", attrs={}, content=b"a@example.com"),
                                BinaryNode(
                                    tag="categories",
                                    attrs={},
                                    content=[BinaryNode(tag="category", attrs={}, content=b"shopping")],
                                ),
                                BinaryNode(
                                    tag="business_hours",
                                    attrs={"timezone": "UTC"},
                                    content=[
                                        BinaryNode(
                                            tag="business_hours_config",
                                            attrs={"day_of_week": "mon", "mode": "open_24h"},
                                        )
                                    ],
                                ),
                            ],
                        )
                    ],
                )
            ],
        )


@pytest.mark.asyncio
async def test_app_patch_builds_sync_collection_query() -> None:
    obj = _PatchHarness()
    patch = {
        "syncAction": {"muteAction": {"muted": True, "muteEndTimestamp": 1000}, "timestamp": 1},
        "index": ["mute", "123@s.whatsapp.net"],
        "type": "regular_low",
        "apiVersion": 2,
        "operation": 1,
    }

    result = await ChatsSocket.app_patch(obj, patch)

    assert result
    sent = obj.queries[-1]
    sync_node = sent.content[0]
    collection = sync_node.content[0]
    assert collection.attrs["name"] == "regular_low"
    assert collection.attrs["version"] == "0"


@pytest.mark.asyncio
async def test_fetch_props_updates_last_hash_and_returns_dict() -> None:
    obj = _PatchHarness()

    async def _query_node(_node: BinaryNode, timeout_ms: int | None = None) -> BinaryNode:
        _ = timeout_ms
        return BinaryNode(
            tag="iq",
            attrs={"type": "result"},
            content=[
                BinaryNode(
                    tag="props",
                    attrs={"hash": "new-hash"},
                    content=[BinaryNode(tag="prop", attrs={"name": "ab", "value": "1"})],
                )
            ],
        )

    obj.query_node = _query_node  # type: ignore[assignment]
    props = await ChatsSocket.fetch_props(obj)

    assert props == {"ab": "1"}
    assert obj.config.auth.creds.last_prop_hash == "new-hash"
    assert ("creds.update", {"lastPropHash": "new-hash"}) in obj.ev.events


@pytest.mark.asyncio
async def test_label_and_quick_reply_wrappers_accept_pydantic_models() -> None:
    obj = _WrapperHarness()

    await ChatsSocket.add_label(obj, "123@s.whatsapp.net", LabelActionBody(id="lbl-1", name="Important"))
    assert obj.jid == "123@s.whatsapp.net"
    assert obj.mod == {"addLabel": {"id": "lbl-1", "name": "Important"}}

    await ChatsSocket.add_or_edit_quick_reply(
        obj,
        QuickReplyAction.model_validate({"timestamp": "101", "message": "hello", "shortcut": "/hi"}),
    )
    assert obj.jid == ""
    assert obj.mod == {"quickReply": {"timestamp": "101", "message": "hello", "shortcut": "/hi"}}


@pytest.mark.asyncio
async def test_chat_modify_routes_through_app_patch_generation() -> None:
    obj = _ChatModifyHarness()
    await ChatsSocket.chat_modify(obj, {"mute": 321}, "123@s.whatsapp.net")
    assert obj.patch is not None
    assert obj.patch["index"][:2] == ["mute", "123@s.whatsapp.net"]


@pytest.mark.asyncio
async def test_update_disable_link_previews_privacy_wraps_chat_modify_payload() -> None:
    obj = _WrapperHarness()
    await ChatsSocket.update_disable_link_previews_privacy(obj, True)
    assert obj.jid == ""
    assert obj.mod == {"disableLinkPreviews": {"isPreviewsDisabled": True}}


@pytest.mark.asyncio
async def test_presence_subscribe_adds_tc_token_content() -> None:
    obj = _PresenceHarness()
    await ChatsSocket.presence_subscribe(obj, "123@s.whatsapp.net")
    assert obj.sent
    node = obj.sent[0]
    assert node.tag == "presence"
    assert isinstance(node.content, list)
    assert node.content[0].tag == "tctoken"


@pytest.mark.asyncio
async def test_create_call_link_builds_call_query_and_returns_token() -> None:
    obj = _CallLinkHarness()
    token = await ChatsSocket.create_call_link(obj, "audio", {"startTime": 1700000000})
    assert token == "voice-token"
    sent = obj.queries[0]
    assert sent.tag == "call"
    assert sent.attrs["to"] == "@call"
    assert sent.attrs["id"] == "tag-call-1"
    assert isinstance(sent.content, list)
    link_create = sent.content[0]
    assert link_create.tag == "link_create"
    assert link_create.attrs["media"] == "audio"
    assert isinstance(link_create.content, list)
    assert link_create.content[0].tag == "event"
    assert link_create.content[0].attrs["start_time"] == "1700000000"


@pytest.mark.asyncio
async def test_get_business_profile_returns_parsed_pydantic_model() -> None:
    obj = _BusinessHarness()
    result = await ChatsSocket.get_business_profile(obj, "123@s.whatsapp.net")
    assert result is not None
    assert result.wid == "123@s.whatsapp.net"
    assert result.website == ["https://example.com"]
    assert result.business_hours.timezone == "UTC"


@pytest.mark.asyncio
async def test_upsert_message_prefers_participant_and_supports_camel_placeholder_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    placeholder_cache = {"seed": "cache"}
    key_store = SimpleNamespace()
    auth = SimpleNamespace(
        creds=SimpleNamespace(
            me={"id": "me@s.whatsapp.net", "name": "Me"},
            account_settings={"unarchiveChats": True},
        ),
        keys=key_store,
    )

    class _Harness:
        def __init__(self) -> None:
            self.ev = _EventBus()
            self._signal_repository = object()
            self._logger = SimpleNamespace(info=lambda *_a, **_k: None)
            self._sync_state = "online"
            self.config = SimpleNamespace(
                auth=auth,
                options={},
                should_sync_history_message=lambda _msg: True,
                get_message=lambda _key: {"text": "from-sync-callback"},
                placeholder_resend_cache=None,
                placeholderResendCache=placeholder_cache,
            )

        def _me_info(self) -> dict[str, Any]:
            return {"id": "me@s.whatsapp.net", "name": "Me"}

    captured: dict[str, Any] = {}

    async def _fake_process_message(_msg: dict[str, Any], opts: dict[str, Any]) -> None:
        captured["placeholder"] = opts.get("placeholderResendCache")
        captured["creds"] = opts.get("creds")
        captured["get_message_result"] = await opts["getMessage"]({"id": "abc"})

    monkeypatch.setattr(chats_mod, "process_message", _fake_process_message)

    obj = _Harness()
    msg = {
        "key": {
            "fromMe": False,
            "remoteJid": "group-chat@g.us",
            "participant": "1234567890:17@s.whatsapp.net",
        },
        "pushName": "Alice",
        "verifiedBizName": "Biz",
        "message": {"conversation": "hello"},
    }
    await ChatsSocket._upsert_message_impl(obj, msg, "notify")

    contacts_events = [event for event in obj.ev.events if event[0] == "contacts.update"]
    assert contacts_events
    expected = jid_normalized_user("1234567890:17@s.whatsapp.net")
    assert contacts_events[0][1][0]["id"] == expected

    assert captured["placeholder"] is placeholder_cache
    assert captured["creds"]["accountSettings"] == {"unarchiveChats": True}
    assert captured["creds"]["me"] == {"id": "me@s.whatsapp.net", "name": "Me"}
    assert captured["get_message_result"] == {"text": "from-sync-callback"}


@pytest.mark.asyncio
async def test_send_presence_update_available_does_not_block_on_unified_session_failure() -> None:
    class _PresenceUpdateHarness:
        def __init__(self) -> None:
            self.ev = _EventBus()
            self.sent: list[BinaryNode] = []
            self.safe_labels: list[str] = []
            self._logger = SimpleNamespace(warning=lambda *_a, **_k: None)

        def _me_info(self) -> dict[str, Any]:
            return {"id": "me@s.whatsapp.net", "name": "Me"}

        async def send_unified_session(self) -> None:
            raise RuntimeError("boom")

        async def _run_safe(self, coro: Any, label: str) -> None:
            try:
                await coro
            except Exception:
                self.safe_labels.append(label)

        async def send_node(self, node: BinaryNode) -> None:
            self.sent.append(node)

    obj = _PresenceUpdateHarness()
    await ChatsSocket.send_presence_update(obj, "available")
    await asyncio.sleep(0)

    assert ("connection.update", {"isOnline": True}) in obj.ev.events
    assert obj.sent and obj.sent[0].tag == "presence"
    assert obj.safe_labels == ["send unified session"]


@pytest.mark.asyncio
async def test_chats_typed_modify_contact_quickreply_and_chat_state_wrappers() -> None:
    class _TypedHarness:
        def __init__(self) -> None:
            self.calls: list[tuple[str, Any]] = []

        async def chat_modify(self, mod: dict[str, Any], jid: str) -> BinaryNode:
            self.calls.append(("chat_modify", (mod, jid)))
            return BinaryNode(tag="iq", attrs={"type": "result"})

        async def update_disable_link_previews_privacy(self, is_previews_disabled: bool) -> BinaryNode:
            self.calls.append(("disable_link_previews", is_previews_disabled))
            return BinaryNode(tag="iq", attrs={"type": "result"})

        async def star(self, jid: str, messages: list[dict[str, Any]], star: bool) -> BinaryNode:
            self.calls.append(("star", (jid, messages, star)))
            return BinaryNode(tag="iq", attrs={"type": "result"})

        async def add_or_edit_contact(self, jid: str, contact: dict[str, Any]) -> BinaryNode:
            self.calls.append(("contact_upsert", (jid, contact)))
            return BinaryNode(tag="iq", attrs={"type": "result"})

        async def remove_contact(self, jid: str) -> BinaryNode:
            self.calls.append(("contact_remove", jid))
            return BinaryNode(tag="iq", attrs={"type": "result"})

        async def add_or_edit_quick_reply(self, quick_reply: dict[str, Any]) -> BinaryNode:
            self.calls.append(("quick_reply_upsert", quick_reply))
            return BinaryNode(tag="iq", attrs={"type": "result"})

        async def remove_quick_reply(self, timestamp: str) -> BinaryNode:
            self.calls.append(("quick_reply_remove", timestamp))
            return BinaryNode(tag="iq", attrs={"type": "result"})

        async def archive_chat(self, jid: str, archive: bool, last_messages: list[dict[str, Any]] | None) -> BinaryNode:
            self.calls.append(("archive", (jid, archive, last_messages)))
            return BinaryNode(tag="iq", attrs={"type": "result"})

        async def mute_chat(self, jid: str, mute_seconds: int | None) -> BinaryNode:
            self.calls.append(("mute", (jid, mute_seconds)))
            return BinaryNode(tag="iq", attrs={"type": "result"})

        async def mark_read(self, jid: str, message_ids: list[str], read: bool = True) -> BinaryNode:
            self.calls.append(("mark_read", (jid, message_ids, read)))
            return BinaryNode(tag="iq", attrs={"type": "result"})

    obj = _TypedHarness()
    await ChatsSocket.modify_chat(obj, {"mod": {"pin": True}, "jid": "1@s.whatsapp.net"})  # type: ignore[arg-type]
    await ChatsSocket.set_link_previews_privacy(obj, {"isPreviewsDisabled": True})  # type: ignore[arg-type]
    await ChatsSocket.set_star_messages(  # type: ignore[arg-type]
        obj, {"jid": "1@s.whatsapp.net", "messages": [{"id": "m1"}], "star": True}
    )
    await ChatsSocket.upsert_contact_entry(  # type: ignore[arg-type]
        obj, {"jid": "1@s.whatsapp.net", "contact": {"name": "A"}}
    )
    await ChatsSocket.delete_contact_entry(obj, {"jid": "1@s.whatsapp.net"})  # type: ignore[arg-type]
    await ChatsSocket.upsert_quick_reply_entry(  # type: ignore[arg-type]
        obj, {"quickReply": {"timestamp": "1", "message": "hello"}}
    )
    await ChatsSocket.delete_quick_reply_entry(obj, {"timestamp": "1"})  # type: ignore[arg-type]
    await ChatsSocket.set_archive_chat(  # type: ignore[arg-type]
        obj, {"jid": "1@s.whatsapp.net", "archive": True, "lastMessages": [{"id": "m1"}]}
    )
    await ChatsSocket.set_mute_chat(obj, {"jid": "1@s.whatsapp.net", "muteSeconds": 30})  # type: ignore[arg-type]
    await ChatsSocket.set_mark_read(  # type: ignore[arg-type]
        obj, {"jid": "1@s.whatsapp.net", "messageIds": ["m1"], "read": False}
    )

    assert ("disable_link_previews", True) in obj.calls
    assert ("contact_remove", "1@s.whatsapp.net") in obj.calls
    assert ("quick_reply_remove", "1") in obj.calls
    assert ("mute", ("1@s.whatsapp.net", 30)) in obj.calls
    assert ("mark_read", ("1@s.whatsapp.net", ["m1"], False)) in obj.calls


@pytest.mark.asyncio
async def test_chats_typed_presence_profile_block_call_and_usync_wrappers() -> None:
    class _TypedHarness:
        def __init__(self) -> None:
            self.calls: list[tuple[str, Any]] = []

        async def fetch_status(self, *jids: str) -> list[dict[str, str]]:
            self.calls.append(("fetch_status", list(jids)))
            return [{"jid": jids[0]}] if jids else []

        async def fetch_disappearing_duration(self, *jids: str) -> list[dict[str, str]]:
            self.calls.append(("fetch_disappear", list(jids)))
            return [{"jid": jids[0]}] if jids else []

        async def on_whatsapp(self, *phone_numbers: str) -> list[dict[str, Any]]:
            self.calls.append(("on_whatsapp", list(phone_numbers)))
            return [{"jid": phone_numbers[0], "exists": True}] if phone_numbers else []

        async def pn_from_lid_usync(self, jids: list[str]) -> list[dict[str, str]]:
            self.calls.append(("pn_from_lid", jids))
            return [{"pn": "1@s.whatsapp.net", "lid": "1@lid"}]

        async def profile_picture_url(self, jid: str, picture_type: str = "preview", timeout_ms: int | None = None) -> str | None:
            self.calls.append(("profile_picture_url", (jid, picture_type, timeout_ms)))
            return "https://cdn/pic"

        async def update_profile_picture(self, jid: str, content: Any, dimensions: Any | None = None) -> BinaryNode:
            self.calls.append(("update_profile_picture", (jid, content, dimensions)))
            return BinaryNode(tag="iq", attrs={"type": "result"})

        async def remove_profile_picture(self, jid: str) -> BinaryNode:
            self.calls.append(("remove_profile_picture", jid))
            return BinaryNode(tag="iq", attrs={"type": "result"})

        async def update_profile_status(self, status: str) -> None:
            self.calls.append(("update_profile_status", status))

        async def update_profile_name(self, name: str) -> BinaryNode:
            self.calls.append(("update_profile_name", name))
            return BinaryNode(tag="iq", attrs={"type": "result"})

        async def update_block_status(self, jid: str, action: str) -> None:
            self.calls.append(("update_block_status", (jid, action)))

        async def clean_dirty_bits(self, type: str, from_timestamp: int | str | None = None) -> None:
            self.calls.append(("clean_dirty_bits", (type, from_timestamp)))

        async def create_call_link(self, media: str, event: dict[str, int] | None = None, timeout_ms: int | None = None) -> str | None:
            self.calls.append(("create_call_link", (media, event, timeout_ms)))
            return "token"

        async def send_presence_update(self, type: str, to_jid: str | None = None) -> None:
            self.calls.append(("send_presence_update", (type, to_jid)))

        async def presence_subscribe(self, to_jid: str) -> None:
            self.calls.append(("presence_subscribe", to_jid))

    obj = _TypedHarness()
    statuses = await ChatsSocket.fetch_status_for(obj, {"jids": ["1@s.whatsapp.net"]})  # type: ignore[arg-type]
    durations = await ChatsSocket.fetch_disappearing_duration_for(obj, {"jids": ["1@s.whatsapp.net"]})  # type: ignore[arg-type]
    whats = await ChatsSocket.check_on_whatsapp(obj, {"phoneNumbers": ["123@s.whatsapp.net"]})  # type: ignore[arg-type]
    mapping = await ChatsSocket.resolve_pn_from_lid(obj, {"jids": ["1@lid"]})  # type: ignore[arg-type]
    pic = await ChatsSocket.fetch_profile_picture(  # type: ignore[arg-type]
        obj, {"jid": "1@s.whatsapp.net", "pictureType": "image", "timeoutMs": 10}
    )
    await ChatsSocket.set_profile_picture(obj, {"jid": "1@s.whatsapp.net", "content": b"img"})  # type: ignore[arg-type]
    await ChatsSocket.clear_profile_picture(obj, {"jid": "1@s.whatsapp.net"})  # type: ignore[arg-type]
    await ChatsSocket.set_profile_status(obj, {"status": "hey"})  # type: ignore[arg-type]
    await ChatsSocket.set_profile_name(obj, {"name": "Name"})  # type: ignore[arg-type]
    await ChatsSocket.set_block_status_entry(obj, {"jid": "1@s.whatsapp.net", "action": "block"})  # type: ignore[arg-type]
    await ChatsSocket.clean_dirty_entry(  # type: ignore[arg-type]
        obj, {"type": "account_sync", "fromTimestamp": 123}
    )
    token = await ChatsSocket.create_call_link_entry(  # type: ignore[arg-type]
        obj, {"media": "audio", "event": {"startTime": 1}, "timeoutMs": 5}
    )
    await ChatsSocket.set_presence_state(obj, {"type": "available"})  # type: ignore[arg-type]
    await ChatsSocket.subscribe_presence_updates(obj, {"toJid": "1@s.whatsapp.net"})  # type: ignore[arg-type]

    assert statuses == [{"jid": "1@s.whatsapp.net"}]
    assert durations == [{"jid": "1@s.whatsapp.net"}]
    assert whats == [{"jid": "123@s.whatsapp.net", "exists": True}]
    assert mapping == [{"pn": "1@s.whatsapp.net", "lid": "1@lid"}]
    assert pic == "https://cdn/pic"
    assert token == "token"
    assert ("presence_subscribe", "1@s.whatsapp.net") in obj.calls
    assert ("update_block_status", ("1@s.whatsapp.net", "block")) in obj.calls
