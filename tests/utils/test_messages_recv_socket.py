from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any

import pytest

from wassupweb.types.message import WAMessageStubType
from wassupweb.utils.identity_change_handler import TTLBoolCache
from wassupweb.socket.messages_recv import MessagesRecvSocket
from wassupweb.wabinary import BinaryNode


class _EventCollector:
    def __init__(self) -> None:
        self.events: list[tuple[str, Any]] = []
        self.buffer_calls = 0
        self.flush_calls = 0

    async def emit(self, event: str, payload: Any) -> bool:
        self.events.append((event, payload))
        return True

    def buffer(self) -> None:
        self.buffer_calls += 1

    def flush(self) -> bool:
        self.flush_calls += 1
        return True


class _Logger:
    def debug(self, *_args: Any, **_kwargs: Any) -> None:
        return

    def info(self, *_args: Any, **_kwargs: Any) -> None:
        return

    def warning(self, *_args: Any, **_kwargs: Any) -> None:
        return

    def error(self, *_args: Any, **_kwargs: Any) -> None:
        return


class _Ids:
    def __init__(self) -> None:
        self.links: list[tuple[str, str]] = []

    def link_pn_lid(self, pn: str, lid: str) -> dict[str, str]:
        self.links.append((pn, lid))
        return {"pn": pn, "lid": lid}


class _PlaceholderCache:
    def __init__(self) -> None:
        self.data: dict[str, Any] = {}

    async def get(self, key: str) -> Any:
        return self.data.get(key)

    async def set(self, key: str, value: Any) -> bool:
        self.data[key] = value
        return True

    async def del_(self, key: str) -> bool:
        self.data.pop(key, None)
        return True


class _RetryCountCache:
    def __init__(self) -> None:
        self.data: dict[str, int] = {}

    async def get(self, key: str) -> int:
        return int(self.data.get(key, 0))

    async def set(self, key: str, value: int) -> bool:
        self.data[key] = int(value)
        return True

    async def del_(self, key: str) -> bool:
        self.data.pop(key, None)
        return True


class _Keys:
    def __init__(self) -> None:
        self.set_calls: list[dict[str, Any]] = []

    async def set(self, data: dict[str, Any]) -> None:
        self.set_calls.append(data)


class _DummySocket:
    def __init__(self) -> None:
        self.ev = _EventCollector()
        self._logger = _Logger()
        self.ids = _Ids()
        self._sent_nodes: list[BinaryNode] = []
        self._query_nodes: list[BinaryNode] = []
        self._upserts: list[tuple[dict[str, Any], str]] = []
        self._relayed: list[tuple[str, dict[str, Any], str | None]] = []
        self._messages: dict[str, dict[str, Any]] = {}
        self._resynced: list[tuple[list[str], bool]] = []
        self._signal_repository = None
        self._msg_retry_counter_cache: dict[str, int] = {}
        self._placeholder_resend_fallback_cache: dict[str, Any] = {}
        self._next_tag = 0
        self._keys = _Keys()
        self._uploaded_prekeys = 0
        self._identity_assert_debounce = TTLBoolCache(ttl_ms=5_000)
        self._offline_nodes: list[tuple[str, BinaryNode]] = []
        self._offline_processing = False
        self.message_retry_manager = None

        async def _get_message(key: dict[str, Any]) -> dict[str, Any] | None:
            return self._messages.get(str(key.get("id") or ""))

        self.config = SimpleNamespace(
            should_ignore_jid=lambda _jid: False,
            placeholder_resend_cache=_PlaceholderCache(),
            auth=SimpleNamespace(
                creds=SimpleNamespace(me={"id": "me@s.whatsapp.net"}, account_settings={"unarchiveChats": False}),
                keys=self._keys,
            ),
            get_message=_get_message,
            max_msg_retry_count=5,
            options={},
        )
        self._cache_get = MessagesRecvSocket._cache_get.__get__(self, _DummySocket)  # type: ignore[attr-defined]
        self._cache_set = MessagesRecvSocket._cache_set.__get__(self, _DummySocket)  # type: ignore[attr-defined]
        self._cache_del = MessagesRecvSocket._cache_del.__get__(self, _DummySocket)  # type: ignore[attr-defined]
        self._retry_count_get = MessagesRecvSocket._retry_count_get.__get__(self, _DummySocket)  # type: ignore[attr-defined]
        self._retry_count_set = MessagesRecvSocket._retry_count_set.__get__(self, _DummySocket)  # type: ignore[attr-defined]
        self._retry_count_del = MessagesRecvSocket._retry_count_del.__get__(self, _DummySocket)  # type: ignore[attr-defined]
        self._is_offline_attr = MessagesRecvSocket._is_offline_attr  # type: ignore[attr-defined]
        self._report_node_error = MessagesRecvSocket._report_node_error.__get__(self, _DummySocket)  # type: ignore[attr-defined]
        self._process_node_with_buffer = MessagesRecvSocket._process_node_with_buffer.__get__(self, _DummySocket)  # type: ignore[attr-defined]
        self._process_offline_nodes = MessagesRecvSocket._process_offline_nodes.__get__(self, _DummySocket)  # type: ignore[attr-defined]
        self._enqueue_offline_node = MessagesRecvSocket._enqueue_offline_node.__get__(self, _DummySocket)  # type: ignore[attr-defined]
        self._dispatch_incoming_node = MessagesRecvSocket._dispatch_incoming_node.__get__(self, _DummySocket)  # type: ignore[attr-defined]
        self._on_message_node = MessagesRecvSocket._on_message_node.__get__(self, _DummySocket)  # type: ignore[attr-defined]
        self._on_receipt_node = MessagesRecvSocket._on_receipt_node.__get__(self, _DummySocket)  # type: ignore[attr-defined]
        self._on_notification_node = MessagesRecvSocket._on_notification_node.__get__(self, _DummySocket)  # type: ignore[attr-defined]
        self.send_peer_data_operation_message = MessagesRecvSocket.send_peer_data_operation_message.__get__(self, _DummySocket)  # type: ignore[attr-defined]
        self._handle_mex_newsletter_notification = MessagesRecvSocket._handle_mex_newsletter_notification.__get__(self, _DummySocket)  # type: ignore[attr-defined]
        self._handle_newsletter_notification = MessagesRecvSocket._handle_newsletter_notification.__get__(self, _DummySocket)  # type: ignore[attr-defined]
        self._handle_privacy_token_notification = MessagesRecvSocket._handle_privacy_token_notification.__get__(self, _DummySocket)  # type: ignore[attr-defined]
        self._handle_encrypt_notification = MessagesRecvSocket._handle_encrypt_notification.__get__(self, _DummySocket)  # type: ignore[attr-defined]
        self._required_bytes = MessagesRecvSocket._required_bytes  # type: ignore[attr-defined]
        self._decipher_link_public_key = MessagesRecvSocket._decipher_link_public_key.__get__(self, _DummySocket)  # type: ignore[attr-defined]
        self._handle_link_code_companion_registration = MessagesRecvSocket._handle_link_code_companion_registration.__get__(self, _DummySocket)  # type: ignore[attr-defined]
        self._extract_group_metadata = MessagesRecvSocket._extract_group_metadata.__get__(self, _DummySocket)  # type: ignore[attr-defined]
        self._decode_text = MessagesRecvSocket._decode_text  # type: ignore[attr-defined]
        self._handle_group_notification = MessagesRecvSocket._handle_group_notification.__get__(self, _DummySocket)  # type: ignore[attr-defined]
        self._upsert_notification_message = MessagesRecvSocket._upsert_notification_message.__get__(self, _DummySocket)  # type: ignore[attr-defined]
        self._handle_ack_node = MessagesRecvSocket._handle_ack_node.__get__(self, _DummySocket)  # type: ignore[attr-defined]
        self._handle_message_node = MessagesRecvSocket._handle_message_node.__get__(self, _DummySocket)  # type: ignore[attr-defined]
        self._handle_receipt_node = MessagesRecvSocket._handle_receipt_node.__get__(self, _DummySocket)  # type: ignore[attr-defined]
        self._handle_notification_node = MessagesRecvSocket._handle_notification_node.__get__(self, _DummySocket)  # type: ignore[attr-defined]
        self._me_info = MessagesRecvSocket._me_info.__get__(self, _DummySocket)  # type: ignore[attr-defined]
        self._to_int = MessagesRecvSocket._to_int  # type: ignore[attr-defined]
        self.will_send_message_again = MessagesRecvSocket.will_send_message_again.__get__(self, _DummySocket)  # type: ignore[attr-defined]
        self.update_send_message_again_count = MessagesRecvSocket.update_send_message_again_count.__get__(self, _DummySocket)  # type: ignore[attr-defined]
        self.send_messages_again = MessagesRecvSocket.send_messages_again.__get__(self, _DummySocket)  # type: ignore[attr-defined]

    def generate_message_tag(self) -> str:
        self._next_tag += 1
        return f"tag-{self._next_tag}"

    async def query_node(self, node: BinaryNode) -> BinaryNode:
        self._query_nodes.append(node)
        return BinaryNode(tag="iq", attrs={"id": node.attrs.get("id", "")})

    async def send_node(self, node: BinaryNode) -> None:
        self._sent_nodes.append(node)

    async def upload_pre_keys(self) -> None:
        self._uploaded_prekeys += 1

    def _me_id(self) -> str:
        return "me@s.whatsapp.net"

    def _decode_proto_message(self, _name: str, _payload: bytes) -> dict[str, Any]:
        return {"conversation": "hello"}

    async def upsert_message(self, msg: dict[str, Any], type_: str) -> None:
        self._upserts.append((msg, type_))

    async def relay_message(
        self,
        jid: str,
        message: dict[str, Any],
        message_id: str | None = None,
        **_kwargs: Any,
    ) -> None:
        self._relayed.append((jid, message, message_id))
        return message_id or f"relay-{len(self._relayed)}"

    async def resync_app_state(self, names: list[str], is_initial: bool) -> None:
        self._resynced.append((names, is_initial))

    async def send_message_ack(self, node: BinaryNode, error_code: int | None = None) -> BinaryNode:
        return await MessagesRecvSocket.send_message_ack(self, node, error_code)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_send_message_ack_parity_fields() -> None:
    sock = _DummySocket()
    node = BinaryNode(
        tag="message",
        attrs={
            "id": "abc",
            "from": "123@s.whatsapp.net",
            "type": "retry",
            "participant": "777@s.whatsapp.net",
            "recipient": "999@s.whatsapp.net",
        },
        content=[BinaryNode(tag="unavailable", attrs={"type": "foo"})],
    )

    ack = await MessagesRecvSocket.send_message_ack(sock, node, error_code=500)  # type: ignore[arg-type]
    assert ack.tag == "ack"
    assert ack.attrs["class"] == "message"
    assert ack.attrs["id"] == "abc"
    assert ack.attrs["to"] == "123@s.whatsapp.net"
    assert ack.attrs["participant"] == "777@s.whatsapp.net"
    assert ack.attrs["recipient"] == "999@s.whatsapp.net"
    assert ack.attrs["type"] == "retry"
    assert ack.attrs["error"] == "500"
    assert ack.attrs["from"] == "me@s.whatsapp.net"


@pytest.mark.asyncio
async def test_process_node_with_buffer_buffers_and_flushes_events() -> None:
    sock = _DummySocket()
    called: list[str] = []

    async def _handler(_node: BinaryNode) -> None:
        called.append("ok")

    await MessagesRecvSocket._process_node_with_buffer(  # type: ignore[arg-type]
        sock,
        BinaryNode(tag="message", attrs={"id": "b1"}),
        "processing message",
        _handler,
    )
    assert called == ["ok"]
    assert sock.ev.buffer_calls == 1
    assert sock.ev.flush_calls == 1


@pytest.mark.asyncio
async def test_on_message_node_offline_enqueues_and_processes_queue(monkeypatch: pytest.MonkeyPatch) -> None:
    sock = _DummySocket()
    seen: list[str] = []

    async def _fake_handle(node: BinaryNode) -> None:
        seen.append(node.attrs.get("id", ""))

    sock._handle_message_node = _fake_handle  # type: ignore[assignment]
    scheduled: list[asyncio.Task[Any]] = []
    real_create_task = asyncio.create_task

    def _capture_task(coro: Any) -> asyncio.Task[Any]:
        task = real_create_task(coro)
        scheduled.append(task)
        return task

    monkeypatch.setattr("wassupweb.socket.messages_recv.asyncio.create_task", _capture_task)

    await MessagesRecvSocket._on_message_node(  # type: ignore[arg-type]
        sock,
        BinaryNode(tag="message", attrs={"id": "offline-1", "offline": "true"}),
    )
    if scheduled:
        await asyncio.gather(*scheduled)

    assert seen == ["offline-1"]
    assert sock._offline_nodes == []


@pytest.mark.asyncio
async def test_handle_notification_lid_mapping_emits_and_acks() -> None:
    sock = _DummySocket()
    node = BinaryNode(
        tag="notification",
        attrs={
            "id": "n1",
            "from": "server@c.us",
            "type": "lid-mapping",
            "lid": "lid-user@lid",
            "pn": "pn-user@s.whatsapp.net",
        },
    )

    await MessagesRecvSocket._handle_notification_node(sock, node)  # type: ignore[arg-type]
    assert ("pn-user@s.whatsapp.net", "lid-user@lid") in sock.ids.links
    assert ("lid-mapping.update", {"lid": "lid-user@lid", "pn": "pn-user@s.whatsapp.net"}) in sock.ev.events
    assert sock._sent_nodes and sock._sent_nodes[-1].tag == "ack"


@pytest.mark.asyncio
async def test_handle_notification_newsletter_reaction_emits_event() -> None:
    sock = _DummySocket()
    reaction_child = BinaryNode(
        tag="reaction",
        attrs={"message_id": "srv-1"},
        content=[BinaryNode(tag="reaction", attrs={}, content="🔥")],
    )
    node = BinaryNode(
        tag="notification",
        attrs={"id": "n2", "from": "news@newsletter", "participant": "author@s.whatsapp.net", "type": "newsletter"},
        content=[reaction_child],
    )

    await MessagesRecvSocket._handle_notification_node(sock, node)  # type: ignore[arg-type]
    assert ("newsletter.reaction", {"id": "news@newsletter", "server_id": "srv-1", "reaction": {"code": "🔥", "count": 1}}) in sock.ev.events
    assert sock._sent_nodes and sock._sent_nodes[-1].tag == "ack"


@pytest.mark.asyncio
async def test_handle_notification_mex_admin_promote_emits_event() -> None:
    sock = _DummySocket()
    mex_payload = {
        "operation": "NotificationNewsletterAdminPromote",
        "updates": [{"jid": "news@newsletter", "user": "x@s.whatsapp.net"}],
    }
    node = BinaryNode(
        tag="notification",
        attrs={"id": "n3", "from": "news@newsletter", "type": "mex"},
        content=[BinaryNode(tag="mex", attrs={}, content=json.dumps(mex_payload).encode("utf-8"))],
    )

    await MessagesRecvSocket._handle_notification_node(sock, node)  # type: ignore[arg-type]
    assert (
        "newsletter-participants.update",
        {"id": "news@newsletter", "author": "news@newsletter", "user": "x@s.whatsapp.net", "new_role": "ADMIN", "action": "promote"},
    ) in sock.ev.events


@pytest.mark.asyncio
async def test_handle_notification_mediaretry_emits_messages_media_update() -> None:
    sock = _DummySocket()
    node = BinaryNode(
        tag="notification",
        attrs={"id": "MID", "from": "server@c.us", "type": "mediaretry"},
        content=[
            BinaryNode(tag="encrypt", attrs={}, content=[BinaryNode(tag="enc_p", attrs={}, content=b"abc"), BinaryNode(tag="enc_iv", attrs={}, content=b"012345678901")]),
            BinaryNode(tag="rmr", attrs={"jid": "chat@s.whatsapp.net", "from_me": "false"}),
        ],
    )

    await MessagesRecvSocket._handle_notification_node(sock, node)  # type: ignore[arg-type]
    media_update = [payload for event, payload in sock.ev.events if event == "messages.media-update"]
    assert media_update
    assert media_update[0][0]["key"]["id"] == "MID"


@pytest.mark.asyncio
async def test_fetch_message_history_builds_pdo_request() -> None:
    sock = _DummySocket()
    request_id = await MessagesRecvSocket.fetch_message_history(  # type: ignore[arg-type]
        sock,
        count=30,
        oldest_msg_key={"remoteJid": "chat@s.whatsapp.net", "fromMe": False, "id": "MSG1"},
        oldest_msg_timestamp=123456789,
    )

    assert request_id == "relay-1"
    assert sock._relayed
    relay_jid, relay_message, _relay_mid = sock._relayed[-1]
    assert relay_jid == "me@s.whatsapp.net"
    protocol = relay_message["protocolMessage"]
    decoded = protocol["peerDataOperationRequestMessage"]
    assert decoded["historySyncOnDemandRequest"]["chatJid"] == "chat@s.whatsapp.net"
    assert decoded["historySyncOnDemandRequest"]["oldestMsgId"] == "MSG1"
    assert decoded["peerDataOperationRequestType"] == "HISTORY_SYNC_ON_DEMAND"


@pytest.mark.asyncio
async def test_request_placeholder_resend_builds_request_and_sets_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    sock = _DummySocket()

    async def _no_sleep(_seconds: float) -> None:
        return

    def _drop_task(coro: Any) -> Any:
        coro.close()
        return SimpleNamespace(done=lambda: True)

    monkeypatch.setattr("wassupweb.socket.messages_recv.asyncio.sleep", _no_sleep)
    monkeypatch.setattr("wassupweb.socket.messages_recv.asyncio.create_task", _drop_task)

    request_id = await MessagesRecvSocket.request_placeholder_resend(  # type: ignore[arg-type]
        sock,
        {"remoteJid": "chat@s.whatsapp.net", "fromMe": False, "id": "M100"},
    )
    assert request_id == "relay-1"
    assert sock._relayed
    decoded = sock._relayed[-1][1]["protocolMessage"]["peerDataOperationRequestMessage"]
    assert decoded["peerDataOperationRequestType"] == "PLACEHOLDER_MESSAGE_RESEND"
    assert decoded["placeholderMessageResendRequest"][0]["messageKey"]["id"] == "M100"


@pytest.mark.asyncio
async def test_request_placeholder_resend_works_without_configured_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    sock = _DummySocket()
    sock.config.placeholder_resend_cache = None

    async def _no_sleep(_seconds: float) -> None:
        return

    def _drop_task(coro: Any) -> Any:
        coro.close()
        return SimpleNamespace(done=lambda: True)

    monkeypatch.setattr("wassupweb.socket.messages_recv.asyncio.sleep", _no_sleep)
    monkeypatch.setattr("wassupweb.socket.messages_recv.asyncio.create_task", _drop_task)

    request_id = await MessagesRecvSocket.request_placeholder_resend(  # type: ignore[arg-type]
        sock,
        {"remoteJid": "chat@s.whatsapp.net", "fromMe": False, "id": "M101"},
    )
    assert request_id == "relay-1"
    assert sock._relayed
    decoded = sock._relayed[-1][1]["protocolMessage"]["peerDataOperationRequestMessage"]
    assert decoded["placeholderMessageResendRequest"][0]["messageKey"]["id"] == "M101"


@pytest.mark.asyncio
async def test_handle_notification_privacy_token_persists_trusted_contact_token() -> None:
    sock = _DummySocket()
    node = BinaryNode(
        tag="notification",
        attrs={"id": "n4", "from": "111222333@s.whatsapp.net", "type": "privacy_token"},
        content=[
            BinaryNode(
                tag="tokens",
                attrs={},
                content=[
                    BinaryNode(tag="token", attrs={"type": "trusted_contact", "t": "123"}, content=b"tc-token"),
                ],
            )
        ],
    )

    await MessagesRecvSocket._handle_notification_node(sock, node)  # type: ignore[arg-type]
    assert sock._keys.set_calls
    tctoken = sock._keys.set_calls[-1]["tctoken"]
    assert tctoken["111222333@s.whatsapp.net"]["token"] == b"tc-token"


@pytest.mark.asyncio
async def test_send_messages_again_relays_messages_from_get_message() -> None:
    sock = _DummySocket()
    sock._messages["M200"] = {"conversation": "hello"}

    await MessagesRecvSocket.send_messages_again(  # type: ignore[arg-type]
        sock,
        {"remoteJid": "chat@s.whatsapp.net", "participant": "peer@s.whatsapp.net"},
        ["M200"],
        BinaryNode(tag="retry", attrs={"count": "1"}),
    )

    assert sock._relayed == [("chat@s.whatsapp.net", {"conversation": "hello"}, "M200")]
    assert sock._msg_retry_counter_cache["M200:peer@s.whatsapp.net"] == 1


@pytest.mark.asyncio
async def test_retry_count_uses_external_cache_for_increment_and_limit() -> None:
    sock = _DummySocket()
    retry_cache = _RetryCountCache()
    sock.config.msg_retry_counter_cache = retry_cache
    sock.config.max_msg_retry_count = 2

    await MessagesRecvSocket.update_send_message_again_count(sock, "M210", "peer@s.whatsapp.net")  # type: ignore[arg-type]
    await MessagesRecvSocket.update_send_message_again_count(sock, "M210", "peer@s.whatsapp.net")  # type: ignore[arg-type]

    assert retry_cache.data["M210:peer@s.whatsapp.net"] == 2
    assert await MessagesRecvSocket.will_send_message_again(sock, "M210", "peer@s.whatsapp.net") is False  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_handle_receipt_retry_triggers_send_messages_again() -> None:
    sock = _DummySocket()
    sock._messages["M300"] = {"conversation": "retry me"}

    node = BinaryNode(
        tag="receipt",
        attrs={
            "id": "M300",
            "from": "chat@s.whatsapp.net",
            "participant": "peer@s.whatsapp.net",
            "type": "retry",
            "t": "1700000000",
        },
        content=[BinaryNode(tag="retry", attrs={"count": "1"})],
    )

    await MessagesRecvSocket._handle_receipt_node(sock, node)  # type: ignore[arg-type]
    assert sock._relayed == [("chat@s.whatsapp.net", {"conversation": "retry me"}, "M300")]
    assert sock._sent_nodes and sock._sent_nodes[-1].tag == "ack"


@pytest.mark.asyncio
async def test_handle_receipt_retry_from_me_uses_recipient_as_remote_jid() -> None:
    sock = _DummySocket()
    sock._messages["M301"] = {"conversation": "retry me 2"}

    node = BinaryNode(
        tag="receipt",
        attrs={
            "id": "M301",
            "from": "chat@s.whatsapp.net",
            "recipient": "other@s.whatsapp.net",
            "participant": "me@s.whatsapp.net",
            "type": "retry",
            "t": "1700000001",
        },
        content=[BinaryNode(tag="retry", attrs={"count": "1"})],
    )

    await MessagesRecvSocket._handle_receipt_node(sock, node)  # type: ignore[arg-type]
    messages_update = [payload for event, payload in sock.ev.events if event == "messages.update"]
    assert messages_update
    assert messages_update[0][0]["key"]["remoteJid"] == "other@s.whatsapp.net"
    assert messages_update[0][0]["key"]["fromMe"] is True


@pytest.mark.asyncio
async def test_handle_notification_server_sync_triggers_resync_app_state() -> None:
    sock = _DummySocket()
    node = BinaryNode(
        tag="notification",
        attrs={"id": "n5", "from": "server@c.us", "type": "server_sync"},
        content=[BinaryNode(tag="collection", attrs={"name": "regular"})],
    )

    await MessagesRecvSocket._handle_notification_node(sock, node)  # type: ignore[arg-type]
    assert sock._resynced == [(["regular"], False)]
    assert sock._sent_nodes and sock._sent_nodes[-1].tag == "ack"


@pytest.mark.asyncio
async def test_handle_notification_account_sync_disappearing_updates_creds() -> None:
    sock = _DummySocket()
    node = BinaryNode(
        tag="notification",
        attrs={"id": "n6", "from": "server@c.us", "type": "account_sync"},
        content=[BinaryNode(tag="disappearing_mode", attrs={"duration": "86400", "t": "1700001000"})],
    )

    await MessagesRecvSocket._handle_notification_node(sock, node)  # type: ignore[arg-type]
    creds_updates = [payload for event, payload in sock.ev.events if event == "creds.update"]
    assert creds_updates
    account_settings = creds_updates[0]["accountSettings"]
    assert account_settings["defaultDisappearingMode"]["ephemeralExpiration"] == 86400
    assert account_settings["defaultDisappearingMode"]["ephemeralSettingTimestamp"] == 1700001000


@pytest.mark.asyncio
async def test_handle_notification_account_sync_blocklist_emits_updates() -> None:
    sock = _DummySocket()
    node = BinaryNode(
        tag="notification",
        attrs={"id": "n7", "from": "server@c.us", "type": "account_sync"},
        content=[
            BinaryNode(
                tag="blocklist",
                attrs={},
                content=[
                    BinaryNode(tag="item", attrs={"jid": "111@s.whatsapp.net", "action": "block"}),
                    BinaryNode(tag="item", attrs={"jid": "222@s.whatsapp.net", "action": "unblock"}),
                ],
            )
        ],
    )

    await MessagesRecvSocket._handle_notification_node(sock, node)  # type: ignore[arg-type]
    block_updates = [payload for event, payload in sock.ev.events if event == "blocklist.update"]
    assert block_updates == [
        {"blocklist": ["111@s.whatsapp.net"], "type": "add"},
        {"blocklist": ["222@s.whatsapp.net"], "type": "remove"},
    ]


@pytest.mark.asyncio
async def test_handle_notification_wgp2_create_emits_group_events_and_stub_message() -> None:
    sock = _DummySocket()
    node = BinaryNode(
        tag="notification",
        attrs={
            "id": "g1",
            "from": "12345@g.us",
            "participant": "owner@s.whatsapp.net",
            "participant_pn": "1111111111@s.whatsapp.net",
            "type": "w:gp2",
            "t": "1700002000",
        },
        content=[
            BinaryNode(
                tag="create",
                attrs={},
                content=[
                    BinaryNode(
                        tag="group",
                        attrs={
                            "id": "12345@g.us",
                            "subject": "Test Group",
                            "creator": "owner@s.whatsapp.net",
                            "creation": "1700001999",
                        },
                    )
                ],
            )
        ],
    )

    await MessagesRecvSocket._handle_notification_node(sock, node)  # type: ignore[arg-type]

    chats_upsert = [payload for event, payload in sock.ev.events if event == "chats.upsert"]
    groups_upsert = [payload for event, payload in sock.ev.events if event == "groups.upsert"]
    assert chats_upsert
    assert groups_upsert
    assert sock._upserts
    message, upsert_type = sock._upserts[-1]
    assert upsert_type == "append"
    assert message["messageStubType"] == int(WAMessageStubType.GROUP_CREATE)
    assert message["messageStubParameters"] == ["Test Group"]
    assert message["key"]["remoteJid"] == "12345@g.us"


@pytest.mark.asyncio
async def test_handle_notification_wgp2_remove_self_maps_to_leave_stub() -> None:
    sock = _DummySocket()
    node = BinaryNode(
        tag="notification",
        attrs={
            "id": "g2",
            "from": "12345@g.us",
            "participant": "me@s.whatsapp.net",
            "type": "w:gp2",
            "t": "1700002100",
        },
        content=[
            BinaryNode(
                tag="remove",
                attrs={},
                content=[BinaryNode(tag="participant", attrs={"jid": "me@s.whatsapp.net"})],
            )
        ],
    )

    await MessagesRecvSocket._handle_notification_node(sock, node)  # type: ignore[arg-type]
    assert sock._upserts
    message, _ = sock._upserts[-1]
    assert message["messageStubType"] == int(WAMessageStubType.GROUP_PARTICIPANT_LEAVE)


@pytest.mark.asyncio
async def test_handle_notification_picture_group_emits_contacts_and_icon_stub() -> None:
    sock = _DummySocket()
    node = BinaryNode(
        tag="notification",
        attrs={
            "id": "g3",
            "from": "12345@g.us",
            "type": "picture",
            "t": "1700002200",
        },
        content=[
            BinaryNode(
                tag="set",
                attrs={"id": "img1", "author": "owner@s.whatsapp.net"},
            )
        ],
    )

    await MessagesRecvSocket._handle_notification_node(sock, node)  # type: ignore[arg-type]

    contacts_update = [payload for event, payload in sock.ev.events if event == "contacts.update"]
    assert contacts_update == [[{"id": "12345@g.us", "imgUrl": "changed"}]]
    assert sock._upserts
    message, _ = sock._upserts[-1]
    assert message["messageStubType"] == int(WAMessageStubType.GROUP_CHANGE_ICON)
    assert message["messageStubParameters"] == ["img1"]
    assert message["participant"] == "owner@s.whatsapp.net"


@pytest.mark.asyncio
async def test_handle_notification_encrypt_low_prekey_count_uploads_prekeys() -> None:
    sock = _DummySocket()
    node = BinaryNode(
        tag="notification",
        attrs={"id": "enc1", "from": "s.whatsapp.net", "type": "encrypt"},
        content=[BinaryNode(tag="count", attrs={"value": "1"})],
    )
    await MessagesRecvSocket._handle_notification_node(sock, node)  # type: ignore[arg-type]
    assert sock._uploaded_prekeys == 1


class _RetryManagerStub:
    def __init__(self, cached_message: dict[str, Any] | None = None) -> None:
        self.cached_message = cached_message
        self.success: list[str] = []
        self.failed: list[str] = []
        self.calls_get: list[tuple[str, str]] = []

    def get_recent_message(self, remote_jid: str, msg_id: str) -> Any:
        self.calls_get.append((remote_jid, msg_id))
        if self.cached_message is None:
            return None
        return SimpleNamespace(message=self.cached_message)

    def mark_retry_success(self, msg_id: str) -> None:
        self.success.append(msg_id)

    def mark_retry_failed(self, msg_id: str) -> None:
        self.failed.append(msg_id)


@pytest.mark.asyncio
async def test_send_messages_again_uses_retry_cache_before_get_message() -> None:
    sock = _DummySocket()
    manager = _RetryManagerStub(cached_message={"conversation": "from-cache"})
    sock.message_retry_manager = manager

    await MessagesRecvSocket.send_messages_again(  # type: ignore[arg-type]
        sock,
        {"remoteJid": "chat@s.whatsapp.net", "participant": "peer@s.whatsapp.net"},
        ["M400"],
        BinaryNode(tag="retry", attrs={"count": "2"}),
    )

    assert manager.calls_get == [("chat@s.whatsapp.net", "M400")]
    assert manager.success == ["M400"]
    assert sock._relayed == [("chat@s.whatsapp.net", {"conversation": "from-cache"}, "M400")]


@pytest.mark.asyncio
async def test_handle_ack_node_emits_message_error_update() -> None:
    sock = _DummySocket()
    ack = BinaryNode(
        tag="ack",
        attrs={"class": "message", "from": "chat@s.whatsapp.net", "id": "ACK1", "error": "500"},
    )

    await MessagesRecvSocket._handle_ack_node(sock, ack)  # type: ignore[arg-type]
    updates = [payload for event, payload in sock.ev.events if event == "messages.update"]
    assert updates
    assert updates[0][0]["key"]["id"] == "ACK1"
    assert updates[0][0]["update"]["status"] == 0
    assert updates[0][0]["update"]["messageStubParameters"] == ["500"]
