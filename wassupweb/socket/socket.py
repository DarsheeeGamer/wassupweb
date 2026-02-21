from __future__ import annotations

import asyncio
import base64
import inspect
import json
import os
import time
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse
from collections.abc import Awaitable, Callable
from typing import Any, Literal

from ..defaults import (
    INITIAL_PREKEY_COUNT,
    MIN_PREKEY_COUNT,
    MIN_UPLOAD_INTERVAL,
    NOISE_WA_HEADER,
    PROCESSABLE_HISTORY_TYPES,
    S_WHATSAPP_NET,
    TIME_MS,
    UPLOAD_TIMEOUT,
)
from ..utils.browser_utils import get_platform_id
from ..utils.crypto import Curve, aes_encrypt_ctr, derive_pairing_code_key, signed_key_pair
from ..utils.generics import (
    bind_wait_for_connection_update,
    bytes_to_crockford,
    get_error_code_from_stream_error,
    get_code_from_ws_error,
)
from ..utils.noise_handler import NoiseHandler, make_noise_handler
from ..utils.signal import get_next_pre_keys_node, xmpp_signed_pre_key
from ..utils.use_multi_file_auth_state import use_multi_file_auth_state
from ..utils.validate_connection import configure_successful_pairing, generate_login_node, generate_registration_node
from ..wausync import USyncQuery, USyncUser
from ..wabinary import (
    BinaryNode,
    decode_binary_node,
    encode_binary_node,
    get_binary_node_child,
    get_binary_node_children,
    is_lid_user,
    jid_decode,
    jid_encode,
)
from ..wam.binary_info import BinaryInfo
from ..wam.encode import encode_wam
from ..waproto import proto
from .client import WASocketClient


def _fallback_json_default(value: Any) -> Any:
    if isinstance(value, (bytes, bytearray)):
        return {"__type__": "bytes", "base64": base64.b64encode(bytes(value)).decode("ascii")}
    raise TypeError(f"Object is not JSON serializable: {type(value)!r}")


def _fallback_json_load(value: Any) -> Any:
    if isinstance(value, dict):
        if value.get("__type__") == "bytes" and isinstance(value.get("base64"), str):
            return base64.b64decode(value["base64"])
        return {k: _fallback_json_load(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_fallback_json_load(v) for v in value]
    return value


def _proto_json(value: Any) -> Any:
    if isinstance(value, (bytes, bytearray)):
        return base64.b64encode(bytes(value)).decode("ascii")
    if isinstance(value, dict):
        return {k: _proto_json(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_proto_json(v) for v in value]
    return value


def _coerce_handshake_bytes(payload: dict[str, Any]) -> dict[str, Any]:
    target = payload
    hello = payload.get("serverHello")
    if isinstance(hello, dict):
        target = hello
    for field in ("ephemeral", "static", "payload"):
        value = target.get(field)
        if isinstance(value, str):
            try:
                target[field] = base64.b64decode(value + ("=" * ((4 - len(value) % 4) % 4)))
            except Exception:
                continue
    return payload


class CoreSocket(WASocketClient):
    _server_time_offset_ms: int = 0

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.wam_buffer = BinaryInfo()
        self._closed = False
        self._last_date_recv_ms = int(time.time() * 1000)
        self._keep_alive_task: asyncio.Task[None] | None = None
        self._qr_task: asyncio.Task[None] | None = None
        self._upload_pre_keys_task: asyncio.Task[None] | None = None
        self._last_upload_time_ms = 0
        self._noise: NoiseHandler | None = None
        self._ephemeral_key_pair = None
        self._save_auth_state: Callable[[], Awaitable[Any]] | None = None
        self._in_handshake = False
        self._core_handlers_attached = False
        self._did_start_buffer = False

    async def _ensure_auth_state(self) -> None:
        if getattr(self.config, "auth", None):
            return
        auth_folder = str(getattr(self.config, "auth_folder", None) or "session")
        try:
            auth_state, save_creds = await use_multi_file_auth_state(auth_folder)
            self.config.auth = auth_state
            self._save_auth_state = save_creds
        except Exception as error:
            self._logger.warning("failed to initialize default auth state", extra={"folder": auth_folder, "error": str(error)})

    async def _resolve_signal_repository(self) -> Any:
        repository = getattr(self, "_signal_repository", None)
        if repository is not None:
            return repository
        ensure_repository = getattr(self, "_ensure_signal_repository", None)
        if not callable(ensure_repository):
            return None
        try:
            maybe_repository = ensure_repository()
            if inspect.isawaitable(maybe_repository):
                maybe_repository = await maybe_repository
            return maybe_repository
        except Exception as error:
            self._logger.warning("failed to initialize signal repository", extra={"error": str(error)})
            return None

    def _apply_routing_info_to_transport_url(self) -> None:
        auth = getattr(self.config, "auth", None)
        if not auth:
            return
        routing_info = getattr(getattr(auth, "creds", None), "routing_info", None)
        if not isinstance(routing_info, (bytes, bytearray)) or not routing_info:
            return

        transport_url = getattr(self.transport, "_url", None)
        if not isinstance(transport_url, str) or not transport_url:
            return

        parsed = urlparse(transport_url)
        if parsed.scheme != "wss":
            return
        query = dict(parse_qsl(parsed.query, keep_blank_values=True))
        if "ED" in query:
            return

        encoded = base64.urlsafe_b64encode(bytes(routing_info)).rstrip(b"=").decode("ascii")
        query["ED"] = encoded
        patched = parsed._replace(query=urlencode(query))
        setattr(self.transport, "_url", urlunparse(patched))

    def _schedule_own_lid_session_migration(self, my_lid: str) -> None:
        my_pn = self._me_id()
        if not my_lid or not my_pn:
            return

        async def _runner() -> None:
            try:
                repository = await self._resolve_signal_repository()
                if repository is None:
                    return

                mapping_store = getattr(repository, "lid_mapping", None) or getattr(repository, "lidMapping", None)
                if mapping_store is not None:
                    store_fn = getattr(mapping_store, "store_lid_pn_mappings", None) or getattr(mapping_store, "storeLIDPNMappings", None)
                    if callable(store_fn):
                        stored = store_fn([{"lid": my_lid, "pn": my_pn}])
                        if inspect.isawaitable(stored):
                            await stored

                auth = getattr(self.config, "auth", None)
                if auth:
                    decoded = jid_decode(my_pn) or {}
                    user = decoded.get("user")
                    if user:
                        device = str(decoded.get("device") or 0)
                        maybe_saved = auth.keys.set({"device-list": {user: [device]}})
                        if inspect.isawaitable(maybe_saved):
                            await maybe_saved

                migrate_fn = getattr(repository, "migrate_session", None) or getattr(repository, "migrateSession", None)
                if callable(migrate_fn):
                    maybe_migrated = migrate_fn(my_pn, my_lid)
                    if inspect.isawaitable(maybe_migrated):
                        await maybe_migrated
                self._logger.info("Own LID session created successfully", extra={"myPN": my_pn, "myLID": my_lid})
            except Exception as error:
                self._logger.error("Failed to create own LID session", extra={"lid": my_lid, "error": str(error)})

        asyncio.create_task(_runner())

    async def connect(self) -> None:
        if bool(getattr(self.config, "mobile", False)):
            raise RuntimeError("Mobile API is not supported anymore")

        ws_url = str(getattr(self.config, "wa_websocket_url", "") or "")
        if ws_url.startswith("tcp:"):
            raise RuntimeError("Mobile API is not supported anymore")

        if bool(getattr(self.config, "print_qr_in_terminal", False)):
            self._logger.warning(
                "The printQRInTerminal option has been deprecated; listen to connection.update and handle QR yourself."
            )
        ensure_auth_fn = getattr(self, "_ensure_auth_state", None)
        if callable(ensure_auth_fn):
            await ensure_auth_fn()

        should_sync = getattr(self.config, "should_sync_history_message", None)
        if callable(should_sync):
            try:
                sync_disabled = all(not bool(should_sync({"syncType": sync_type})) for sync_type in PROCESSABLE_HISTORY_TYPES)
                if sync_disabled:
                    self._logger.warning(
                        "DANGER: disabling all history sync can prevent initial LID mappings and destabilize sessions."
                    )
            except Exception as error:
                self._logger.debug("failed to evaluate history-sync safety check", extra={"error": str(error)})

        apply_routing_fn = getattr(self, "_apply_routing_info_to_transport_url", None)
        if callable(apply_routing_fn):
            apply_routing_fn()

        self._closed = False
        self._last_date_recv_ms = int(time.time() * 1000)
        for plugin in self._plugins:
            await plugin.before_connect(self)

        await self.transport.connect()
        self._recv_task = asyncio.create_task(self._recv_loop())
        self._attach_core_handlers()

        self._did_start_buffer = False
        if self._me_id():
            buffer_fn = getattr(self.ev, "buffer", None)
            if callable(buffer_fn):
                buffer_fn()
                self._did_start_buffer = True

        await self.ev.emit(
            "connection.update",
            {"connection": "connecting", "receivedPendingNotifications": False, "qr": None},
        )

        try:
            await self.validate_connection()
        except Exception as error:
            self._logger.error("error in validating connection", extra={"error": str(error)})
            await self.end(error)
            raise

        for plugin in self._plugins:
            await plugin.after_connect(self)

    async def disconnect(self) -> None:
        should_emit_close = not self._closed
        self._stop_keep_alive_request()
        self._cancel_qr_task()
        current_task = asyncio.current_task()
        if self._recv_task and self._recv_task is not current_task:
            self._recv_task.cancel()
            try:
                await self._recv_task
            except asyncio.CancelledError:
                self._logger.debug("recv task cancelled during disconnect")
        await self.transport.disconnect()
        if should_emit_close:
            await self.ev.emit("connection.update", {"connection": "close"})

    def _attach_core_handlers(self) -> None:
        if self._core_handlers_attached:
            return
        self.ev.on("node:iq", self._handle_iq_node)
        self.ev.on("node:success", self._handle_success_node)
        self.ev.on("node:stream:error", self._handle_stream_error_node)
        self.ev.on("node:failure", self._handle_failure_node)
        self.ev.on("node:xmlstreamend", self._handle_xmlstreamend_node)
        self.ev.on("node:ib", self._handle_ib_node)
        self.ev.on("creds.update", self._handle_creds_update)
        self._core_handlers_attached = True

    def _cancel_qr_task(self) -> None:
        if self._qr_task and not self._qr_task.done():
            self._qr_task.cancel()
        self._qr_task = None

    async def _emit_cb_node_events(self, node: BinaryNode) -> None:
        msg_id = node.attrs.get("id")
        if msg_id:
            await self._ev.emit(f"TAG:{msg_id}", node)

        first_child_tag = ""
        if isinstance(node.content, list) and node.content and isinstance(node.content[0], BinaryNode):
            first_child_tag = node.content[0].tag

        for key, value in node.attrs.items():
            await self._ev.emit(f"CB:{node.tag},{key}:{value},{first_child_tag}", node)
            await self._ev.emit(f"CB:{node.tag},{key}:{value}", node)
            await self._ev.emit(f"CB:{node.tag},{key}", node)

        await self._ev.emit(f"CB:{node.tag},,{first_child_tag}", node)
        await self._ev.emit(f"CB:{node.tag}", node)

    async def _recv_loop(self) -> None:
        while True:
            try:
                payload = await self.transport.recv()
                await self.on_message_received(payload)
            except asyncio.CancelledError:
                raise
            except Exception as error:  # pragma: no cover - runtime wire errors
                map_error_fn = getattr(self, "_map_websocket_error", None)
                mapped_error = map_error_fn(error) if callable(map_error_fn) else error
                self._logger.error("socket recv loop error", extra={"error": str(mapped_error)})
                for plugin in self._plugins:
                    await plugin.on_error(self, mapped_error)
                await self._ev.emit("error", mapped_error)
                if not self._is_transport_open():
                    await self.end(mapped_error)
                    return

    async def _dispatch_node(self, node: BinaryNode) -> None:
        current: Any = node
        for plugin in self._plugins:
            current = await plugin.after_receive_node(self, current)
        if isinstance(current, BinaryNode):
            await self._emit_cb_node_events(current)
            await self._ev.emit("node", current)
            await self._ev.emit(f"node:{current.tag}", current)
            msg_id = current.attrs.get("id")
            if msg_id and msg_id in self._pending_queries and not self._pending_queries[msg_id].done():
                self._pending_queries[msg_id].set_result(current)

    async def _dispatch_frame(self, frame: bytes | BinaryNode) -> None:
        await self._ev.emit("frame", frame)
        if isinstance(frame, BinaryNode):
            await self._dispatch_node(frame)
            return
        noise = getattr(self, "_noise", None)
        if getattr(self, "_in_handshake", False) and noise is not None and getattr(noise, "_transport", None) is None:
            # Pre-transport handshake frames are protobuf payloads, not binary nodes.
            return
        try:
            node = await decode_binary_node(bytes(frame))
        except Exception as error:
            self._logger.error("failed to decode incoming binary node frame", extra={"error": str(error), "frameLen": len(bytes(frame))})
            await self._ev.emit("error", error)
            return
        await self._dispatch_node(node)

    async def _handle_creds_update(self, update: dict[str, Any]) -> None:
        auth = getattr(self.config, "auth", None)
        if not auth or not isinstance(update, dict):
            return

        creds = auth.creds
        old_name = None
        old_me = getattr(creds, "me", None)
        if isinstance(old_me, dict):
            old_name = old_me.get("name")

        model_fields = getattr(type(creds), "model_fields", {}) or {}
        handled: set[str] = set()
        for field_name, field_info in model_fields.items():
            alias = getattr(field_info, "alias", None) or field_name
            if field_name in update:
                setattr(creds, field_name, update[field_name])
                handled.add(field_name)
            elif alias in update:
                setattr(creds, field_name, update[alias])
                handled.add(alias)

        for key, value in update.items():
            if key in handled:
                continue
            if hasattr(creds, key):
                setattr(creds, key, value)

        new_me = getattr(creds, "me", None)
        new_name = new_me.get("name") if isinstance(new_me, dict) else None
        if new_name and old_name != new_name:
            try:
                await self.send_node(
                    BinaryNode(
                        tag="presence",
                        attrs={"name": new_name},
                    )
                )
            except Exception as error:
                self._logger.warning(
                    "error in sending presence update on name change",
                    extra={"error": str(error)},
                )
        save_auth_state = getattr(self, "_save_auth_state", None)
        if callable(save_auth_state):
            try:
                await save_auth_state()
            except Exception as error:
                self._logger.warning("failed to persist auth state", extra={"error": str(error)})

    async def _handle_pair_device_node(self, stanza: BinaryNode) -> None:
        auth = self._require_auth()
        creds = auth.creds
        await self.send_node(
            BinaryNode(
                tag="iq",
                attrs={
                    "to": S_WHATSAPP_NET,
                    "type": "result",
                    "id": stanza.attrs.get("id", ""),
                },
            )
        )

        pair_device_node = get_binary_node_child(stanza, "pair-device")
        ref_nodes = get_binary_node_children(pair_device_node, "ref")
        if not ref_nodes:
            return

        noise_key_b64 = base64.b64encode(bytes(creds.noise_key.public)).decode("ascii")
        identity_key_b64 = base64.b64encode(bytes(creds.signed_identity_key.public)).decode("ascii")
        adv_b64 = creds.adv_secret_key
        qr_timeout = getattr(self.config, "qr_timeout", None)
        initial_qr_ms = int(qr_timeout or 60_000)
        next_qr_ms = int(qr_timeout or 20_000)

        self._cancel_qr_task()

        async def _runner() -> None:
            wait_ms = initial_qr_ms
            refs = list(ref_nodes)
            while refs:
                if not self._is_transport_open():
                    return

                ref_node = refs.pop(0)
                ref_raw = ref_node.content
                if isinstance(ref_raw, (bytes, bytearray)):
                    ref = bytes(ref_raw).decode("utf-8", errors="ignore")
                elif isinstance(ref_raw, str):
                    ref = ref_raw
                else:
                    ref = ""
                if not ref:
                    continue

                qr = ",".join([ref, noise_key_b64, identity_key_b64, adv_b64])
                await self.ev.emit("connection.update", {"qr": qr})
                await asyncio.sleep(wait_ms / 1000.0)
                wait_ms = next_qr_ms

            await self.end(RuntimeError("QR refs attempts ended"))

        self._qr_task = asyncio.create_task(_runner())

    async def _handle_pair_success_node(self, stanza: BinaryNode) -> None:
        auth = self._require_auth()
        creds = auth.creds
        try:
            self.update_server_time_offset(stanza)

            pairing_payload = configure_successful_pairing(stanza, self._creds_map())
            reply = pairing_payload.get("reply")
            updated = pairing_payload.get("creds") or {}

            if updated:
                await self.ev.emit("creds.update", updated)
            await self.ev.emit("connection.update", {"isNewLogin": True, "qr": None})

            if isinstance(reply, BinaryNode):
                await self.send_node(reply)
            await self.send_unified_session()
        except Exception:
            # Fallback: still accept minimal jid/lid update if richer pairing parse fails.
            pair_success = get_binary_node_child(stanza, "pair-success")
            device_node = get_binary_node_child(pair_success, "device")
            if device_node and device_node.attrs.get("jid"):
                me = creds.me or {}
                if not isinstance(me, dict):
                    me = {}
                me["id"] = device_node.attrs.get("jid", me.get("id"))
                if device_node.attrs.get("lid"):
                    me["lid"] = device_node.attrs["lid"]
                await self.ev.emit("creds.update", {"me": me})
                await self.ev.emit("connection.update", {"isNewLogin": True, "qr": None})

            await self.send_node(
                BinaryNode(
                    tag="iq",
                    attrs={"to": S_WHATSAPP_NET, "type": "result", "id": stanza.attrs.get("id", "")},
                )
            )

    async def _handle_iq_node(self, node: BinaryNode) -> None:
        if get_binary_node_child(node, "pair-device") and node.attrs.get("type") == "set":
            await self._handle_pair_device_node(node)
            return
        if get_binary_node_child(node, "pair-success"):
            await self._handle_pair_success_node(node)

    async def _handle_success_node(self, node: BinaryNode) -> None:
        self.update_server_time_offset(node)
        self._cancel_qr_task()

        try:
            await self.upload_pre_keys_to_server_if_required()
            await self.send_passive_iq("active")
            try:
                await self.digest_key_bundle()
            except Exception as digest_error:
                self._logger.warning("failed to run digest after login", extra={"error": str(digest_error)})
        except Exception as error:
            self._logger.warning("failed to send initial passive iq", extra={"error": str(error)})

        auth = getattr(self.config, "auth", None)
        if auth and node.attrs.get("lid"):
            me = getattr(auth.creds, "me", None) or {}
            if isinstance(me, dict):
                me["lid"] = node.attrs["lid"]
                await self.ev.emit("creds.update", {"me": me})
            migrate_fn = getattr(self, "_schedule_own_lid_session_migration", None)
            if callable(migrate_fn):
                migrate_fn(node.attrs["lid"])

        await self.ev.emit("connection.update", {"connection": "open"})
        await self.send_unified_session()

    async def _handle_stream_error_node(self, node: BinaryNode) -> None:
        data = get_error_code_from_stream_error(node)
        reason = data.get("reason", "unknown")
        status = data.get("statusCode")
        await self.end(RuntimeError(f"Stream Errored ({reason}) [{status}]"))

    async def _handle_failure_node(self, node: BinaryNode) -> None:
        reason = int(node.attrs.get("reason") or 500)
        await self.end(RuntimeError(f"Connection Failure ({reason})"))

    async def _handle_xmlstreamend_node(self, _node: BinaryNode) -> None:
        await self.end(RuntimeError("Connection Terminated by Server"))

    async def _handle_ib_node(self, node: BinaryNode) -> None:
        if get_binary_node_child(node, "downgrade_webclient"):
            await self.end(RuntimeError("Multi-device beta not joined"))
            return

        if get_binary_node_child(node, "offline_preview"):
            self._logger.info("offline preview received", extra={"node": str(node)})
            await self.send_node(
                BinaryNode(
                    tag="ib",
                    attrs={},
                    content=[BinaryNode(tag="offline_batch", attrs={"count": "100"})],
                )
            )

        edge_routing = get_binary_node_child(node, "edge_routing")
        routing_info = get_binary_node_child(edge_routing, "routing_info")
        if routing_info and routing_info.content:
            auth = getattr(self.config, "auth", None)
            if auth:
                content = routing_info.content
                if isinstance(content, (bytes, bytearray)):
                    auth.creds.routing_info = bytes(content)
                    await self.ev.emit("creds.update", {"routingInfo": auth.creds.routing_info})
                elif isinstance(content, str):
                    auth.creds.routing_info = content.encode("utf-8")
                    await self.ev.emit("creds.update", {"routingInfo": auth.creds.routing_info})

        offline = get_binary_node_child(node, "offline")
        if offline:
            offline_notifs = int(offline.attrs.get("count") or 0)
            self._logger.info(f"handled {offline_notifs} offline messages/notifications")
            if self._did_start_buffer:
                flush_fn = getattr(self.ev, "flush", None)
                if callable(flush_fn):
                    flush_fn()
                self._did_start_buffer = False
            await self.ev.emit("connection.update", {"receivedPendingNotifications": True})

    async def send_raw_message(self, payload: bytes) -> None:
        is_open_fn = getattr(self, "_is_transport_open", None)
        is_open = bool(is_open_fn()) if callable(is_open_fn) else True
        if not is_open:
            raise RuntimeError("Connection Closed")

        outgoing = bytes(payload)
        if self._noise is not None:
            outgoing = self._noise.encode_frame(outgoing)
        timeout_ms = getattr(getattr(self, "config", None), "connect_timeout_ms", None)
        if timeout_ms and int(timeout_ms) > 0:
            await asyncio.wait_for(self.transport.send(outgoing), timeout=float(timeout_ms) / 1000.0)
            return
        await self.transport.send(outgoing)

    async def send_node(self, node: BinaryNode) -> None:
        current: Any = node
        for plugin in self._plugins:
            current = await plugin.before_send_node(self, current)
        payload = encode_binary_node(current)
        await self.send_raw_message(payload)
        await self._ev.emit("node.sent", current)

    async def await_next_message(self, send_msg: bytes | None = None, timeout_ms: int | None = None) -> Any:
        if not self._is_transport_open():
            raise RuntimeError("Connection Closed")
        if send_msg is not None:
            await self.send_raw_message(send_msg)
        timeout = timeout_ms if timeout_ms is not None else self.config.connect_timeout_ms
        return await self.wait_for("frame", timeout_ms=timeout)

    async def wait_for_message(self, msg_id: str, timeout_ms: int | None = None) -> BinaryNode | None:
        try:
            return await self.wait_for(
                "node",
                predicate=lambda node: isinstance(node, BinaryNode) and node.attrs.get("id") == msg_id,
                timeout_ms=timeout_ms,
            )
        except asyncio.TimeoutError:
            self._logger.warning("timed out waiting for message", extra={"msgId": msg_id})
            return None
        except RuntimeError as error:
            if "Timed Out" in str(error):
                self._logger.warning("timed out waiting for message", extra={"msgId": msg_id})
                return None
            raise

    async def query_node(self, node: BinaryNode, timeout_ms: int | None = None) -> BinaryNode:
        return await self.query(node, timeout_ms=timeout_ms)

    async def send_node_with_id(
        self,
        tag: str,
        attrs: dict[str, str] | None = None,
        content: Any = None,
        timeout_ms: int | None = None,
    ) -> BinaryNode:
        node = BinaryNode(tag=tag, attrs=attrs or {}, content=content)
        return await self.query_node(node, timeout_ms=timeout_ms)

    def _require_auth(self) -> Any:
        auth = getattr(self.config, "auth", None)
        if not auth:
            raise RuntimeError("auth state is required for this operation")
        return auth

    def _is_transport_open(self) -> bool:
        transport = self.transport
        if hasattr(transport, "is_open"):
            state = transport.is_open
            if callable(state):
                try:
                    return bool(state())
                except Exception:
                    return False
            return bool(state)

        ws = getattr(transport, "_ws", None)
        if ws is not None:
            ws_state = getattr(ws, "state", None)
            if ws_state is not None:
                ws_state_name = getattr(ws_state, "name", None)
                if isinstance(ws_state_name, str):
                    return ws_state_name.upper() == "OPEN"
                try:
                    return int(ws_state) == 1
                except Exception:
                    return False
            open_state = getattr(ws, "open", None)
            if open_state is not None:
                return bool(open_state)
            closed_state = getattr(ws, "closed", None)
            if isinstance(closed_state, bool):
                return not closed_state

        return self._recv_task is not None and not self._recv_task.done()

    def _is_transport_closed_or_closing(self) -> bool:
        transport = self.transport

        for attr_name in ("is_closed", "is_closing", "isClosing", "closed", "closing"):
            if not hasattr(transport, attr_name):
                continue
            state = getattr(transport, attr_name)
            if callable(state):
                try:
                    state = state()
                except Exception:
                    continue
            if isinstance(state, bool):
                if state:
                    return True
            elif state not in (None, 0, "", "0", "false", "False"):
                return True

        ws = getattr(transport, "_ws", None)
        if ws is None:
            return False

        ws_state = getattr(ws, "state", None)
        if ws_state is not None:
            ws_state_name = getattr(ws_state, "name", None)
            if isinstance(ws_state_name, str):
                normalized = ws_state_name.upper()
                if normalized in {"CLOSED", "CLOSING"}:
                    return True
            else:
                try:
                    ws_state_value = int(ws_state)
                    if ws_state_value in {2, 3}:
                        return True
                except Exception:
                    pass

        for attr_name in ("closed", "closing", "is_closed", "is_closing"):
            if not hasattr(ws, attr_name):
                continue
            state = getattr(ws, attr_name)
            if callable(state):
                try:
                    state = state()
                except Exception:
                    continue
            if isinstance(state, bool):
                if state:
                    return True
            elif state not in (None, 0, "", "0", "false", "False"):
                return True

        return getattr(ws, "close_code", None) is not None

    async def wait_for_socket_open(self) -> None:
        if self._is_transport_open():
            return
        close_check_fn = getattr(self, "_is_transport_closed_or_closing", None)
        is_closed = bool(close_check_fn()) if callable(close_check_fn) else False
        if is_closed:
            raise RuntimeError("Connection Closed")

        waiter = bind_wait_for_connection_update(self.ev)

        async def _check(update: dict[str, Any]) -> bool:
            return update.get("connection") == "open"

        await waiter(_check, self.config.connect_timeout_ms)

    def _map_websocket_error(self, error: Exception) -> RuntimeError:
        status_code = int(get_code_from_ws_error(error))
        mapped = RuntimeError(f"WebSocket Error ({error}) [{status_code}]")
        setattr(mapped, "status_code", status_code)
        setattr(mapped, "data", error)
        return mapped

    def _stop_keep_alive_request(self) -> None:
        if self._keep_alive_task and not self._keep_alive_task.done():
            self._keep_alive_task.cancel()
        self._keep_alive_task = None

    def start_keep_alive_request(self) -> asyncio.Task[None]:
        self._stop_keep_alive_request()
        interval_ms = int(self.config.keep_alive_interval_ms or 30_000)

        async def _loop() -> None:
            while True:
                await asyncio.sleep(interval_ms / 1000.0)
                diff = int(time.time() * 1000) - self._last_date_recv_ms
                if diff > interval_ms + 5000:
                    await self.end(RuntimeError("Connection was lost"))
                    return

                if not self._is_transport_open():
                    self._logger.warning("keep alive called when transport not open")
                    continue

                try:
                    await self.query_node(
                        BinaryNode(
                            tag="iq",
                            attrs={
                                "id": self.generate_message_tag(),
                                "to": S_WHATSAPP_NET,
                                "type": "get",
                                "xmlns": "w:p",
                            },
                            content=[BinaryNode(tag="ping", attrs={})],
                        )
                    )
                except Exception as error:
                    self._logger.error("error in sending keep alive", extra={"error": str(error)})

        self._keep_alive_task = asyncio.create_task(_loop())
        return self._keep_alive_task

    def _proto_cls(self, name: str) -> Any:
        getter = getattr(proto, "get", None)
        if callable(getter):
            return getter(name)
        return getattr(proto, name, None)

    def _encode_proto_message(self, name: str, payload: dict[str, Any]) -> bytes:
        cls = self._proto_cls(name)
        if cls is not None:
            try:
                from google.protobuf.json_format import ParseDict

                message = cls()
                ParseDict(_proto_json(payload), message, ignore_unknown_fields=True)
                return message.SerializeToString()
            except Exception as error:
                self._logger.debug("proto encode fallback triggered", extra={"name": name, "error": str(error)})

        return json.dumps(payload, separators=(",", ":"), default=_fallback_json_default).encode("utf-8")

    def _decode_proto_message(self, name: str, payload: bytes) -> dict[str, Any]:
        cls = self._proto_cls(name)
        if cls is not None:
            try:
                from google.protobuf.json_format import MessageToDict

                message = cls()
                message.ParseFromString(bytes(payload))
                return MessageToDict(message, preserving_proto_field_name=False)
            except Exception as error:
                self._logger.debug("proto decode fallback triggered", extra={"name": name, "error": str(error)})

        return _fallback_json_load(json.loads(bytes(payload).decode("utf-8")))

    def _config_map(self) -> dict[str, Any]:
        if hasattr(self.config, "model_dump"):
            return self.config.model_dump(by_alias=True, exclude_none=True)
        return dict(getattr(self.config, "__dict__", {}))

    def _creds_map(self) -> dict[str, Any]:
        auth = self._require_auth()
        creds = auth.creds
        if hasattr(creds, "model_dump"):
            return creds.model_dump(by_alias=True, exclude_none=True)
        return dict(getattr(creds, "__dict__", {}))

    async def validate_connection(self) -> None:
        await self.wait_for_socket_open()

        auth = self._require_auth()
        creds = auth.creds
        self._ephemeral_key_pair = Curve.generate_key_pair()
        self._noise = make_noise_handler(
            key_pair=self._ephemeral_key_pair,
            NOISE_HEADER=NOISE_WA_HEADER,
            logger=self._logger,
            routing_info=creds.routing_info,
            strict_cert_validation=bool(getattr(self.config, "strict_noise_cert_validation", False)),
        )

        self._in_handshake = True
        hello = {"clientHello": {"ephemeral": bytes(self._ephemeral_key_pair.public)}}
        init = self._encode_proto_message("HandshakeMessage", hello)
        response = await self.await_next_message(init, self.config.connect_timeout_ms)
        if not isinstance(response, (bytes, bytearray)):
            raise RuntimeError("invalid handshake response type")

        handshake = self._decode_proto_message("HandshakeMessage", bytes(response))
        handshake = _coerce_handshake_bytes(handshake)
        key_enc = self._noise.process_handshake(handshake, creds.noise_key)

        me = creds.me or {}
        if hasattr(me, "model_dump"):
            me = me.model_dump(by_alias=True, exclude_none=True)

        if isinstance(me, dict) and me.get("id"):
            client_payload = generate_login_node(str(me["id"]), self._config_map())
        else:
            client_payload = generate_registration_node(self._creds_map(), self._config_map())

        payload_enc = self._noise.encrypt(self._encode_proto_message("ClientPayload", client_payload))
        finish = {"clientFinish": {"static": key_enc, "payload": payload_enc}}
        await self.send_raw_message(self._encode_proto_message("HandshakeMessage", finish))
        await self._noise.finish_init()
        self._in_handshake = False
        self.start_keep_alive_request()

    async def send_passive_iq(self, tag: Literal["passive", "active"] | str) -> BinaryNode:
        return await self.query_node(
            BinaryNode(
                tag="iq",
                attrs={"to": S_WHATSAPP_NET, "xmlns": "passive", "type": "set"},
                content=[BinaryNode(tag=tag, attrs={})],
            )
        )

    async def get_available_prekeys_on_server(self) -> int:
        result = await self.query_node(
            BinaryNode(
                tag="iq",
                attrs={"id": self.generate_message_tag(), "xmlns": "encrypt", "type": "get", "to": S_WHATSAPP_NET},
                content=[BinaryNode(tag="count", attrs={})],
            )
        )
        count_child = get_binary_node_child(result, "count")
        return int((count_child.attrs.get("value") if count_child else "0") or 0)

    async def _run_keys_transaction(self, work: Callable[[], Awaitable[Any]], key: str) -> Any:
        auth = self._require_auth()
        tx = getattr(auth.keys, "transaction", None)
        if callable(tx):
            return await tx(work, key)
        return await work()

    def _me_id(self) -> str | None:
        auth = getattr(self.config, "auth", None)
        if not auth:
            return None
        me = getattr(auth.creds, "me", None) or {}
        if hasattr(me, "model_dump"):
            me = me.model_dump(by_alias=True, exclude_none=True)
        if isinstance(me, dict):
            raw = me.get("id")
            if isinstance(raw, str) and raw:
                return raw
        return None

    async def upload_pre_keys(self, count: int = MIN_PREKEY_COUNT, retry_count: int = 0) -> None:
        if retry_count == 0:
            elapsed = int(time.time() * 1000) - self._last_upload_time_ms
            if elapsed < MIN_UPLOAD_INTERVAL:
                self._logger.debug("Skipping upload, pre-key upload interval not reached", extra={"elapsedMs": elapsed})
                return

        if self._upload_pre_keys_task and not self._upload_pre_keys_task.done():
            self._logger.debug("Pre-key upload already in progress, waiting for completion")
            await self._upload_pre_keys_task
            return

        auth = self._require_auth()

        async def _logic() -> None:
            self._logger.info("uploading pre-keys", extra={"count": count, "retryCount": retry_count})

            async def _prepare() -> BinaryNode:
                generated = await get_next_pre_keys_node(auth, count)
                update = generated.get("update") or {}
                if "nextPreKeyId" in update:
                    auth.creds.next_pre_key_id = int(update["nextPreKeyId"])
                if "firstUnuploadedPreKeyId" in update:
                    auth.creds.first_unuploaded_pre_key_id = int(update["firstUnuploadedPreKeyId"])
                if update:
                    await self.ev.emit("creds.update", update)

                node = generated.get("node")
                if not isinstance(node, BinaryNode):
                    raise RuntimeError("failed to build pre-key upload node")
                return node

            node = await self._run_keys_transaction(_prepare, self._me_id() or "upload-pre-keys")
            attempt = retry_count
            while True:
                try:
                    await self.query_node(node)
                    self._last_upload_time_ms = int(time.time() * 1000)
                    return
                except Exception as error:
                    if attempt >= 3:
                        raise
                    delay_ms = min(1000 * (2**attempt), 10_000)
                    self._logger.warning(
                        "pre-key upload failed, retrying",
                        extra={"error": str(error), "attempt": attempt + 1, "delayMs": delay_ms},
                    )
                    attempt += 1
                    await asyncio.sleep(delay_ms / 1000.0)

        timeout = UPLOAD_TIMEOUT / 1000.0
        task = asyncio.create_task(asyncio.wait_for(_logic(), timeout=timeout))
        self._upload_pre_keys_task = task
        try:
            await task
        except asyncio.TimeoutError as error:
            raise RuntimeError("Pre-key upload timeout") from error
        finally:
            if self._upload_pre_keys_task is task:
                self._upload_pre_keys_task = None

    async def verify_current_pre_key_exists(self) -> dict[str, Any]:
        auth = self._require_auth()
        current_pre_key_id = int(auth.creds.next_pre_key_id or 0) - 1
        if current_pre_key_id <= 0:
            return {"exists": False, "currentPreKeyId": 0}

        pre_keys = await auth.keys.get("pre-key", [str(current_pre_key_id)])
        exists = bool(pre_keys.get(str(current_pre_key_id)))
        return {"exists": exists, "currentPreKeyId": current_pre_key_id}

    async def upload_pre_keys_to_server_if_required(self) -> None:
        try:
            pre_key_count = await self.get_available_prekeys_on_server()
            threshold = INITIAL_PREKEY_COUNT if pre_key_count == 0 else MIN_PREKEY_COUNT
            check = await self.verify_current_pre_key_exists()
            current_exists = bool(check.get("exists"))
            current_prekey_id = int(check.get("currentPreKeyId") or 0)
            low_server_count = pre_key_count <= threshold
            missing_current_prekey = not current_exists and current_prekey_id > 0

            if low_server_count or missing_current_prekey:
                await self.upload_pre_keys(threshold)
        except Exception as error:
            self._logger.error("Failed to check/upload pre-keys during initialization", extra={"error": str(error)})

    async def digest_key_bundle(self) -> None:
        result = await self.query_node(
            BinaryNode(
                tag="iq",
                attrs={"to": S_WHATSAPP_NET, "type": "get", "xmlns": "encrypt"},
                content=[BinaryNode(tag="digest", attrs={})],
            )
        )
        digest_node = get_binary_node_child(result, "digest")
        if not digest_node:
            await self.upload_pre_keys()
            raise RuntimeError("encrypt/get digest returned no digest node")

    async def rotate_signed_pre_key(self) -> None:
        auth = self._require_auth()
        creds = auth.creds
        new_id = int(creds.signed_pre_key.key_id or 0) + 1
        skey = signed_key_pair(creds.signed_identity_key, new_id)
        await self.query_node(
            BinaryNode(
                tag="iq",
                attrs={"to": S_WHATSAPP_NET, "type": "set", "xmlns": "encrypt"},
                content=[BinaryNode(tag="rotate", attrs={}, content=[xmpp_signed_pre_key(skey)])],
            )
        )
        creds.signed_pre_key = skey
        await self.ev.emit("creds.update", {"signedPreKey": skey})

    async def execute_usync_query(self, usync_query: USyncQuery) -> Any:
        if not usync_query.protocols:
            raise ValueError("USyncQuery must have at least one protocol")

        user_nodes: list[BinaryNode] = []
        for user in usync_query.users:
            attrs: dict[str, str] = {}
            if not user.phone and user.id:
                attrs["jid"] = user.id
            content = [element for protocol in usync_query.protocols if (element := protocol.get_user_element(user)) is not None]
            user_nodes.append(BinaryNode(tag="user", attrs=attrs, content=content))

        iq = BinaryNode(
            tag="iq",
            attrs={"to": S_WHATSAPP_NET, "type": "get", "xmlns": "usync"},
            content=[
                BinaryNode(
                    tag="usync",
                    attrs={
                        "context": usync_query.context,
                        "mode": usync_query.mode,
                        "sid": self.generate_message_tag(),
                        "last": "true",
                        "index": "0",
                    },
                    content=[
                        BinaryNode(tag="query", attrs={}, content=[protocol.get_query_element() for protocol in usync_query.protocols]),
                        BinaryNode(tag="list", attrs={}, content=user_nodes),
                    ],
                )
            ],
        )
        result = await self.query_node(iq)
        return usync_query.parse_usync_query_result(result)

    async def on_whatsapp(self, *phone_numbers: str) -> list[dict[str, Any]]:
        usync_query = USyncQuery()
        contact_enabled = False
        for jid in phone_numbers:
            if is_lid_user(jid):
                self._logger.warning("LIDs are not supported with onWhatsApp")
                continue

            if not contact_enabled:
                contact_enabled = True
                usync_query = usync_query.with_contact_protocol()

            phone = f"+{jid.replace('+', '').split('@')[0].split(':')[0]}"
            usync_query.with_user(USyncUser().with_phone(phone))

        if not usync_query.users:
            return []

        results = await self.execute_usync_query(usync_query)
        if not results:
            return []
        response: list[dict[str, Any]] = []
        for item in results.list:
            contact = item.data.get("contact")
            if contact:
                response.append({"jid": item.id, "exists": bool(contact)})
        return response

    async def pn_from_lid_usync(self, jids: list[str]) -> list[dict[str, str]]:
        usync_query = USyncQuery().with_lid_protocol().with_context("background")
        for jid in jids:
            if is_lid_user(jid):
                self._logger.warning("LID user found in LID fetch call")
                continue
            usync_query.with_user(USyncUser().with_id(jid))
        if not usync_query.users:
            return []

        results = await self.execute_usync_query(usync_query)
        if not results:
            return []
        mappings: list[dict[str, str]] = []
        for item in results.list:
            lid = item.data.get("lid")
            if isinstance(lid, str) and lid:
                mappings.append({"pn": item.id, "lid": lid})
        return mappings

    async def on_message_received(self, data: bytes) -> None:
        self._last_date_recv_ms = int(time.time() * 1000)
        if self._noise is None:
            await self._dispatch_frame(bytes(data))
            return
        await self._noise.decode_frame(bytes(data), self._dispatch_frame)

    async def logout(self, msg: str | None = None) -> None:
        auth = getattr(self.config, "auth", None)
        jid = None
        if auth:
            me = getattr(auth.creds, "me", None) or {}
            if hasattr(me, "model_dump"):
                me = me.model_dump(by_alias=True, exclude_none=True)
            jid = me.get("id") if isinstance(me, dict) else None
        if jid:
            await self.send_node(
                BinaryNode(
                    tag="iq",
                    attrs={"to": S_WHATSAPP_NET, "type": "set", "id": self.generate_message_tag(), "xmlns": "md"},
                    content=[BinaryNode(tag="remove-companion-device", attrs={"jid": jid, "reason": "user_initiated"})],
                )
            )
        await self.end(RuntimeError(msg or "Intentional Logout"))

    async def generate_pairing_key(self) -> bytes:
        auth = self._require_auth()
        pairing_code = auth.creds.pairing_code
        if not pairing_code:
            raise RuntimeError("pairing code is not set")
        salt = os.urandom(32)
        random_iv = os.urandom(16)
        key = derive_pairing_code_key(pairing_code, salt)
        ciphered = aes_encrypt_ctr(bytes(auth.creds.pairing_ephemeral_key_pair.public), key, random_iv)
        return salt + random_iv + ciphered

    async def request_pairing_code(self, phone_number: str, custom_pairing_code: str | None = None) -> str:
        auth = self._require_auth()
        pairing_code = custom_pairing_code or bytes_to_crockford(os.urandom(5))
        if custom_pairing_code is not None and len(custom_pairing_code) != 8:
            raise ValueError("Custom pairing code must be exactly 8 chars")

        auth.creds.pairing_code = pairing_code
        auth.creds.me = {"id": jid_encode(phone_number, "s.whatsapp.net"), "name": "~"}
        await self.ev.emit(
            "creds.update",
            {"pairingCode": pairing_code, "me": auth.creds.me},
        )

        browser = self.config.browser
        await self.send_node(
            BinaryNode(
                tag="iq",
                attrs={
                    "to": S_WHATSAPP_NET,
                    "type": "set",
                    "id": self.generate_message_tag(),
                    "xmlns": "md",
                },
                content=[
                    BinaryNode(
                        tag="link_code_companion_reg",
                        attrs={
                            "jid": auth.creds.me["id"],
                            "stage": "companion_hello",
                            "should_show_push_notification": "true",
                        },
                        content=[
                            BinaryNode(
                                tag="link_code_pairing_wrapped_companion_ephemeral_pub",
                                attrs={},
                                content=await self.generate_pairing_key(),
                            ),
                            BinaryNode(
                                tag="companion_server_auth_key_pub",
                                attrs={},
                                content=bytes(auth.creds.noise_key.public),
                            ),
                            BinaryNode(
                                tag="companion_platform_id",
                                attrs={},
                                content=get_platform_id(browser[1]),
                            ),
                            BinaryNode(
                                tag="companion_platform_display",
                                attrs={},
                                content=f"{browser[1]} ({browser[0]})",
                            ),
                            BinaryNode(
                                tag="link_code_pairing_nonce",
                                attrs={},
                                content="0",
                            ),
                        ],
                    )
                ],
            )
        )
        return pairing_code

    async def send_wam_buffer(self, wam_buffer: bytes | bytearray | None = None) -> BinaryNode:
        payload = bytes(wam_buffer) if wam_buffer is not None else encode_wam(self.wam_buffer)
        return await self.query_node(
            BinaryNode(
                tag="iq",
                attrs={"to": S_WHATSAPP_NET, "id": self.generate_message_tag(), "xmlns": "w:stats"},
                content=[
                    BinaryNode(
                        tag="add",
                        attrs={"t": str(round(time.time()))},
                        content=payload,
                    )
                ],
            )
        )

    async def end(self, error: Exception | None = None) -> None:
        if self._closed:
            self._logger.debug("connection already closed", extra={"error": str(error) if error else None})
            return
        self._closed = True
        self._did_start_buffer = False
        self._stop_keep_alive_request()
        await self.disconnect()
        await self.ev.emit(
            "connection.update",
            {"connection": "close", "lastDisconnect": {"error": error, "date": time.time()}},
        )
        remove_all = getattr(self.ev, "remove_all_listeners", None)
        if not callable(remove_all):
            remove_all = getattr(self.ev, "removeAllListeners", None)
        if callable(remove_all):
            remove_all("connection.update")

    async def wait_for_connection_update(
        self,
        check: Callable[[dict[str, Any]], Awaitable[bool] | bool],
        timeout_ms: int | None = None,
    ) -> None:
        waiter = bind_wait_for_connection_update(self.ev)

        async def _runner(update: dict[str, Any]) -> bool:
            result = check(update)
            if asyncio.iscoroutine(result):
                return bool(await result)
            return bool(result)

        await waiter(_runner, timeout_ms)

    def on_unexpected_error(self, err: Exception, msg: str) -> None:
        self._logger.error(f"unexpected error in '{msg}'", extra={"error": str(err)})

    def update_server_time_offset(self, node: BinaryNode) -> None:
        t_value = node.attrs.get("t")
        if not t_value:
            return
        try:
            parsed = int(t_value)
        except Exception:
            return
        if parsed <= 0:
            return
        self._server_time_offset_ms = parsed * 1000 - int(time.time() * 1000)

    def get_unified_session_id(self) -> str:
        offset_ms = 3 * TIME_MS["day"]
        now = int(time.time() * 1000) + self._server_time_offset_ms
        return str((now + offset_ms) % TIME_MS["week"])

    async def send_unified_session(self) -> None:
        if not self._is_transport_open():
            return
        try:
            await self.send_node(
                BinaryNode(
                    tag="ib",
                    attrs={},
                    content=[BinaryNode(tag="unified_session", attrs={"id": self.get_unified_session_id()})],
                )
            )
        except Exception as error:
            self._logger.debug("failed to send unified_session telemetry", extra={"error": str(error)})

    # camelCase aliases for Baileys parity
    awaitNextMessage = await_next_message
    waitForSocketOpen = wait_for_socket_open
    startKeepAliveRequest = start_keep_alive_request
    validateConnection = validate_connection
    sendPassiveIq = send_passive_iq
    getAvailablePreKeysOnServer = get_available_prekeys_on_server
    verifyCurrentPreKeyExists = verify_current_pre_key_exists
    uploadPreKeys = upload_pre_keys
    uploadPreKeysToServerIfRequired = upload_pre_keys_to_server_if_required
    digestKeyBundle = digest_key_bundle
    rotateSignedPreKey = rotate_signed_pre_key
    executeUSyncQuery = execute_usync_query
    onWhatsApp = on_whatsapp
    pnFromLIDUSync = pn_from_lid_usync
    sendUnifiedSession = send_unified_session
    updateServerTimeOffset = update_server_time_offset
    getUnifiedSessionId = get_unified_session_id
    onUnexpectedError = on_unexpected_error
    onMessageReceived = on_message_received
    requestPairingCode = request_pairing_code
    generatePairingKey = generate_pairing_key
    sendWAMBuffer = send_wam_buffer
    waitForConnectionUpdate = wait_for_connection_update
