from __future__ import annotations

import asyncio
import base64
from types import MethodType, SimpleNamespace
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest

import wassupweb.socket.socket as core_socket_mod
from wassupweb.defaults import INITIAL_PREKEY_COUNT, MIN_PREKEY_COUNT, S_WHATSAPP_NET
from wassupweb.socket.socket import CoreSocket
from wassupweb.wabinary import BinaryNode


class _Logger:
    def debug(self, *_args: object, **_kwargs: object) -> None:
        return

    def info(self, *_args: object, **_kwargs: object) -> None:
        return

    def warning(self, *_args: object, **_kwargs: object) -> None:
        return

    def error(self, *_args: object, **_kwargs: object) -> None:
        return


class _CapturingLogger(_Logger):
    def __init__(self) -> None:
        self.warnings: list[tuple[tuple[object, ...], dict[str, object]]] = []
        self.infos: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def warning(self, *args: object, **kwargs: object) -> None:
        self.warnings.append((args, kwargs))

    def info(self, *args: object, **kwargs: object) -> None:
        self.infos.append((args, kwargs))


class _EventBus:
    def __init__(self) -> None:
        self.events: list[tuple[str, Any]] = []

    async def emit(self, event: str, payload: Any) -> None:
        self.events.append((event, payload))


@pytest.mark.asyncio
async def test_await_next_message_sends_and_waits_for_frame() -> None:
    class _Harness:
        def __init__(self) -> None:
            self.config = SimpleNamespace(connect_timeout_ms=123)
            self.sent: list[bytes] = []
            self.waited: list[tuple[str, int | None]] = []

        def _is_transport_open(self) -> bool:
            return True

        async def send_raw_message(self, payload: bytes) -> None:
            self.sent.append(payload)

        async def wait_for(self, event: str, predicate: Any = None, timeout_ms: int | None = None) -> bytes:
            _ = predicate
            self.waited.append((event, timeout_ms))
            return b"frame-bytes"

    obj = _Harness()
    result = await CoreSocket.await_next_message(obj, b"hello")

    assert result == b"frame-bytes"
    assert obj.sent == [b"hello"]
    assert obj.waited == [("frame", 123)]


@pytest.mark.asyncio
async def test_await_next_message_raises_when_transport_closed() -> None:
    class _Harness:
        config = SimpleNamespace(connect_timeout_ms=1)

        def _is_transport_open(self) -> bool:
            return False

    with pytest.raises(RuntimeError, match="Connection Closed"):
        await CoreSocket.await_next_message(_Harness())


@pytest.mark.asyncio
async def test_dispatch_frame_skips_binary_decode_during_pre_transport_handshake() -> None:
    class _Harness:
        def __init__(self) -> None:
            self._ev = _EventBus()
            self._noise = SimpleNamespace(_transport=None)
            self._logger = _Logger()
            self._in_handshake = True
            self.dispatched: list[Any] = []

        async def _dispatch_node(self, node: Any) -> None:
            self.dispatched.append(node)

    obj = _Harness()
    await CoreSocket._dispatch_frame(obj, b"\x08\x96\x01")  # type: ignore[arg-type]
    assert ("frame", b"\x08\x96\x01") in obj._ev.events
    assert obj.dispatched == []


@pytest.mark.asyncio
async def test_dispatch_frame_attempts_decode_when_not_in_handshake() -> None:
    class _Harness:
        def __init__(self) -> None:
            self._ev = _EventBus()
            self._noise = SimpleNamespace(_transport=None)
            self._logger = _Logger()
            self._in_handshake = False
            self.dispatched: list[Any] = []

        async def _dispatch_node(self, node: Any) -> None:
            self.dispatched.append(node)

    obj = _Harness()
    await CoreSocket._dispatch_frame(obj, b"\x08\x96\x01")  # type: ignore[arg-type]
    # invalid binary node payload should trigger error event instead of silent skip
    assert any(name == "error" for name, _payload in obj._ev.events)


@pytest.mark.asyncio
async def test_connect_rejects_mobile_config() -> None:
    class _Harness:
        def __init__(self) -> None:
            self.config = SimpleNamespace(mobile=True, wa_websocket_url="wss://web.whatsapp.com/ws/chat", print_qr_in_terminal=False)
            self._logger = _CapturingLogger()
            self._plugins: list[Any] = []

    with pytest.raises(RuntimeError, match="Mobile API is not supported anymore"):
        await CoreSocket.connect(_Harness())


@pytest.mark.asyncio
async def test_connect_rejects_tcp_protocol_url() -> None:
    class _Harness:
        def __init__(self) -> None:
            self.config = SimpleNamespace(mobile=False, wa_websocket_url="tcp://localhost:5222", print_qr_in_terminal=False)
            self._logger = _CapturingLogger()
            self._plugins: list[Any] = []

    with pytest.raises(RuntimeError, match="Mobile API is not supported anymore"):
        await CoreSocket.connect(_Harness())


@pytest.mark.asyncio
async def test_connect_warns_for_deprecated_print_qr_flag() -> None:
    class _Transport:
        async def connect(self) -> None:
            raise RuntimeError("stop-connect")

    class _Harness:
        def __init__(self) -> None:
            self.config = SimpleNamespace(
                mobile=False,
                wa_websocket_url="wss://web.whatsapp.com/ws/chat",
                print_qr_in_terminal=True,
            )
            self._logger = _CapturingLogger()
            self._plugins: list[Any] = []
            self.transport = _Transport()

    obj = _Harness()
    obj._apply_routing_info_to_transport_url = MethodType(CoreSocket._apply_routing_info_to_transport_url, obj)
    with pytest.raises(RuntimeError, match="stop-connect"):
        await CoreSocket.connect(obj)
    assert obj._logger.warnings
    assert "printQRInTerminal" in str(obj._logger.warnings[0][0][0])


@pytest.mark.asyncio
async def test_connect_warns_when_all_history_sync_types_are_disabled() -> None:
    class _Transport:
        async def connect(self) -> None:
            raise RuntimeError("stop-connect")

    class _Harness:
        def __init__(self) -> None:
            self.config = SimpleNamespace(
                mobile=False,
                wa_websocket_url="wss://web.whatsapp.com/ws/chat",
                print_qr_in_terminal=False,
                should_sync_history_message=lambda _msg: False,
            )
            self._logger = _CapturingLogger()
            self._plugins: list[Any] = []
            self.transport = _Transport()

    obj = _Harness()
    obj._apply_routing_info_to_transport_url = MethodType(CoreSocket._apply_routing_info_to_transport_url, obj)
    with pytest.raises(RuntimeError, match="stop-connect"):
        await CoreSocket.connect(obj)
    assert any("DANGER" in str(args[0]) for args, _kwargs in obj._logger.warnings)


@pytest.mark.asyncio
async def test_connect_appends_routing_info_ed_query_param_on_wss_transport() -> None:
    class _Transport:
        def __init__(self) -> None:
            self._url = "wss://web.whatsapp.com/ws/chat?foo=bar"

        async def connect(self) -> None:
            raise RuntimeError("stop-connect")

    routing_info = b"routing-bytes"

    class _Harness:
        def __init__(self) -> None:
            self.config = SimpleNamespace(
                mobile=False,
                wa_websocket_url="wss://web.whatsapp.com/ws/chat",
                print_qr_in_terminal=False,
                should_sync_history_message=lambda _msg: True,
                auth=SimpleNamespace(creds=SimpleNamespace(routing_info=routing_info)),
            )
            self._logger = _CapturingLogger()
            self._plugins: list[Any] = []
            self.transport = _Transport()

    obj = _Harness()
    obj._apply_routing_info_to_transport_url = MethodType(CoreSocket._apply_routing_info_to_transport_url, obj)
    with pytest.raises(RuntimeError, match="stop-connect"):
        await CoreSocket.connect(obj)

    parsed = urlparse(obj.transport._url)
    query = parse_qs(parsed.query)
    assert "ED" in query
    expected = base64.urlsafe_b64encode(routing_info).rstrip(b"=").decode("ascii")
    assert query["ED"] == [expected]


@pytest.mark.asyncio
async def test_send_raw_message_uses_noise_frame_encoder() -> None:
    class _Noise:
        def encode_frame(self, payload: bytes) -> bytes:
            return b"enc:" + payload

    class _Transport:
        def __init__(self) -> None:
            self.payload: bytes | None = None

        async def send(self, payload: bytes) -> None:
            self.payload = payload

    obj = SimpleNamespace(_noise=_Noise(), transport=_Transport())
    await CoreSocket.send_raw_message(obj, b"abc")

    assert obj.transport.payload == b"enc:abc"


@pytest.mark.asyncio
async def test_verify_current_prekey_exists() -> None:
    class _KeyStore:
        async def get(self, _key_type: str, ids: list[str]) -> dict[str, Any]:
            if "8" in ids:
                return {"8": {"public": b"k"}}
            return {}

    class _Harness:
        def __init__(self, next_pre_key_id: int) -> None:
            self._auth = SimpleNamespace(creds=SimpleNamespace(next_pre_key_id=next_pre_key_id), keys=_KeyStore())

        def _require_auth(self) -> Any:
            return self._auth

    empty = await CoreSocket.verify_current_pre_key_exists(_Harness(1))
    assert empty == {"exists": False, "currentPreKeyId": 0}

    present = await CoreSocket.verify_current_pre_key_exists(_Harness(9))
    assert present == {"exists": True, "currentPreKeyId": 8}


@pytest.mark.asyncio
async def test_upload_prekeys_if_required_uses_initial_threshold_when_server_empty() -> None:
    class _Harness:
        def __init__(self) -> None:
            self._logger = _Logger()
            self.uploaded: list[int] = []

        async def get_available_prekeys_on_server(self) -> int:
            return 0

        async def verify_current_pre_key_exists(self) -> dict[str, Any]:
            return {"exists": True, "currentPreKeyId": 1}

        async def upload_pre_keys(self, count: int) -> None:
            self.uploaded.append(count)

    obj = _Harness()
    await CoreSocket.upload_pre_keys_to_server_if_required(obj)
    assert obj.uploaded == [INITIAL_PREKEY_COUNT]


@pytest.mark.asyncio
async def test_upload_prekeys_if_required_uploads_when_current_missing() -> None:
    class _Harness:
        def __init__(self) -> None:
            self._logger = _Logger()
            self.uploaded: list[int] = []

        async def get_available_prekeys_on_server(self) -> int:
            return 500

        async def verify_current_pre_key_exists(self) -> dict[str, Any]:
            return {"exists": False, "currentPreKeyId": 99}

        async def upload_pre_keys(self, count: int) -> None:
            self.uploaded.append(count)

    obj = _Harness()
    await CoreSocket.upload_pre_keys_to_server_if_required(obj)
    assert obj.uploaded == [MIN_PREKEY_COUNT]


@pytest.mark.asyncio
async def test_request_pairing_code_validates_custom_code_length() -> None:
    class _Harness:
        def _require_auth(self) -> Any:
            return SimpleNamespace(creds=SimpleNamespace())

    with pytest.raises(ValueError, match="exactly 8"):
        await CoreSocket.request_pairing_code(_Harness(), "15550001111", custom_pairing_code="SHORT")


@pytest.mark.asyncio
async def test_request_pairing_code_builds_pairing_registration_node() -> None:
    class _Harness:
        def __init__(self) -> None:
            creds = SimpleNamespace(
                pairing_code=None,
                me=None,
                noise_key=SimpleNamespace(public=b"noise"),
                pairing_ephemeral_key_pair=SimpleNamespace(public=b"ephemeral"),
            )
            self._auth = SimpleNamespace(creds=creds)
            self.config = SimpleNamespace(browser=("Mac OS", "Chrome", "14.4.1"))
            self.ev = _EventBus()
            self.sent: list[BinaryNode] = []

        def _require_auth(self) -> Any:
            return self._auth

        def generate_message_tag(self) -> str:
            return "tag-1"

        async def generate_pairing_key(self) -> bytes:
            return b"k" * 32

        async def send_node(self, node: BinaryNode) -> None:
            self.sent.append(node)

    obj = _Harness()
    code = await CoreSocket.request_pairing_code(obj, "15550001111", custom_pairing_code="ABCDEFGH")

    assert code == "ABCDEFGH"
    assert obj._auth.creds.me == {"id": "15550001111@s.whatsapp.net", "name": "~"}
    assert ("creds.update", {"pairingCode": "ABCDEFGH", "me": obj._auth.creds.me}) in obj.ev.events

    assert obj.sent
    node = obj.sent[0]
    assert node.tag == "iq"
    assert node.attrs["to"] == S_WHATSAPP_NET
    assert node.attrs["xmlns"] == "md"

    reg = node.content[0]
    assert reg.tag == "link_code_companion_reg"
    assert reg.attrs["jid"] == "15550001111@s.whatsapp.net"
    tags = [child.tag for child in reg.content]
    assert "link_code_pairing_wrapped_companion_ephemeral_pub" in tags
    assert "companion_server_auth_key_pub" in tags
    assert "companion_platform_id" in tags


@pytest.mark.asyncio
async def test_send_wam_buffer_builds_stats_iq() -> None:
    class _Harness:
        def __init__(self) -> None:
            self.sent: BinaryNode | None = None

        def generate_message_tag(self) -> str:
            return "tag-2"

        async def query_node(self, node: BinaryNode) -> BinaryNode:
            self.sent = node
            return node

    obj = _Harness()
    result = await CoreSocket.send_wam_buffer(obj, b"payload")

    assert obj.sent is not None
    assert result == obj.sent
    assert obj.sent.tag == "iq"
    assert obj.sent.attrs["xmlns"] == "w:stats"
    add = obj.sent.content[0]
    assert add.tag == "add"
    assert add.content == b"payload"
    assert add.attrs["t"].isdigit()


@pytest.mark.asyncio
async def test_emit_cb_node_events_emits_tag_and_callback_patterns() -> None:
    class _Bus:
        def __init__(self) -> None:
            self.events: list[tuple[str, Any]] = []

        async def emit(self, event: str, payload: Any) -> None:
            self.events.append((event, payload))

    obj = SimpleNamespace(_ev=_Bus())
    node = BinaryNode(
        tag="iq",
        attrs={"id": "abc", "type": "set"},
        content=[BinaryNode(tag="pair-device", attrs={})],
    )
    await CoreSocket._emit_cb_node_events(obj, node)
    names = [name for name, _ in obj._ev.events]
    assert "TAG:abc" in names
    assert "CB:iq,type:set,pair-device" in names
    assert "CB:iq,type:set" in names
    assert "CB:iq,type" in names
    assert "CB:iq,,pair-device" in names
    assert "CB:iq" in names


@pytest.mark.asyncio
async def test_handle_ib_node_offline_flushes_buffer_and_marks_pending_notifications() -> None:
    class _Bus:
        def __init__(self) -> None:
            self.events: list[tuple[str, Any]] = []
            self.flushed = False

        async def emit(self, event: str, payload: Any) -> None:
            self.events.append((event, payload))

        def flush(self) -> bool:
            self.flushed = True
            return True

    bus = _Bus()
    obj = SimpleNamespace(ev=bus, _did_start_buffer=True, _logger=_Logger(), config=SimpleNamespace(auth=None))
    node = BinaryNode(
        tag="ib",
        attrs={},
        content=[BinaryNode(tag="offline", attrs={"count": "7"})],
    )
    await CoreSocket._handle_ib_node(obj, node)
    assert bus.flushed is True
    assert ("connection.update", {"receivedPendingNotifications": True}) in bus.events


@pytest.mark.asyncio
async def test_handle_success_node_updates_lid_and_emits_open() -> None:
    class _Bus:
        def __init__(self) -> None:
            self.events: list[tuple[str, Any]] = []

        async def emit(self, event: str, payload: Any) -> None:
            self.events.append((event, payload))

    creds = SimpleNamespace(me={"id": "111@s.whatsapp.net", "name": "Me"}, routing_info=None)
    auth = SimpleNamespace(creds=creds)
    obj = SimpleNamespace(
        ev=_Bus(),
        config=SimpleNamespace(auth=auth),
        _logger=_Logger(),
    )
    obj.update_server_time_offset = MethodType(CoreSocket.update_server_time_offset, obj)
    obj._cancel_qr_task = lambda: None

    async def _noop(*_args: Any, **_kwargs: Any) -> None:
        return

    obj.upload_pre_keys_to_server_if_required = _noop
    obj.send_passive_iq = _noop
    obj.digest_key_bundle = _noop
    obj.send_unified_session = _noop

    node = BinaryNode(tag="success", attrs={"lid": "me@lid", "t": "10"})
    await CoreSocket._handle_success_node(obj, node)

    assert ("creds.update", {"me": {"id": "111@s.whatsapp.net", "name": "Me", "lid": "me@lid"}}) in obj.ev.events
    assert ("connection.update", {"connection": "open"}) in obj.ev.events


@pytest.mark.asyncio
async def test_disconnect_emits_close_for_explicit_disconnect() -> None:
    class _Transport:
        def __init__(self) -> None:
            self.disconnected = False

        async def disconnect(self) -> None:
            self.disconnected = True

    class _Bus:
        def __init__(self) -> None:
            self.events: list[tuple[str, Any]] = []

        async def emit(self, event: str, payload: Any) -> None:
            self.events.append((event, payload))

    obj = SimpleNamespace(
        _closed=False,
        _stop_keep_alive_request=lambda: None,
        _cancel_qr_task=lambda: None,
        _recv_task=None,
        transport=_Transport(),
        ev=_Bus(),
    )

    await CoreSocket.disconnect(obj)

    assert obj.transport.disconnected is True
    assert ("connection.update", {"connection": "close"}) in obj.ev.events


@pytest.mark.asyncio
async def test_disconnect_skips_simple_close_emit_when_end_path_marks_closed() -> None:
    class _Transport:
        async def disconnect(self) -> None:
            return

    class _Bus:
        def __init__(self) -> None:
            self.events: list[tuple[str, Any]] = []

        async def emit(self, event: str, payload: Any) -> None:
            self.events.append((event, payload))

    obj = SimpleNamespace(
        _closed=True,
        _stop_keep_alive_request=lambda: None,
        _cancel_qr_task=lambda: None,
        _recv_task=None,
        transport=_Transport(),
        ev=_Bus(),
    )

    await CoreSocket.disconnect(obj)
    assert obj.ev.events == []


@pytest.mark.asyncio
async def test_recv_loop_ends_connection_when_transport_is_closed() -> None:
    class _Transport:
        async def recv(self) -> bytes:
            raise RuntimeError("transport is not connected")

    class _Bus:
        def __init__(self) -> None:
            self.events: list[tuple[str, Any]] = []

        async def emit(self, event: str, payload: Any) -> None:
            self.events.append((event, payload))

    class _Harness:
        def __init__(self) -> None:
            self._logger = _Logger()
            self._plugins: list[Any] = []
            self._ev = _Bus()
            self.transport = _Transport()
            self.ended_with: Exception | None = None

        def _is_transport_open(self) -> bool:
            return False

        async def end(self, error: Exception | None = None) -> None:
            self.ended_with = error

    obj = _Harness()
    await CoreSocket._recv_loop(obj)
    assert isinstance(obj.ended_with, RuntimeError)
    assert any(event == "error" for event, _ in obj._ev.events)


@pytest.mark.asyncio
async def test_wait_for_message_returns_none_on_timeout() -> None:
    logger = _CapturingLogger()

    class _Harness:
        def __init__(self) -> None:
            self._logger = logger

        async def wait_for(self, _event: str, predicate: Any = None, timeout_ms: int | None = None) -> BinaryNode:
            _ = predicate
            _ = timeout_ms
            raise asyncio.TimeoutError

    result = await CoreSocket.wait_for_message(_Harness(), "msg-1", timeout_ms=3)
    assert result is None
    assert logger.warnings
    assert logger.warnings[0][1]["extra"] == {"msgId": "msg-1"}


@pytest.mark.asyncio
async def test_wait_for_socket_open_raises_if_transport_is_already_closed() -> None:
    class _Bus:
        def on(self, _event: str, _handler: Any) -> None:
            return

        def off(self, _event: str, _handler: Any) -> None:
            return

    obj = SimpleNamespace(
        transport=SimpleNamespace(is_open=False, _ws=SimpleNamespace(closed=True)),
        config=SimpleNamespace(connect_timeout_ms=20),
        ev=_Bus(),
    )
    obj._is_transport_open = MethodType(CoreSocket._is_transport_open, obj)
    obj._is_transport_closed_or_closing = MethodType(CoreSocket._is_transport_closed_or_closing, obj)

    with pytest.raises(RuntimeError, match="Connection Closed"):
        await CoreSocket.wait_for_socket_open(obj)


def test_is_transport_open_returns_false_when_callable_state_raises() -> None:
    class _Transport:
        def is_open(self) -> bool:
            raise RuntimeError("boom")

    obj = SimpleNamespace(transport=_Transport(), _recv_task=None)
    assert CoreSocket._is_transport_open(obj) is False


def test_is_transport_open_uses_ws_state_enum_name() -> None:
    class _Ws:
        state = SimpleNamespace(name="OPEN")

    obj = SimpleNamespace(transport=SimpleNamespace(_ws=_Ws()), _recv_task=None)
    assert CoreSocket._is_transport_open(obj) is True


def test_is_transport_closed_or_closing_uses_ws_state_enum_name() -> None:
    class _Ws:
        state = SimpleNamespace(name="CLOSING")
        close_code = None

    obj = SimpleNamespace(transport=SimpleNamespace(_ws=_Ws()))
    assert CoreSocket._is_transport_closed_or_closing(obj) is True


@pytest.mark.asyncio
async def test_handle_success_node_schedules_own_lid_mapping_migration(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Bus:
        def __init__(self) -> None:
            self.events: list[tuple[str, Any]] = []

        async def emit(self, event: str, payload: Any) -> None:
            self.events.append((event, payload))

    class _Keys:
        def __init__(self) -> None:
            self.payloads: list[dict[str, Any]] = []

        async def set(self, payload: dict[str, Any]) -> None:
            self.payloads.append(payload)

    class _LidStore:
        def __init__(self) -> None:
            self.mappings: list[list[dict[str, str]]] = []

        async def store_lid_pn_mappings(self, pairs: list[dict[str, str]]) -> None:
            self.mappings.append(pairs)

    class _Repo:
        def __init__(self) -> None:
            self.lid_mapping = _LidStore()
            self.migrations: list[tuple[str, str]] = []

        async def migrate_session(self, from_jid: str, to_jid: str) -> None:
            self.migrations.append((from_jid, to_jid))

    logger = _CapturingLogger()
    keys = _Keys()
    repo = _Repo()
    auth = SimpleNamespace(creds=SimpleNamespace(me={"id": "111@s.whatsapp.net", "name": "Me"}), keys=keys)
    obj = SimpleNamespace(
        ev=_Bus(),
        config=SimpleNamespace(auth=auth),
        _logger=logger,
        _signal_repository=repo,
    )
    obj._me_id = MethodType(CoreSocket._me_id, obj)
    obj._resolve_signal_repository = MethodType(CoreSocket._resolve_signal_repository, obj)
    obj._schedule_own_lid_session_migration = MethodType(CoreSocket._schedule_own_lid_session_migration, obj)
    obj.update_server_time_offset = MethodType(CoreSocket.update_server_time_offset, obj)
    obj._cancel_qr_task = lambda: None
    obj._server_time_offset_ms = 0

    async def _noop(*_args: Any, **_kwargs: Any) -> None:
        return

    obj.upload_pre_keys_to_server_if_required = _noop
    obj.send_passive_iq = _noop
    obj.digest_key_bundle = _noop
    obj.send_unified_session = _noop

    scheduled: list[asyncio.Task[Any]] = []
    real_create_task = asyncio.create_task

    def _capture_task(coro: Any) -> asyncio.Task[Any]:
        task = real_create_task(coro)
        scheduled.append(task)
        return task

    monkeypatch.setattr(core_socket_mod.asyncio, "create_task", _capture_task)

    node = BinaryNode(tag="success", attrs={"lid": "me@lid", "t": "10"})
    await CoreSocket._handle_success_node(obj, node)
    if scheduled:
        await asyncio.gather(*scheduled)

    assert repo.lid_mapping.mappings == [[{"lid": "me@lid", "pn": "111@s.whatsapp.net"}]]
    assert keys.payloads == [{"device-list": {"111": ["0"]}}]
    assert repo.migrations == [("111@s.whatsapp.net", "me@lid")]
