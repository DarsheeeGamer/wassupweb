from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import BaseModel

import wassupweb.socket.messages_send as messages_send_mod
from wassupweb.defaults import WA_DEFAULT_EPHEMERAL
from wassupweb.socket.messages_send import MessagesSendSocket, _ExpiringMap
from wassupweb.types.identity import JidKind
from wassupweb.utils.make_mutex import make_keyed_mutex
from wassupweb.wabinary import BinaryNode


class _ReceiptSocket:
    def __init__(self) -> None:
        self.sent: list[BinaryNode] = []

    def resolve_chat_jid(self, jid: str | None) -> str:
        return jid or ""

    async def send_node(self, node: BinaryNode) -> None:
        self.sent.append(node)


class _Keys:
    def __init__(self) -> None:
        self._state: dict[str, dict[str, Any]] = {
            "sender-key-memory": {},
            "tctoken": {},
            "device-list": {},
        }
        self.set_calls: list[dict[str, Any]] = []

    async def get(self, key_type: str, ids: list[str]) -> dict[str, Any]:
        store = self._state.setdefault(key_type, {})
        return {item_id: store[item_id] for item_id in ids if item_id in store}

    async def set(self, data: dict[str, dict[str, Any]]) -> None:
        self.set_calls.append(data)
        for key_type, mapping in data.items():
            store = self._state.setdefault(key_type, {})
            for item_id, value in mapping.items():
                if value is None:
                    store.pop(item_id, None)
                else:
                    store[item_id] = value


class _LidStore:
    def __init__(self) -> None:
        self.stored: list[list[dict[str, str]]] = []

    async def store_lid_pn_mappings(self, pairs: list[dict[str, str]]) -> None:
        self.stored.append(pairs)

    async def get_lids_for_pns(self, _pns: list[str]) -> list[dict[str, str]]:
        return []


class _LidStoreCamel:
    def __init__(self) -> None:
        self.stored: list[list[dict[str, str]]] = []

    async def storeLIDPNMappings(self, pairs: list[dict[str, str]]) -> None:  # noqa: N802 - parity surface
        self.stored.append(pairs)

    async def getLIDsForPNs(self, pns: list[str]) -> list[dict[str, str]]:  # noqa: N802 - parity surface
        if not pns:
            return []
        return [{"pn": pns[0], "lid": "123@lid"}]


class _Repo:
    def __init__(self) -> None:
        self.validate_calls: list[str] = []
        self.encrypt_calls: list[str] = []
        self.lid_mapping = _LidStore()

    async def validate_session(self, jid: str) -> dict[str, Any]:
        self.validate_calls.append(jid)
        return {"exists": False}

    def jid_to_signal_protocol_address(self, jid: str) -> str:
        return f"sig:{jid}"

    async def encrypt_message(self, opts: dict[str, Any]) -> dict[str, Any]:
        jid = str(opts["jid"])
        self.encrypt_calls.append(jid)
        return {"type": "msg", "ciphertext": f"enc:{jid}".encode("utf-8")}

    async def encrypt_group_message(self, _opts: dict[str, Any]) -> dict[str, Any]:
        return {"ciphertext": b"group", "senderKeyDistributionMessage": b"dsm"}


class _UserDevicesCache:
    def __init__(self) -> None:
        self.store: dict[str, Any] = {}
        self.mget_calls: list[list[str]] = []
        self.mset_calls: list[list[dict[str, Any]]] = []

    async def mget(self, keys: list[str]) -> dict[str, Any]:
        self.mget_calls.append(keys)
        return {key: self.store.get(key) for key in keys}

    async def mset(self, entries: list[dict[str, Any]]) -> None:
        self.mset_calls.append(entries)
        for entry in entries:
            self.store[entry["key"]] = entry["value"]


class _SendHarness(MessagesSendSocket):
    def __init__(self) -> None:
        self._keys = _Keys()
        self._repo = _Repo()
        self._signal_repository = self._repo
        self._media_conn = None
        self._user_devices_cache = _ExpiringMap(300)
        self._peer_sessions_cache = _ExpiringMap(300)
        self._encryption_mutex = make_keyed_mutex()
        self.sent_nodes: list[BinaryNode] = []
        self.queries: list[BinaryNode] = []
        self._wait_payload: Any = []
        self.emitted: list[tuple[str, Any]] = []
        self._ev = SimpleNamespace(emit=self._emit)
        self.config = SimpleNamespace(
            auth=SimpleNamespace(
                creds=SimpleNamespace(
                    me={"id": "me@s.whatsapp.net", "lid": "me@lid"},
                    account={"details": b"d", "accountSignatureKey": b"k", "deviceSignature": b"s"},
                ),
                keys=self._keys,
            ),
            patch_message_before_sending=lambda msg, _r=None: msg,
            cached_group_metadata=None,
            emit_own_events=False,
            make_signal_repository=None,
            default_query_timeout_ms=1000,
            user_devices_cache=None,
        )
        self._logger = SimpleNamespace(
            debug=lambda *_a, **_k: None,
            info=lambda *_a, **_k: None,
            warning=lambda *_a, **_k: None,
        )

    def resolve_chat_jid(self, jid: str | None) -> str:
        return jid or ""

    def _require_auth(self) -> Any:
        return self.config.auth

    async def send_node(self, node: BinaryNode) -> None:
        self.sent_nodes.append(node)

    async def query_node(self, node: BinaryNode, timeout_ms: int | None = None) -> BinaryNode:
        _ = timeout_ms
        self.queries.append(node)
        return BinaryNode(tag="iq", attrs={"type": "result"}, content=[])

    async def _emit(self, event: str, payload: Any) -> None:
        self.emitted.append((event, payload))

    async def wait_for(self, _event: str, predicate: Any = None, timeout_ms: int | None = None) -> Any:
        _ = timeout_ms
        payload = self._wait_payload
        if predicate:
            allowed = predicate(payload)
            if asyncio.iscoroutine(allowed):
                allowed = await allowed
            if not allowed:
                raise RuntimeError("predicate did not match")
        return payload


@pytest.mark.asyncio
async def test_send_receipt_builds_list_and_read_timestamp() -> None:
    sock = _ReceiptSocket()

    node = await MessagesSendSocket.send_receipt(
        sock,  # type: ignore[arg-type]
        jid="12345@g.us",
        participant="55119999@s.whatsapp.net",
        message_ids=["A", "B", "C"],
        receipt_type="read",
    )

    assert node.attrs["id"] == "A"
    assert node.attrs["to"] == "12345@g.us"
    assert node.attrs["participant"] == "55119999@s.whatsapp.net"
    assert node.attrs["type"] == "read"
    assert "t" in node.attrs
    assert len(sock.sent) == 1
    assert isinstance(node.content, list)
    list_node = node.content[0]
    assert isinstance(list_node, BinaryNode)
    assert list_node.tag == "list"
    assert [item.attrs["id"] for item in list_node.content] == ["B", "C"]


@pytest.mark.asyncio
async def test_send_receipt_sender_type_uses_recipient_for_pn_or_lid() -> None:
    sock = _ReceiptSocket()

    node = await MessagesSendSocket.send_receipt(
        sock,  # type: ignore[arg-type]
        jid="551188877766@s.whatsapp.net",
        participant="123456789@s.whatsapp.net",
        message_ids=["X1"],
        receipt_type="sender",
    )

    assert node.attrs["recipient"] == "551188877766@s.whatsapp.net"
    assert node.attrs["to"] == "123456789@s.whatsapp.net"
    assert "participant" not in node.attrs


@pytest.mark.asyncio
async def test_send_receipts_aggregates_keys_and_ignores_from_me() -> None:
    calls: list[dict[str, Any]] = []

    class _Sock:
        async def send_receipt(
            self,
            jid: str,
            participant: str | None,
            message_ids: list[str],
            receipt_type: str = "read",
        ) -> BinaryNode:
            calls.append(
                {
                    "jid": jid,
                    "participant": participant,
                    "message_ids": message_ids,
                    "receipt_type": receipt_type,
                }
            )
            return BinaryNode(tag="receipt", attrs={"id": message_ids[0] if message_ids else ""})

    keys = [
        {"remoteJid": "111@g.us", "id": "a", "fromMe": False, "participant": "1@s.whatsapp.net"},
        {"remoteJid": "111@g.us", "id": "b", "fromMe": False, "participant": "1@s.whatsapp.net"},
        {"remoteJid": "111@g.us", "id": "c", "fromMe": True, "participant": "1@s.whatsapp.net"},
    ]
    await MessagesSendSocket.send_receipts(_Sock(), keys, "read")  # type: ignore[arg-type]

    assert calls == [
        {
            "jid": "111@g.us",
            "participant": "1@s.whatsapp.net",
            "message_ids": ["a", "b"],
            "receipt_type": "read",
        }
    ]


@pytest.mark.asyncio
async def test_read_messages_uses_privacy_setting_to_pick_read_type() -> None:
    sent_types: list[str] = []

    class _Sock:
        async def fetch_privacy_settings(self) -> dict[str, Any]:
            return {"readreceipts": "all"}

        async def send_receipts(self, _keys: list[dict[str, Any]], receipt_type: str) -> None:
            sent_types.append(receipt_type)

    await MessagesSendSocket.read_messages(_Sock(), [{"id": "x", "remoteJid": "a@g.us", "fromMe": False}])  # type: ignore[arg-type]
    assert sent_types == ["read"]

    sent_types.clear()

    class _SockNoAll:
        async def fetch_privacy_settings(self) -> dict[str, Any]:
            return {"readreceipts": "none"}

        async def send_receipts(self, _keys: list[dict[str, Any]], receipt_type: str) -> None:
            sent_types.append(receipt_type)

    await MessagesSendSocket.read_messages(  # type: ignore[arg-type]
        _SockNoAll(),
        [{"id": "x", "remoteJid": "a@g.us", "fromMe": False}],
    )
    assert sent_types == ["read-self"]


@pytest.mark.asyncio
async def test_send_receipt_rejects_empty_message_ids() -> None:
    sock = _ReceiptSocket()
    with pytest.raises(ValueError, match="missing ids in receipt"):
        await MessagesSendSocket.send_receipt(sock, "a@g.us", None, [], "read")  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_assert_sessions_fetches_once_and_then_uses_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    sock = _SendHarness()
    parse_calls: list[BinaryNode] = []

    async def _fake_parse(node: BinaryNode, _repo: Any) -> None:
        parse_calls.append(node)

    monkeypatch.setattr(messages_send_mod, "parse_and_inject_e2e_sessions", _fake_parse)

    did_fetch = await MessagesSendSocket.assert_sessions(sock, ["111@s.whatsapp.net"])  # type: ignore[arg-type]
    assert did_fetch is True
    assert len(sock.queries) == 1
    assert len(parse_calls) == 1

    did_fetch_again = await MessagesSendSocket.assert_sessions(sock, ["111@s.whatsapp.net"])  # type: ignore[arg-type]
    assert did_fetch_again is False
    assert len(sock.queries) == 1


@pytest.mark.asyncio
async def test_assert_sessions_supports_camel_case_repository_and_lid_mapping() -> None:
    sock = _SendHarness()

    class _RepoCamel:
        def __init__(self) -> None:
            self.lid_mapping = _LidStoreCamel()
            self.validated: list[str] = []
            self.signal_ids: list[str] = []

        async def validateSession(self, jid: str) -> dict[str, Any]:  # noqa: N802 - parity surface
            self.validated.append(jid)
            return {"exists": False}

        def jidToSignalProtocolAddress(self, jid: str) -> str:  # noqa: N802 - parity surface
            self.signal_ids.append(jid)
            return f"sig:{jid}"

    sock._signal_repository = _RepoCamel()

    did_fetch = await MessagesSendSocket.assert_sessions(sock, ["123@s.whatsapp.net"])  # type: ignore[arg-type]
    assert did_fetch is True
    assert len(sock.queries) == 1
    iq = sock.queries[0]
    key_node = iq.content[0]
    first_user = key_node.content[0]
    assert first_user.attrs["jid"] == "123@lid"


@pytest.mark.asyncio
async def test_get_usync_devices_extracts_and_persists_device_list(monkeypatch: pytest.MonkeyPatch) -> None:
    sock = _SendHarness()

    async def _fake_execute(_query: Any) -> Any:
        return SimpleNamespace(list=[{"id": "123@s.whatsapp.net", "data": {"devices": {"deviceList": []}}}])

    sock.execute_usync_query = _fake_execute  # type: ignore[attr-defined]

    def _fake_extract(_result: Any, _my_jid: str, _my_lid: str, _ignore_zero: bool) -> list[dict[str, Any]]:
        return [{"user": "123", "device": 1, "server": "s.whatsapp.net"}]

    monkeypatch.setattr(messages_send_mod, "extract_device_jids", _fake_extract)

    result = await MessagesSendSocket.get_usync_devices(  # type: ignore[arg-type]
        sock,
        ["123@s.whatsapp.net"],
        use_cache=False,
        ignore_zero_devices=False,
    )
    assert result and result[0]["jid"] == "123:1@s.whatsapp.net"
    assert sock._keys.set_calls[-1]["device-list"]["123"] == ["1"]


@pytest.mark.asyncio
async def test_get_usync_devices_supports_camel_case_lid_mapping_store(monkeypatch: pytest.MonkeyPatch) -> None:
    sock = _SendHarness()
    repo = _Repo()
    repo.lid_mapping = _LidStoreCamel()
    sock._signal_repository = repo
    asserted: list[tuple[list[str], bool]] = []

    async def _fake_assert(jids: list[str], force: bool = False) -> bool:
        asserted.append((jids, force))
        return True

    sock.assert_sessions = _fake_assert  # type: ignore[assignment]

    async def _fake_execute(_query: Any) -> Any:
        return SimpleNamespace(list=[{"id": "123@s.whatsapp.net", "data": {"lid": "123@lid", "devices": {"deviceList": []}}}])

    sock.execute_usync_query = _fake_execute  # type: ignore[attr-defined]

    def _fake_extract(_result: Any, _my_jid: str, _my_lid: str, _ignore_zero: bool) -> list[dict[str, Any]]:
        return [{"user": "123", "device": 1, "server": "s.whatsapp.net"}]

    monkeypatch.setattr(messages_send_mod, "extract_device_jids", _fake_extract)

    result = await MessagesSendSocket.get_usync_devices(  # type: ignore[arg-type]
        sock,
        ["123@s.whatsapp.net"],
        use_cache=False,
        ignore_zero_devices=False,
    )
    assert result
    assert repo.lid_mapping.stored == [[{"pn": "123@s.whatsapp.net", "lid": "123@lid"}]]
    assert asserted == [(["123@lid"], True)]


@pytest.mark.asyncio
async def test_get_usync_devices_uses_external_user_devices_cache() -> None:
    sock = _SendHarness()
    ext_cache = _UserDevicesCache()
    ext_cache.store["123"] = [{"user": "123", "server": "s.whatsapp.net", "device": 4}]
    sock.config.user_devices_cache = ext_cache

    result = await MessagesSendSocket.get_usync_devices(  # type: ignore[arg-type]
        sock,
        ["123@s.whatsapp.net"],
        use_cache=True,
        ignore_zero_devices=False,
    )
    assert result == [{"user": "123", "server": "s.whatsapp.net", "device": 4, "jid": "123:4@s.whatsapp.net"}]
    assert ext_cache.mget_calls == [["123"]]
    assert sock.queries == []


@pytest.mark.asyncio
async def test_relay_message_direct_chat_builds_participants_stanza() -> None:
    sock = _SendHarness()

    async def _fake_devices(_jids: list[str], _use_cache: bool = True, _ignore_zero: bool = False) -> list[dict[str, Any]]:
        return [
            {"user": "me", "device": 1, "jid": "me:1@s.whatsapp.net"},
            {"user": "111", "device": 1, "jid": "111:1@s.whatsapp.net"},
        ]

    async def _fake_assert(_jids: list[str], _force: bool = False) -> bool:
        return True

    sock.get_usync_devices = _fake_devices  # type: ignore[assignment]
    sock.assert_sessions = _fake_assert  # type: ignore[assignment]

    msg_id = await MessagesSendSocket.relay_message(  # type: ignore[arg-type]
        sock,
        jid="111@s.whatsapp.net",
        message={"conversation": "hello"},
        message_id="m.1",
    )

    assert msg_id == "m.1"
    assert len(sock.sent_nodes) == 1
    stanza = sock.sent_nodes[0]
    assert stanza.tag == "message"
    assert stanza.attrs["id"] == "m.1"
    assert stanza.attrs["to"] == "111@s.whatsapp.net"
    assert any(isinstance(item, BinaryNode) and item.tag == "participants" for item in stanza.content)


@pytest.mark.asyncio
async def test_relay_message_retry_sets_device_fanout_false_for_non_group() -> None:
    sock = _SendHarness()

    msg_id = await MessagesSendSocket.relay_message(  # type: ignore[arg-type]
        sock,
        jid="111@s.whatsapp.net",
        message={"conversation": "retry"},
        message_id="m.retry",
        participant={"jid": "111:1@s.whatsapp.net", "count": 1},
    )

    assert msg_id == "m.retry"
    stanza = sock.sent_nodes[-1]
    assert stanza.attrs["device_fanout"] == "false"
    assert stanza.attrs["to"] == "111:1@s.whatsapp.net"


@pytest.mark.asyncio
async def test_relay_message_retry_includes_device_identity_for_pydantic_account() -> None:
    class _Account(BaseModel):
        details: bytes
        accountSignatureKey: bytes
        deviceSignature: bytes

    sock = _SendHarness()
    sock.config.auth.creds.account = _Account(details=b"d", accountSignatureKey=b"k", deviceSignature=b"s")

    await MessagesSendSocket.relay_message(  # type: ignore[arg-type]
        sock,
        jid="111@s.whatsapp.net",
        message={"conversation": "retry"},
        message_id="m.retry.id",
        participant={"jid": "111:1@s.whatsapp.net", "count": 1},
    )
    stanza = sock.sent_nodes[-1]
    assert any(isinstance(item, BinaryNode) and item.tag == "device-identity" for item in stanza.content)


@pytest.mark.asyncio
async def test_relay_message_status_does_not_use_cached_group_metadata() -> None:
    sock = _SendHarness()
    called = {"count": 0}

    async def _cached(_jid: str) -> dict[str, Any]:
        called["count"] += 1
        return {"participants": [{"id": "111@s.whatsapp.net"}]}

    async def _fake_devices(_jids: list[str], _use_cache: bool = True, _ignore_zero: bool = False) -> list[dict[str, Any]]:
        return []

    sock.config.cached_group_metadata = _cached
    sock.get_usync_devices = _fake_devices  # type: ignore[assignment]

    await MessagesSendSocket.relay_message(  # type: ignore[arg-type]
        sock,
        jid="status@broadcast",
        message={"conversation": "status"},
        message_id="m.status",
        use_cached_group_metadata=True,
    )
    assert called["count"] == 0


@pytest.mark.asyncio
async def test_relay_message_honors_use_user_devices_cache_flag_for_direct_chat() -> None:
    sock = _SendHarness()
    observed: list[bool] = []

    async def _fake_devices(_jids: list[str], use_cache: bool = True, _ignore_zero: bool = False) -> list[dict[str, Any]]:
        observed.append(use_cache)
        return []

    async def _fake_assert(_jids: list[str], _force: bool = False) -> bool:
        return True

    sock.get_usync_devices = _fake_devices  # type: ignore[assignment]
    sock.assert_sessions = _fake_assert  # type: ignore[assignment]

    await MessagesSendSocket.relay_message(  # type: ignore[arg-type]
        sock,
        jid="111@s.whatsapp.net",
        message={"conversation": "cache-control"},
        message_id="m.cache",
        use_user_devices_cache=False,
    )
    assert observed and observed[-1] is False


@pytest.mark.asyncio
async def test_send_message_sets_delete_edit_attribute_for_group_admin_delete() -> None:
    sock = _SendHarness()
    called: dict[str, Any] = {}

    async def _fake_relay(
        _jid: str,
        _message: dict[str, Any],
        message_id: str | None = None,
        participant: dict[str, Any] | None = None,
        additional_attributes: dict[str, str] | None = None,
        additional_nodes: list[BinaryNode] | None = None,
        use_user_devices_cache: bool = True,
        use_cached_group_metadata: bool = True,
        status_jid_list: list[str] | None = None,
    ) -> str:
        called["message_id"] = message_id
        called["participant"] = participant
        called["additional_attributes"] = additional_attributes
        called["additional_nodes"] = additional_nodes
        called["use_user_devices_cache"] = use_user_devices_cache
        called["use_cached_group_metadata"] = use_cached_group_metadata
        called["status_jid_list"] = status_jid_list
        return "ok"

    sock.relay_message = _fake_relay  # type: ignore[assignment]
    result = await MessagesSendSocket.send_message(  # type: ignore[arg-type]
        sock,
        "111@s.whatsapp.net",
        {"delete": {"remoteJid": "123@g.us", "fromMe": False, "id": "x"}},
    )

    assert result is not None
    assert called["additional_attributes"]["edit"] == "8"


@pytest.mark.asyncio
async def test_send_message_group_disappearing_routes_to_group_toggle() -> None:
    sock = _SendHarness()
    captured: dict[str, Any] = {}

    async def _toggle(jid: str, value: int) -> None:
        captured["jid"] = jid
        captured["value"] = value

    sock.group_toggle_ephemeral = _toggle  # type: ignore[attr-defined]
    result = await MessagesSendSocket.send_message(  # type: ignore[arg-type]
        sock,
        "123@g.us",
        {"disappearingMessagesInChat": True},
    )
    assert result is None
    assert captured == {"jid": "123@g.us", "value": WA_DEFAULT_EPHEMERAL}


@pytest.mark.asyncio
async def test_send_message_passes_generation_hooks(monkeypatch: pytest.MonkeyPatch) -> None:
    sock = _SendHarness()
    sock.wa_upload_to_server = object()
    sock.profile_picture_url = lambda _jid, _picture_type="preview", _timeout_ms=None: None  # type: ignore[assignment]
    sock.create_call_link = lambda _media, _event=None, _timeout_ms=None: "tok"  # type: ignore[assignment]
    sock.config.link_preview_image_thumbnail_width = 256
    sock.config.generate_high_quality_link_preview = True
    sock.config.options = {"agent": "x"}
    sock.config.media_cache = {"cache": "ok"}

    captured: dict[str, Any] = {}

    async def _fake_generate(jid: str, content: dict[str, Any], opts: dict[str, Any]) -> Any:
        captured["jid"] = jid
        captured["content"] = content
        captured["opts"] = opts
        return SimpleNamespace(
            model_dump=lambda by_alias=True, exclude_none=True: {  # noqa: ARG005
                "key": {"id": "m2", "remoteJid": jid, "fromMe": True},
                "message": {"conversation": "ok"},
            }
        )

    async def _fake_relay(
        _jid: str,
        _message: dict[str, Any],
        message_id: str | None = None,
        **_kwargs: Any,
    ) -> str:
        return str(message_id or "none")

    monkeypatch.setattr(messages_send_mod, "generate_wa_message", _fake_generate)
    sock.relay_message = _fake_relay  # type: ignore[assignment]

    result = await MessagesSendSocket.send_message(sock, "111@s.whatsapp.net", {"text": "hello"})  # type: ignore[arg-type]

    assert result is not None
    opts = captured["opts"]
    assert callable(opts["getUrlInfo"])
    assert opts["upload"] is sock.wa_upload_to_server
    assert opts["getProfilePicUrl"] is sock.profile_picture_url
    assert opts["getCallLink"] is sock.create_call_link
    assert opts["options"] == {"agent": "x"}
    assert opts["mediaCache"] == {"cache": "ok"}


@pytest.mark.asyncio
async def test_send_peer_data_operation_message_relays_with_peer_attrs() -> None:
    sock = _SendHarness()
    captured: dict[str, Any] = {}

    async def _fake_relay(
        jid: str,
        message: dict[str, Any],
        message_id: str | None = None,
        participant: dict[str, Any] | None = None,
        additional_attributes: dict[str, str] | None = None,
        additional_nodes: list[BinaryNode] | None = None,
        **_kwargs: Any,
    ) -> str:
        captured["jid"] = jid
        captured["message"] = message
        captured["message_id"] = message_id
        captured["participant"] = participant
        captured["additional_attributes"] = additional_attributes
        captured["additional_nodes"] = additional_nodes
        return "pdo-id"

    sock.relay_message = _fake_relay  # type: ignore[assignment]

    out = await MessagesSendSocket.send_peer_data_operation_message(  # type: ignore[arg-type]
        sock,
        {"historySyncOnDemandRequest": {"chatJid": "chat@s.whatsapp.net"}},
    )
    assert out == "pdo-id"
    assert captured["jid"] == "me@s.whatsapp.net"
    assert captured["additional_attributes"] == {"category": "peer", "push_priority": "high_force"}
    assert isinstance(captured["additional_nodes"], list)
    assert captured["additional_nodes"][0].tag == "meta"


@pytest.mark.asyncio
async def test_refresh_media_conn_fetches_and_caches_until_forced() -> None:
    sock = _SendHarness()
    calls = 0

    async def _query(node: BinaryNode, timeout_ms: int | None = None) -> BinaryNode:
        nonlocal calls
        _ = timeout_ms
        calls += 1
        assert node.tag == "iq"
        return BinaryNode(
            tag="iq",
            attrs={"type": "result"},
            content=[
                BinaryNode(
                    tag="media_conn",
                    attrs={"auth": "token", "ttl": "60"},
                    content=[
                        BinaryNode(tag="host", attrs={"hostname": "upload.example", "maxContentLengthBytes": "1000"})
                    ],
                )
            ],
        )

    sock.query_node = _query  # type: ignore[assignment]

    first = await MessagesSendSocket.refresh_media_conn(sock)  # type: ignore[arg-type]
    second = await MessagesSendSocket.refresh_media_conn(sock)  # type: ignore[arg-type]
    forced = await MessagesSendSocket.refresh_media_conn(sock, True)  # type: ignore[arg-type]

    assert calls == 2
    assert first["auth"] == "token"
    assert second["hosts"][0]["hostname"] == "upload.example"
    assert forced["ttl"] == 60


@pytest.mark.asyncio
async def test_update_media_message_requests_retry_and_applies_direct_path(monkeypatch: pytest.MonkeyPatch) -> None:
    sock = _SendHarness()
    sock._wait_payload = [
        {"key": {"id": "m1"}, "media": {"ciphertext": b"abc", "iv": b"123456789012"}},
    ]

    def _fake_decrypt(_media: dict[str, Any], _media_key: bytes, _msg_id: str) -> dict[str, Any]:
        return {"directPath": "/mms/image/abc"}

    monkeypatch.setattr(messages_send_mod, "decrypt_media_retry_data", _fake_decrypt)

    message = {
        "key": {"id": "m1", "remoteJid": "111@s.whatsapp.net", "fromMe": True},
        "message": {"imageMessage": {"mediaKey": b"k" * 32}},
    }
    updated = await MessagesSendSocket.update_media_message(sock, message)  # type: ignore[arg-type]

    assert updated["message"]["imageMessage"]["directPath"] == "/mms/image/abc"
    assert updated["message"]["imageMessage"]["url"]
    assert sock.sent_nodes and sock.sent_nodes[0].tag == "receipt"
    assert any(event == "messages.update" for event, _payload in sock.emitted)


@pytest.mark.asyncio
async def test_update_media_message_raises_on_retry_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    sock = _SendHarness()
    sock._wait_payload = [
        {"key": {"id": "m1"}, "media": {"ciphertext": b"abc", "iv": b"123456789012"}},
    ]

    def _fake_decrypt(_media: dict[str, Any], _media_key: bytes, _msg_id: str) -> dict[str, Any]:
        return {"result": 2}

    monkeypatch.setattr(messages_send_mod, "decrypt_media_retry_data", _fake_decrypt)

    message = {
        "key": {"id": "m1", "remoteJid": "111@s.whatsapp.net", "fromMe": True},
        "message": {"imageMessage": {"mediaKey": b"k" * 32}},
    }
    with pytest.raises(RuntimeError, match="Media re-upload failed"):
        await MessagesSendSocket.update_media_message(sock, message)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_send_helper_uses_typed_request_and_prefer() -> None:
    class _Sock:
        def __init__(self) -> None:
            self.resolved: list[tuple[Any, JidKind]] = []
            self.calls: list[tuple[str, dict[str, Any], dict[str, Any] | None]] = []

        def resolve_chat_jid(self, value: Any, *, prefer: JidKind = JidKind.PN) -> str:
            self.resolved.append((value, prefer))
            if isinstance(value, dict) and value.get("pnJid"):
                return f"{value['pnJid']}-{prefer.value}"
            return f"{value}-{prefer.value}"

        async def send_message(
            self,
            jid: str,
            content: dict[str, Any],
            options: dict[str, Any] | None = None,
        ) -> dict[str, Any]:
            self.calls.append((jid, content, options))
            return {"jid": jid, "content": content, "options": options}

    sock = _Sock()
    out = await MessagesSendSocket.send(  # type: ignore[arg-type]
        sock,
        {"to": "99112233@lid", "content": {"text": "hi"}, "prefer": "lid", "options": {"foo": "bar"}},
    )

    assert sock.resolved == [("99112233@lid", JidKind.LID)]
    assert sock.calls == [("99112233@lid-lid", {"text": "hi"}, {"foo": "bar"})]
    assert out == {"jid": "99112233@lid-lid", "content": {"text": "hi"}, "options": {"foo": "bar"}}


@pytest.mark.asyncio
async def test_send_helper_accepts_structured_user_ref_target() -> None:
    class _Sock:
        def __init__(self) -> None:
            self.resolved: list[tuple[Any, JidKind]] = []
            self.calls: list[tuple[str, dict[str, Any], dict[str, Any] | None]] = []

        def resolve_chat_jid(self, value: Any, *, prefer: JidKind = JidKind.PN) -> str:
            self.resolved.append((value, prefer))
            if hasattr(value, "model_dump"):
                value = value.model_dump(by_alias=True, exclude_none=True)
            if isinstance(value, dict):
                return str(value.get("pnJid") or value.get("jid") or "")
            return str(value)

        async def send_message(
            self,
            jid: str,
            content: dict[str, Any],
            options: dict[str, Any] | None = None,
        ) -> dict[str, Any]:
            self.calls.append((jid, content, options))
            return {"jid": jid, "content": content, "options": options}

    sock = _Sock()
    out = await MessagesSendSocket.send(  # type: ignore[arg-type]
        sock,
        {
            "to": {"userId": "pn:5511888", "pnJid": "5511888@s.whatsapp.net"},
            "content": {"text": "hello"},
            "prefer": "pn",
        },
    )

    assert sock.resolved[0][1] == JidKind.PN
    resolved_target = sock.resolved[0][0]
    assert hasattr(resolved_target, "user_id")
    assert resolved_target.user_id == "pn:5511888"
    assert resolved_target.pn_jid == "5511888@s.whatsapp.net"
    assert sock.calls == [("5511888@s.whatsapp.net", {"text": "hello"}, {})]
    assert out == {"jid": "5511888@s.whatsapp.net", "content": {"text": "hello"}, "options": {}}


@pytest.mark.asyncio
async def test_send_text_helper_builds_text_content() -> None:
    class _Sock:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        async def send(self, request: Any) -> dict[str, Any]:
            if hasattr(request, "model_dump"):
                payload = request.model_dump(by_alias=True, exclude_none=True)
            else:
                payload = request
            self.calls.append(payload)
            return {"ok": True, "request": payload}

    sock = _Sock()
    out = await MessagesSendSocket.send_text(  # type: ignore[arg-type]
        sock,
        {"to": "5511999999999@s.whatsapp.net", "text": "hello"},
    )

    assert sock.calls == [
        {
            "to": "5511999999999@s.whatsapp.net",
            "content": {"text": "hello"},
            "options": {},
            "prefer": JidKind.PN,
        }
    ]
    assert out == {"ok": True, "request": sock.calls[0]}


@pytest.mark.asyncio
async def test_send_text_helper_accepts_user_ref_target() -> None:
    class _Sock:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        async def send(self, request: Any) -> dict[str, Any]:
            if hasattr(request, "model_dump"):
                payload = request.model_dump(by_alias=True, exclude_none=True)
            else:
                payload = request
            self.calls.append(payload)
            return {"ok": True, "request": payload}

    sock = _Sock()
    out = await MessagesSendSocket.send_text(  # type: ignore[arg-type]
        sock,
        {"to": {"userId": "pn:123", "pnJid": "123@s.whatsapp.net"}, "text": "hello"},
    )

    assert sock.calls[0]["to"]["userId"] == "pn:123"
    assert sock.calls[0]["to"]["pnJid"] == "123@s.whatsapp.net"
    assert sock.calls[0]["content"] == {"text": "hello"}
    assert sock.calls[0]["options"] == {}
    assert sock.calls[0]["prefer"] == JidKind.PN
    assert out == {"ok": True, "request": sock.calls[0]}
