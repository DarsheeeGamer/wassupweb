from __future__ import annotations

import asyncio
import base64
import json
import os
import time
from typing import Any

from ..defaults import (
    KEY_BUNDLE_TYPE,
    MIN_PREKEY_COUNT,
    PLACEHOLDER_MAX_AGE_SECONDS,
    STATUS_EXPIRY_SECONDS,
    S_WHATSAPP_NET,
)
from ..types.message import WAMessageStubType
from ..utils.crypto import Curve, aes_decrypt_ctr, aes_encrypt_gcm, derive_pairing_code_key, hkdf
from ..utils.decode_wa_message import (
    MISSING_KEYS_ERROR_TEXT,
    NACK_REASONS,
    NO_MESSAGE_FOUND_ERROR_TEXT,
    decode_message_node,
    decrypt_message_node,
)
from ..utils.generics import (
    encode_big_endian,
    get_status_from_receipt_type,
    to_number,
    unix_timestamp_seconds,
)
from ..utils.history import get_history_msg
from ..utils.identity_change_handler import TTLBoolCache, handle_identity_change
from ..utils.message_retry_manager import MessageRetryManager
from ..utils.messages_media import decode_media_retry_node
from ..utils.process_message import clean_message
from ..utils.signal import get_next_pre_keys, xmpp_pre_key, xmpp_signed_pre_key
from ..utils.validate_connection import encode_signed_device_identity
from ..wabinary import (
    are_jids_same_user,
    BinaryNode,
    binary_node_to_string,
    get_all_binary_node_children,
    get_binary_node_child,
    get_binary_node_child_buffer,
    get_binary_node_child_string,
    get_binary_node_children,
    is_jid_group,
    is_jid_newsletter,
    is_jid_status_broadcast,
    is_lid_user,
    is_pn_user,
    jid_decode,
    jid_normalized_user,
)
from .messages_send import MessagesSendSocket


class MessagesRecvSocket(MessagesSendSocket):
    _recv_handlers_attached: bool = False
    _recv_connection_handler_attached: bool = False
    _signal_repository: Any = None
    _msg_retry_counter_cache: dict[str, int]
    _identity_assert_debounce: TTLBoolCache
    _send_active_receipts: bool = False
    message_retry_manager: MessageRetryManager | None = None
    _offline_nodes: list[tuple[str, BinaryNode]]
    _offline_processing: bool

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._msg_retry_counter_cache = {}
        self._placeholder_resend_fallback_cache: dict[str, Any] = {}
        self._identity_assert_debounce = TTLBoolCache(ttl_ms=5_000)
        self._offline_nodes = []
        self._offline_processing = False
        if bool(getattr(self.config, "enable_recent_message_cache", True)):
            self.message_retry_manager = MessageRetryManager(
                logger=self._logger,
                max_msg_retry_count=int(getattr(self.config, "max_msg_retry_count", 5) or 5),
            )

    async def connect(self) -> None:
        await super().connect()
        self._ensure_signal_repository()
        if not self._recv_handlers_attached:
            self.ev.on("node:message", self._on_message_node)
            self.ev.on("node:receipt", self._on_receipt_node)
            self.ev.on("node:notification", self._on_notification_node)
            self.ev.on("node:ack", self._handle_ack_node)
            self._recv_handlers_attached = True
        if not self._recv_connection_handler_attached:
            self.ev.on("connection.update", self._handle_recv_connection_update)
            self._recv_connection_handler_attached = True

    @staticmethod
    def _is_offline_attr(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if value is None:
            return False
        text = str(value).strip().lower()
        return text not in {"", "0", "false", "none", "null"}

    def _report_node_error(self, error: Exception, identifier: str) -> None:
        on_unexpected_error = getattr(self, "on_unexpected_error", None)
        if callable(on_unexpected_error):
            on_unexpected_error(error, identifier)
            return
        self._logger.error(f"unexpected error in '{identifier}'", extra={"error": str(error)})

    async def _process_node_with_buffer(self, node: BinaryNode, identifier: str, handler: Any) -> None:
        buffer_fn = getattr(self.ev, "buffer", None)
        flush_fn = getattr(self.ev, "flush", None)
        if callable(buffer_fn):
            buffer_fn()
        try:
            await handler(node)
        except Exception as error:
            self._report_node_error(error, identifier)
        finally:
            if callable(flush_fn):
                flush_fn()

    async def _process_offline_nodes(self) -> None:
        if self._offline_processing:
            return
        self._offline_processing = True
        processed_in_batch = 0
        try:
            while self._offline_nodes:
                is_open = getattr(self, "_is_transport_open", None)
                if callable(is_open) and not is_open():
                    break

                node_type, node = self._offline_nodes.pop(0)
                handlers = {
                    "message": (self._handle_message_node, "processing message"),
                    "receipt": (self._handle_receipt_node, "handling receipt"),
                    "notification": (self._handle_notification_node, "handling notification"),
                }
                handler_entry = handlers.get(node_type)
                if handler_entry is None:
                    self._report_node_error(RuntimeError(f"unknown offline node type: {node_type}"), "processing offline node")
                    continue

                handler, identifier = handler_entry
                try:
                    await handler(node)
                except Exception as error:
                    self._report_node_error(error, identifier)

                processed_in_batch += 1
                if processed_in_batch >= 10:
                    processed_in_batch = 0
                    await asyncio.sleep(0)
        finally:
            self._offline_processing = False

    def _enqueue_offline_node(self, node_type: str, node: BinaryNode) -> None:
        self._offline_nodes.append((node_type, node))
        if self._offline_processing:
            return
        asyncio.create_task(self._process_offline_nodes())

    async def _dispatch_incoming_node(
        self,
        node_type: str,
        node: BinaryNode,
        identifier: str,
        handler: Any,
    ) -> None:
        if self._is_offline_attr(node.attrs.get("offline")):
            self._enqueue_offline_node(node_type, node)
            return
        await self._process_node_with_buffer(node, identifier, handler)

    async def _on_message_node(self, node: BinaryNode) -> None:
        await self._dispatch_incoming_node("message", node, "processing message", self._handle_message_node)

    async def _on_receipt_node(self, node: BinaryNode) -> None:
        await self._dispatch_incoming_node("receipt", node, "handling receipt", self._handle_receipt_node)

    async def _on_notification_node(self, node: BinaryNode) -> None:
        await self._dispatch_incoming_node("notification", node, "handling notification", self._handle_notification_node)

    async def _handle_recv_connection_update(self, update: dict[str, Any]) -> None:
        is_online = update.get("isOnline")
        if isinstance(is_online, bool):
            self._send_active_receipts = is_online

    def _ensure_signal_repository(self) -> None:
        if self._signal_repository is not None:
            return
        if not self.config.auth or not self.config.make_signal_repository:
            return
        try:
            self._signal_repository = self.config.make_signal_repository(self.config.auth, self._logger, None)
        except Exception as error:  # pragma: no cover - repository init is env-dependent
            self._logger.warning("failed to initialize signal repository", extra={"error": str(error)})
            self._signal_repository = None

    async def _cache_get(self, key: str) -> Any:
        cache = getattr(self.config, "placeholder_resend_cache", None)
        if cache:
            getter = getattr(cache, "get", None)
            if callable(getter):
                result = getter(key)
                if asyncio.iscoroutine(result):
                    return await result
                return result
            if isinstance(cache, dict):
                return cache.get(key)
        return self._placeholder_resend_fallback_cache.get(key)

    async def _cache_set(self, key: str, value: Any) -> None:
        cache = getattr(self.config, "placeholder_resend_cache", None)
        if cache:
            setter = getattr(cache, "set", None)
            if callable(setter):
                result = setter(key, value)
                if asyncio.iscoroutine(result):
                    await result
                self._placeholder_resend_fallback_cache[key] = value
                return
            if isinstance(cache, dict):
                cache[key] = value
        self._placeholder_resend_fallback_cache[key] = value

    async def _cache_del(self, key: str) -> None:
        cache = getattr(self.config, "placeholder_resend_cache", None)
        if cache:
            for name in ("del_", "del", "delete", "remove", "pop"):
                fn = getattr(cache, name, None)
                if not callable(fn):
                    continue
                try:
                    result = fn(key, None) if name == "pop" else fn(key)
                except TypeError:
                    result = fn(key)
                if asyncio.iscoroutine(result):
                    await result
                self._placeholder_resend_fallback_cache.pop(key, None)
                return
            if isinstance(cache, dict):
                cache.pop(key, None)
        self._placeholder_resend_fallback_cache.pop(key, None)

    async def _retry_count_get(self, key: str) -> int:
        cache = getattr(self.config, "msg_retry_counter_cache", None)
        if cache:
            getter = getattr(cache, "get", None)
            if callable(getter):
                value = getter(key)
                if asyncio.iscoroutine(value):
                    value = await value
                try:
                    return int(value or 0)
                except Exception:
                    return 0
            if isinstance(cache, dict):
                try:
                    return int(cache.get(key) or 0)
                except Exception:
                    return 0
        return int(self._msg_retry_counter_cache.get(key, 0) or 0)

    async def _retry_count_set(self, key: str, value: int) -> None:
        self._msg_retry_counter_cache[key] = int(value)
        cache = getattr(self.config, "msg_retry_counter_cache", None)
        if not cache:
            return
        setter = getattr(cache, "set", None)
        if callable(setter):
            result = setter(key, int(value))
            if asyncio.iscoroutine(result):
                await result
            return
        if isinstance(cache, dict):
            cache[key] = int(value)

    async def _retry_count_del(self, key: str) -> None:
        self._msg_retry_counter_cache.pop(key, None)
        cache = getattr(self.config, "msg_retry_counter_cache", None)
        if not cache:
            return
        for name in ("del_", "del", "delete", "remove", "pop"):
            fn = getattr(cache, name, None)
            if not callable(fn):
                continue
            try:
                result = fn(key, None) if name == "pop" else fn(key)
            except TypeError:
                result = fn(key)
            if asyncio.iscoroutine(result):
                await result
            return
        if isinstance(cache, dict):
            cache.pop(key, None)

    def _me_info(self) -> dict[str, Any]:
        auth = getattr(self.config, "auth", None)
        if not auth:
            return {}
        creds = getattr(auth, "creds", None)
        me = getattr(creds, "me", None) if creds else None
        if hasattr(me, "model_dump"):
            return me.model_dump(by_alias=True, exclude_none=True)
        if isinstance(me, dict):
            return dict(me)
        return {}

    @staticmethod
    def _to_int(value: Any, default: int = 0) -> int:
        try:
            return int(value)
        except Exception:
            return default

    async def send_peer_data_operation_message(self, operation: dict[str, Any]) -> str:
        me_id = self._me_info().get("id")
        if not me_id:
            raise RuntimeError("Not authenticated")
        protocol_message = {
            "protocolMessage": {
                "peerDataOperationRequestMessage": operation,
                "type": "PEER_DATA_OPERATION_REQUEST_MESSAGE",
            }
        }
        relay = getattr(self, "relay_message", None)
        if not callable(relay):
            raise RuntimeError("relay_message unavailable")
        return await relay(
            me_id,
            protocol_message,
            additional_attributes={"category": "peer", "push_priority": "high_force"},
            additional_nodes=[BinaryNode(tag="meta", attrs={"appdata": "default"})],
        )

    async def fetch_message_history(
        self,
        count: int,
        oldest_msg_key: dict[str, Any],
        oldest_msg_timestamp: int,
    ) -> str:
        if not self.config.auth or not getattr(self.config.auth.creds, "me", None):
            raise RuntimeError("Not authenticated")

        pdo_message = {
            "historySyncOnDemandRequest": {
                "chatJid": oldest_msg_key.get("remoteJid"),
                "oldestMsgFromMe": bool(oldest_msg_key.get("fromMe")),
                "oldestMsgId": oldest_msg_key.get("id"),
                "oldestMsgTimestampMs": oldest_msg_timestamp,
                "onDemandMsgCount": count,
            },
            "peerDataOperationRequestType": "HISTORY_SYNC_ON_DEMAND",
        }
        return await self.send_peer_data_operation_message(pdo_message)

    async def request_placeholder_resend(
        self,
        message_key: dict[str, Any],
        msg_data: dict[str, Any] | None = None,
    ) -> str | None:
        if not self.config.auth or not getattr(self.config.auth.creds, "me", None):
            raise RuntimeError("Not authenticated")

        msg_id = str(message_key.get("id") or "")
        if not msg_id:
            return None

        if await self._cache_get(msg_id):
            self._logger.debug("already requested resend", extra={"messageKey": message_key})
            return None

        await self._cache_set(msg_id, msg_data or True)
        await asyncio.sleep(2)

        if not await self._cache_get(msg_id):
            self._logger.debug("message received while resend requested", extra={"messageKey": message_key})
            return "RESOLVED"

        pdo_message = {
            "placeholderMessageResendRequest": [{"messageKey": message_key}],
            "peerDataOperationRequestType": "PLACEHOLDER_MESSAGE_RESEND",
        }
        request_id = await self.send_peer_data_operation_message(pdo_message)

        async def _cleanup() -> None:
            await asyncio.sleep(8)
            if await self._cache_get(msg_id):
                self._logger.debug("PDO message without response after 8 seconds", extra={"messageKey": message_key})
                await self._cache_del(msg_id)

        asyncio.create_task(_cleanup())
        return request_id

    async def _handle_mex_newsletter_notification(self, node: BinaryNode) -> None:
        mex_node = get_binary_node_child(node, "mex")
        if not mex_node or mex_node.content is None:
            self._logger.warning("Invalid mex newsletter notification", extra={"node": node})
            return

        try:
            if isinstance(mex_node.content, (bytes, bytearray)):
                data = json.loads(bytes(mex_node.content).decode("utf-8"))
            elif isinstance(mex_node.content, str):
                data = json.loads(mex_node.content)
            else:
                self._logger.warning("Invalid mex payload type", extra={"node": node})
                return
        except Exception as error:
            self._logger.error("Failed to parse mex newsletter notification", extra={"error": str(error)})
            return

        operation = data.get("operation")
        updates = data.get("updates")
        if not operation or not isinstance(updates, list):
            self._logger.warning("Invalid mex newsletter notification content", extra={"data": data})
            return

        if operation == "NotificationNewsletterUpdate":
            for update in updates:
                jid = update.get("jid")
                settings = update.get("settings") or {}
                if jid and isinstance(settings, dict) and settings:
                    await self.ev.emit("newsletter-settings.update", {"id": jid, "update": settings})
            return

        if operation == "NotificationNewsletterAdminPromote":
            for update in updates:
                jid = update.get("jid")
                user = update.get("user")
                if jid and user:
                    await self.ev.emit(
                        "newsletter-participants.update",
                        {
                            "id": jid,
                            "author": node.attrs.get("from", ""),
                            "user": user,
                            "new_role": "ADMIN",
                            "action": "promote",
                        },
                    )
            return

        self._logger.info("Unhandled mex newsletter notification", extra={"operation": operation})

    async def _handle_newsletter_notification(self, node: BinaryNode) -> None:
        from_jid = node.attrs.get("from", "")
        author = node.attrs.get("participant", "")
        children = get_all_binary_node_children(node)
        if not children:
            return
        child = children[0]

        if child.tag == "reaction":
            await self.ev.emit(
                "newsletter.reaction",
                {
                    "id": from_jid,
                    "server_id": child.attrs.get("message_id", ""),
                    "reaction": {"code": get_binary_node_child_string(child, "reaction"), "count": 1},
                },
            )
            return

        if child.tag == "view":
            count_value = 0
            content = child.content
            try:
                if isinstance(content, (bytes, bytearray)):
                    count_value = int(bytes(content).decode("utf-8") or 0)
                elif isinstance(content, str):
                    count_value = int(content or 0)
            except Exception:
                count_value = 0
            await self.ev.emit(
                "newsletter.view",
                {
                    "id": from_jid,
                    "server_id": child.attrs.get("message_id", ""),
                    "count": count_value,
                },
            )
            return

        if child.tag == "participant":
            await self.ev.emit(
                "newsletter-participants.update",
                {
                    "id": from_jid,
                    "author": author,
                    "user": child.attrs.get("jid", ""),
                    "action": child.attrs.get("action", ""),
                    "new_role": child.attrs.get("role", ""),
                },
            )
            return

        if child.tag == "update":
            settings_node = get_binary_node_child(child, "settings")
            if settings_node:
                update: dict[str, Any] = {}
                name_node = get_binary_node_child(settings_node, "name")
                description_node = get_binary_node_child(settings_node, "description")
                if isinstance(name_node.content if name_node else None, (bytes, bytearray)):
                    update["name"] = bytes(name_node.content).decode("utf-8")
                elif isinstance(name_node.content if name_node else None, str):
                    update["name"] = name_node.content
                if isinstance(description_node.content if description_node else None, (bytes, bytearray)):
                    update["description"] = bytes(description_node.content).decode("utf-8")
                elif isinstance(description_node.content if description_node else None, str):
                    update["description"] = description_node.content
                if update:
                    await self.ev.emit("newsletter-settings.update", {"id": from_jid, "update": update})
            return

        if child.tag == "message":
            plaintext_node = get_binary_node_child(child, "plaintext")
            if plaintext_node and isinstance(plaintext_node.content, (bytes, bytearray, str)):
                try:
                    if isinstance(plaintext_node.content, str):
                        payload = plaintext_node.content.encode("latin1")
                    else:
                        payload = bytes(plaintext_node.content)
                    message_proto = self._decode_proto_message("Message", payload)
                    full_message = {
                        "key": {
                            "remoteJid": from_jid,
                            "id": child.attrs.get("message_id") or child.attrs.get("server_id"),
                            "fromMe": False,
                        },
                        "message": message_proto,
                        "messageTimestamp": int(child.attrs.get("t") or 0),
                    }
                    upsert = getattr(self, "upsert_message", None)
                    if callable(upsert):
                        await upsert(full_message, "append")
                    else:
                        await self.ev.emit("messages.upsert", {"messages": [full_message], "type": "append"})
                except Exception as error:
                    self._logger.error("Failed to decode plaintext newsletter message", extra={"error": str(error)})

    async def _handle_privacy_token_notification(self, node: BinaryNode) -> None:
        tokens_node = get_binary_node_child(node, "tokens")
        if not tokens_node:
            return
        auth = getattr(self.config, "auth", None)
        if not auth:
            return

        from_jid = jid_normalized_user(node.attrs.get("from"))
        for token_node in get_binary_node_children(tokens_node, "token"):
            if token_node.attrs.get("type") != "trusted_contact":
                continue
            if not isinstance(token_node.content, (bytes, bytearray)):
                continue
            timestamp = token_node.attrs.get("t")
            await auth.keys.set({"tctoken": {from_jid: {"token": bytes(token_node.content), "timestamp": timestamp}}})

    async def _handle_encrypt_notification(self, node: BinaryNode) -> None:
        from_jid = node.attrs.get("from")
        if from_jid == S_WHATSAPP_NET:
            count_child = get_binary_node_child(node, "count")
            count = self._to_int(count_child.attrs.get("value") if count_child else None)
            should_upload = count < MIN_PREKEY_COUNT
            self._logger.debug("recv pre-key count", extra={"count": count, "shouldUploadMorePreKeys": should_upload})
            if should_upload:
                uploader = getattr(self, "upload_pre_keys", None)
                if callable(uploader):
                    await uploader()
            return

        validate_session = None
        if self._signal_repository is not None:
            validate_session = getattr(self._signal_repository, "validate_session", None)
        if not callable(validate_session):
            self._logger.info("unknown encrypt notification", extra={"node": node})
            return

        assert_sessions = getattr(self, "assert_sessions", None)
        if not callable(assert_sessions):
            async def _assert_sessions(_jids: list[str], _force: bool) -> None:
                return

            assert_sessions = _assert_sessions

        me_info = self._me_info()
        result = await handle_identity_change(
            node,
            {
                "meId": me_info.get("id"),
                "meLid": me_info.get("lid"),
                "validateSession": validate_session,
                "assertSessions": assert_sessions,
                "debounceCache": self._identity_assert_debounce,
                "logger": self._logger,
            },
        )
        if result.get("action") == "no_identity_node":
            self._logger.info("unknown encrypt notification", extra={"node": node})

    @staticmethod
    def _required_bytes(data: bytes | bytearray | str | None) -> bytes:
        if data is None:
            raise RuntimeError("Invalid buffer")
        if isinstance(data, bytes):
            return data
        if isinstance(data, bytearray):
            return bytes(data)
        return data.encode("latin1")

    async def _decipher_link_public_key(self, wrapped: bytes | bytearray | str | None) -> bytes:
        auth = getattr(self.config, "auth", None)
        creds = getattr(auth, "creds", None) if auth else None
        pairing_code = getattr(creds, "pairing_code", None) if creds else None
        if not pairing_code:
            raise RuntimeError("Missing pairing code")

        buffer = self._required_bytes(wrapped)
        salt = buffer[:32]
        secret_key = derive_pairing_code_key(pairing_code, salt)
        iv = buffer[32:48]
        payload = buffer[48:80]
        return aes_decrypt_ctr(payload, secret_key, iv)

    async def _handle_link_code_companion_registration(self, node: BinaryNode) -> None:
        auth = getattr(self.config, "auth", None)
        creds = getattr(auth, "creds", None) if auth else None
        if not auth or not creds:
            return

        link_node = get_binary_node_child(node, "link_code_companion_reg")
        if not link_node:
            return

        me_info = self._me_info()
        me_id = me_info.get("id")
        if not me_id:
            return

        ref = self._required_bytes(get_binary_node_child_buffer(link_node, "link_code_pairing_ref"))
        primary_identity_public_key = self._required_bytes(get_binary_node_child_buffer(link_node, "primary_identity_pub"))
        wrapped_primary_ephemeral = self._required_bytes(
            get_binary_node_child_buffer(link_node, "link_code_pairing_wrapped_primary_ephemeral_pub")
        )

        pairing_public_key = await self._decipher_link_public_key(wrapped_primary_ephemeral)
        companion_shared_key = Curve.shared_key(bytes(creds.pairing_ephemeral_key_pair.private), pairing_public_key)
        random = os.urandom(32)
        link_code_salt = os.urandom(32)
        pairing_expanded = hkdf(
            companion_shared_key,
            32,
            salt=link_code_salt,
            info="link_code_pairing_key_bundle_encryption_key",
        )

        encrypt_payload = b"".join(
            [
                bytes(creds.signed_identity_key.public),
                primary_identity_public_key,
                random,
            ]
        )
        encrypt_iv = os.urandom(12)
        encrypted = aes_encrypt_gcm(encrypt_payload, pairing_expanded, encrypt_iv, b"")
        encrypted_payload = b"".join([link_code_salt, encrypt_iv, encrypted])

        identity_shared_key = Curve.shared_key(bytes(creds.signed_identity_key.private), primary_identity_public_key)
        identity_payload = b"".join([companion_shared_key, identity_shared_key, random])
        creds.adv_secret_key = base64.b64encode(hkdf(identity_payload, 32, info="adv_secret")).decode("ascii")

        await self.query_node(
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
                        attrs={"jid": me_id, "stage": "companion_finish"},
                        content=[
                            BinaryNode(
                                tag="link_code_pairing_wrapped_key_bundle",
                                attrs={},
                                content=encrypted_payload,
                            ),
                            BinaryNode(
                                tag="companion_identity_public",
                                attrs={},
                                content=bytes(creds.signed_identity_key.public),
                            ),
                            BinaryNode(
                                tag="link_code_pairing_ref",
                                attrs={},
                                content=ref,
                            ),
                        ],
                    )
                ],
            )
        )

        creds.registered = True
        await self.ev.emit("creds.update", creds.model_dump(by_alias=True, exclude_none=True))

    async def _extract_group_metadata(self, node: BinaryNode) -> dict[str, Any]:
        try:
            from .groups import extract_group_metadata  # local import avoids socket-layer cycle

            return extract_group_metadata(node).model_dump(by_alias=True, exclude_none=True)
        except Exception:
            group = get_binary_node_child(node, "group")
            if not group:
                return {}
            group_id = group.attrs.get("id", "")
            if group_id and "@" not in group_id:
                group_id = f"{group_id}@g.us"
            return {
                "id": group_id,
                "subject": group.attrs.get("subject", ""),
                "owner": jid_normalized_user(group.attrs.get("creator")) if group.attrs.get("creator") else None,
                "ownerPn": jid_normalized_user(group.attrs.get("creator_pn")) if group.attrs.get("creator_pn") else None,
                "creation": self._to_int(group.attrs.get("creation"), 0),
            }

    @staticmethod
    def _decode_text(data: Any) -> str | None:
        if isinstance(data, bytes):
            return data.decode("utf-8", errors="ignore")
        if isinstance(data, bytearray):
            return bytes(data).decode("utf-8", errors="ignore")
        if isinstance(data, str):
            return data
        return None

    async def _handle_group_notification(self, full_node: BinaryNode, child: BinaryNode, msg: dict[str, Any]) -> None:
        acting_participant_lid = full_node.attrs.get("participant")
        acting_participant_pn = full_node.attrs.get("participant_pn")

        affected_participant = get_binary_node_child(child, "participant")
        affected_participant_lid = (
            affected_participant.attrs.get("jid") if affected_participant else acting_participant_lid
        )
        affected_participant_pn = (
            affected_participant.attrs.get("phone_number") if affected_participant else acting_participant_pn
        )

        if child.tag == "create":
            metadata = await self._extract_group_metadata(child)
            subject = metadata.get("subject") or ""
            msg["messageStubType"] = int(WAMessageStubType.GROUP_CREATE)
            msg["messageStubParameters"] = [subject]
            msg["key"] = {
                "participant": metadata.get("owner"),
                "participantAlt": metadata.get("ownerPn"),
            }
            await self.ev.emit(
                "chats.upsert",
                [
                    {
                        "id": metadata.get("id"),
                        "name": subject,
                        "conversationTimestamp": metadata.get("creation"),
                    }
                ],
            )
            await self.ev.emit(
                "groups.upsert",
                [{**metadata, "author": acting_participant_lid, "authorPn": acting_participant_pn}],
            )
            return

        if child.tag in {"ephemeral", "not_ephemeral"}:
            msg["message"] = {
                "protocolMessage": {
                    "type": "EPHEMERAL_SETTING",
                    "ephemeralExpiration": self._to_int(child.attrs.get("expiration"), 0),
                }
            }
            return

        if child.tag == "modify":
            old_numbers = [p.attrs.get("jid", "") for p in get_binary_node_children(child, "participant")]
            msg["messageStubParameters"] = old_numbers
            msg["messageStubType"] = int(WAMessageStubType.GROUP_PARTICIPANT_CHANGE_NUMBER)
            return

        if child.tag in {"promote", "demote", "remove", "add", "leave"}:
            stub_map = {
                "promote": WAMessageStubType.GROUP_PARTICIPANT_PROMOTE,
                "demote": WAMessageStubType.GROUP_PARTICIPANT_DEMOTE,
                "remove": WAMessageStubType.GROUP_PARTICIPANT_REMOVE,
                "add": WAMessageStubType.GROUP_PARTICIPANT_ADD,
                "leave": WAMessageStubType.GROUP_PARTICIPANT_LEAVE,
            }
            msg["messageStubType"] = int(stub_map[child.tag])

            participants = []
            for item in get_binary_node_children(child, "participant"):
                jid = item.attrs.get("jid", "")
                participants.append(
                    {
                        "id": jid,
                        "phoneNumber": item.attrs.get("phone_number")
                        if is_lid_user(jid) and is_pn_user(item.attrs.get("phone_number"))
                        else None,
                        "lid": item.attrs.get("lid")
                        if is_pn_user(jid) and is_lid_user(item.attrs.get("lid"))
                        else None,
                        "admin": item.attrs.get("type"),
                    }
                )

            if (
                len(participants) == 1
                and child.tag == "remove"
                and (
                    are_jids_same_user(participants[0].get("id"), acting_participant_lid)
                    or are_jids_same_user(participants[0].get("id"), acting_participant_pn)
                )
            ):
                msg["messageStubType"] = int(WAMessageStubType.GROUP_PARTICIPANT_LEAVE)

            msg["messageStubParameters"] = [json.dumps(item) for item in participants]
            return

        if child.tag == "subject":
            msg["messageStubType"] = int(WAMessageStubType.GROUP_CHANGE_SUBJECT)
            msg["messageStubParameters"] = [child.attrs.get("subject", "")]
            return

        if child.tag == "description":
            body = get_binary_node_child(child, "body")
            description = self._decode_text(body.content if body else None)
            msg["messageStubType"] = int(WAMessageStubType.GROUP_CHANGE_DESCRIPTION)
            if description:
                msg["messageStubParameters"] = [description]
            return

        if child.tag in {"announcement", "not_announcement"}:
            msg["messageStubType"] = int(WAMessageStubType.GROUP_CHANGE_ANNOUNCE)
            msg["messageStubParameters"] = ["on" if child.tag == "announcement" else "off"]
            return

        if child.tag in {"locked", "unlocked"}:
            msg["messageStubType"] = int(WAMessageStubType.GROUP_CHANGE_RESTRICT)
            msg["messageStubParameters"] = ["on" if child.tag == "locked" else "off"]
            return

        if child.tag == "invite":
            msg["messageStubType"] = int(WAMessageStubType.GROUP_CHANGE_INVITE_LINK)
            msg["messageStubParameters"] = [child.attrs.get("code", "")]
            return

        if child.tag == "member_add_mode":
            add_mode = self._decode_text(child.content)
            if add_mode is not None:
                msg["messageStubType"] = int(WAMessageStubType.GROUP_MEMBER_ADD_MODE)
                msg["messageStubParameters"] = [add_mode]
            return

        if child.tag == "membership_approval_mode":
            approval_mode = get_binary_node_child(child, "group_join")
            if approval_mode:
                msg["messageStubType"] = int(WAMessageStubType.GROUP_MEMBERSHIP_JOIN_APPROVAL_MODE)
                msg["messageStubParameters"] = [approval_mode.attrs.get("state", "")]
            return

        if child.tag == "created_membership_requests":
            msg["messageStubType"] = int(WAMessageStubType.GROUP_MEMBERSHIP_JOIN_APPROVAL_REQUEST_NON_ADMIN_ADD)
            msg["messageStubParameters"] = [
                json.dumps({"lid": affected_participant_lid, "pn": affected_participant_pn}),
                "created",
                child.attrs.get("request_method", ""),
            ]
            return

        if child.tag == "revoked_membership_requests":
            is_denied = are_jids_same_user(affected_participant_lid, acting_participant_lid)
            msg["messageStubType"] = int(WAMessageStubType.GROUP_MEMBERSHIP_JOIN_APPROVAL_REQUEST_NON_ADMIN_ADD)
            msg["messageStubParameters"] = [
                json.dumps({"lid": affected_participant_lid, "pn": affected_participant_pn}),
                "revoked" if is_denied else "rejected",
            ]

    async def _upsert_notification_message(self, node: BinaryNode, msg: dict[str, Any]) -> None:
        remote_jid = node.attrs.get("from")
        if not remote_jid:
            return

        me_info = self._me_info()
        from_me = are_jids_same_user(node.attrs.get("participant") or remote_jid, me_info.get("id"))
        key = {
            "remoteJid": remote_jid,
            "fromMe": from_me,
            "participant": node.attrs.get("participant"),
            "id": node.attrs.get("id"),
        }
        key.update(msg.get("key") or {})
        msg["key"] = key
        if not msg.get("participant"):
            msg["participant"] = node.attrs.get("participant")
        msg["messageTimestamp"] = self._to_int(node.attrs.get("t"), int(time.time()))

        upsert = getattr(self, "upsert_message", None)
        if callable(upsert):
            await upsert(msg, "append")
        else:
            await self.ev.emit("messages.upsert", {"messages": [msg], "type": "append"})

    async def will_send_message_again(self, message_id: str, participant: str) -> bool:
        key = f"{message_id}:{participant}"
        retry_count = await self._retry_count_get(key)
        return retry_count < int(getattr(self.config, "max_msg_retry_count", 5) or 5)

    async def update_send_message_again_count(self, message_id: str, participant: str) -> None:
        key = f"{message_id}:{participant}"
        retry_count = await self._retry_count_get(key)
        await self._retry_count_set(key, retry_count + 1)

    async def send_messages_again(self, key: dict[str, Any], ids: list[str], retry_node: BinaryNode) -> None:
        remote_jid = key.get("remoteJid") or ""
        participant = key.get("participant") or remote_jid
        if not remote_jid or not participant:
            return

        send_to_all = not (jid_decode(participant) or {}).get("device")

        retry_count = self._to_int(retry_node.attrs.get("count"), 1)
        enable_auto_session_recreation = bool(getattr(self.config, "enable_auto_session_recreation", True))
        auth = getattr(self.config, "auth", None)
        if (
            enable_auto_session_recreation
            and self.message_retry_manager
            and self._signal_repository
            and retry_count > 1
            and auth
        ):
            try:
                has_session = await self._signal_repository.validate_session(participant)
                result = self.message_retry_manager.should_recreate_session(participant, bool((has_session or {}).get("exists")))
                if result.get("recreate"):
                    session_id = self._signal_repository.jid_to_signal_protocol_address(participant)
                    await auth.keys.set({"session": {session_id: None}})
            except Exception as error:
                self._logger.warning(
                    "failed to check session recreation for outgoing retry",
                    extra={"error": str(error), "participant": participant},
                )

        for msg_id in ids:
            if not msg_id:
                continue
            msg = None
            if self.message_retry_manager:
                cached_msg = self.message_retry_manager.get_recent_message(remote_jid, msg_id)
                if cached_msg:
                    msg = cached_msg.message
                    self.message_retry_manager.mark_retry_success(msg_id)

            if msg is None:
                get_message = getattr(self.config, "get_message", None)
                msg = await get_message({**key, "id": msg_id}) if callable(get_message) else None
                if msg is not None and self.message_retry_manager:
                    self.message_retry_manager.mark_retry_success(msg_id)

            if not msg:
                if self.message_retry_manager:
                    self.message_retry_manager.mark_retry_failed(msg_id)
                continue
            if not await self.will_send_message_again(msg_id, participant):
                continue

            await self.update_send_message_again_count(msg_id, participant)
            relay_message = getattr(self, "relay_message", None)
            if callable(relay_message):
                if send_to_all:
                    self._logger.debug("retry relay to all devices", extra={"remoteJid": remote_jid, "id": msg_id})
                await relay_message(remote_jid, msg, message_id=msg_id)

    async def _with_mutex(self, name: str, task: Any) -> Any:
        mutex = getattr(self, name, None)
        if mutex and callable(getattr(mutex, "mutex", None)):
            return await mutex.mutex(task)
        result = task()
        if asyncio.iscoroutine(result):
            return await result
        return result

    async def send_retry_request(self, node: BinaryNode, force_include_keys: bool = False) -> None:
        auth = getattr(self.config, "auth", None)
        if not auth:
            return

        me_info = self._me_info()
        decoded = decode_message_node(node, me_info.get("id", ""), me_info.get("lid", "") or "")
        msg_key = (decoded.get("fullMessage") or {}).get("key") or {}
        msg_id = msg_key.get("id")
        if not msg_id:
            return

        if self.message_retry_manager:
            if self.message_retry_manager.has_exceeded_max_retries(msg_id):
                self.message_retry_manager.mark_retry_failed(msg_id)
                return
            retry_count = self.message_retry_manager.increment_retry_count(msg_id)
            await self._retry_count_set(f"{msg_id}:{msg_key.get('participant') or ''}", retry_count)
        else:
            participant_key = msg_key.get("participant") or ""
            retry_cache_key = f"{msg_id}:{participant_key}"
            retry_count = await self._retry_count_get(retry_cache_key)
            max_retry = int(getattr(self.config, "max_msg_retry_count", 5) or 5)
            if retry_count >= max_retry:
                await self._retry_count_del(retry_cache_key)
                self._logger.debug("reached retry limit, clearing", extra={"retryCount": retry_count, "msgId": msg_id})
                return
            retry_count += 1
            await self._retry_count_set(retry_cache_key, retry_count)

        if retry_count <= 2:
            async def _request_phone_resend() -> None:
                try:
                    await self.request_placeholder_resend(
                        {
                            "remoteJid": msg_key.get("remoteJid"),
                            "fromMe": bool(msg_key.get("fromMe")),
                            "id": msg_key.get("id"),
                            "participant": msg_key.get("participant"),
                        }
                    )
                except Exception as error:
                    self._logger.warning("failed to request placeholder resend", extra={"error": str(error), "msgId": msg_id})

            if self.message_retry_manager:
                loop = asyncio.get_running_loop()

                def _schedule() -> None:
                    loop.call_soon_threadsafe(lambda: asyncio.create_task(_request_phone_resend()))

                self.message_retry_manager.schedule_phone_request(msg_id, _schedule)
            else:
                await _request_phone_resend()

        creds = auth.creds
        account = self._as_dict(getattr(creds, "account", None))
        device_identity = encode_signed_device_identity(account, True) if account else b""

        async def _tx() -> None:
            receipt = BinaryNode(
                tag="receipt",
                attrs={"id": msg_id, "type": "retry", "to": node.attrs.get("from", "")},
                content=[
                    BinaryNode(
                        tag="retry",
                        attrs={
                            "count": str(retry_count),
                            "id": node.attrs.get("id", ""),
                            "t": node.attrs.get("t", ""),
                            "v": "1",
                            "error": "0",
                        },
                    ),
                    BinaryNode(
                        tag="registration",
                        attrs={},
                        content=encode_big_endian(int(creds.registration_id)),
                    ),
                ],
            )

            if node.attrs.get("recipient"):
                receipt.attrs["recipient"] = node.attrs["recipient"]
            if node.attrs.get("participant"):
                receipt.attrs["participant"] = node.attrs["participant"]

            if retry_count > 1 or force_include_keys:
                generated = await get_next_pre_keys(auth, 1)
                pre_keys = generated.get("preKeys") or {}
                update = generated.get("update") or {}
                if pre_keys:
                    key_id = next(iter(pre_keys.keys()))
                    key = pre_keys[key_id]
                    keys_content = [
                        BinaryNode(tag="type", attrs={}, content=KEY_BUNDLE_TYPE),
                        BinaryNode(tag="identity", attrs={}, content=bytes(creds.signed_identity_key.public)),
                        xmpp_pre_key(key, int(key_id)),
                        xmpp_signed_pre_key(creds.signed_pre_key),
                    ]
                    if device_identity:
                        keys_content.append(BinaryNode(tag="device-identity", attrs={}, content=device_identity))

                    if isinstance(receipt.content, list):
                        receipt.content.append(BinaryNode(tag="keys", attrs={}, content=keys_content))
                    await self.ev.emit("creds.update", update)

            await self.send_node(receipt)
            self._logger.info("sent retry receipt", extra={"msgAttrs": node.attrs, "retryCount": retry_count})

        tx = getattr(auth.keys, "transaction", None)
        if callable(tx):
            await tx(_tx, me_info.get("id") or "sendRetryRequest")
        else:
            await _tx()

    async def _handle_message_node(self, node: BinaryNode) -> None:
        should_ignore = getattr(self.config, "should_ignore_jid", lambda _jid: False)
        if should_ignore(node.attrs.get("from", "")) and node.attrs.get("from") != S_WHATSAPP_NET:
            await self.send_message_ack(node, NACK_REASONS["UnhandledError"])
            return

        enc_node = get_binary_node_child(node, "enc")
        if enc_node and enc_node.attrs.get("type") == "msmsg":
            await self.send_message_ack(node, NACK_REASONS["MissingMessageSecret"])
            return

        me_info = self._me_info()
        me_id = me_info.get("id") or ""
        me_lid = me_info.get("lid") or ""
        if not self._signal_repository:
            await self.send_message_ack(node, NACK_REASONS["UnhandledError"])
            return

        try:
            decrypted = decrypt_message_node(node, me_id, me_lid, self._signal_repository, self._logger)
            msg = decrypted["fullMessage"]
            category = decrypted.get("category")
            author = decrypted.get("author")
            decrypt = decrypted["decrypt"]
        except Exception as error:
            self._logger.error(
                "error in decoding message",
                extra={"error": str(error), "node": binary_node_to_string(node)},
            )
            await self.send_message_ack(node, NACK_REASONS["ParsingError"])
            return

        async def _work() -> None:
            await decrypt()

            key = msg.get("key") or {}
            msg_remote_jid = key.get("remoteJid")
            msg_id = key.get("id")
            if self.message_retry_manager and msg_remote_jid and msg_id and msg.get("message"):
                self.message_retry_manager.add_recent_message(msg_remote_jid, msg_id, msg.get("message"))

            is_ciphertext_stub = int(msg.get("messageStubType") or 0) == int(WAMessageStubType.CIPHERTEXT)
            if is_ciphertext_stub and msg.get("category") != "peer":
                stub_error = (msg.get("messageStubParameters") or [""])[0]

                if stub_error == MISSING_KEYS_ERROR_TEXT:
                    await self.send_message_ack(node, NACK_REASONS["ParsingError"])
                    return

                if stub_error == NO_MESSAGE_FOUND_ERROR_TEXT:
                    unavailable_node = get_binary_node_child(node, "unavailable")
                    unavailable_type = unavailable_node.attrs.get("type") if unavailable_node else None
                    if unavailable_type not in {
                        "bot_unavailable_fanout",
                        "hosted_unavailable_fanout",
                        "view_once_unavailable_fanout",
                    }:
                        message_age = unix_timestamp_seconds() - to_number(msg.get("messageTimestamp"))
                        if message_age <= PLACEHOLDER_MAX_AGE_SECONDS:
                            clean_key = {
                                "remoteJid": (msg.get("key") or {}).get("remoteJid"),
                                "fromMe": bool((msg.get("key") or {}).get("fromMe")),
                                "id": (msg.get("key") or {}).get("id"),
                                "participant": (msg.get("key") or {}).get("participant"),
                            }
                            msg_data = {
                                "key": msg.get("key"),
                                "messageTimestamp": msg.get("messageTimestamp"),
                                "pushName": msg.get("pushName"),
                                "participant": msg.get("participant"),
                                "verifiedBizName": msg.get("verifiedBizName"),
                            }
                            try:
                                request_id = await self.request_placeholder_resend(clean_key, msg_data)
                                if request_id and request_id != "RESOLVED":
                                    await self.ev.emit(
                                        "messages.update",
                                        [
                                            {
                                                "key": msg.get("key"),
                                                "update": {"messageStubParameters": [NO_MESSAGE_FOUND_ERROR_TEXT, request_id]},
                                            }
                                        ],
                                    )
                            except Exception as error:
                                self._logger.warning(
                                    "failed to request placeholder resend for unavailable message",
                                    extra={"error": str(error), "msgId": (msg.get("key") or {}).get("id")},
                                )
                    await self.send_message_ack(node)
                else:
                    if is_jid_status_broadcast((msg.get("key") or {}).get("remoteJid")):
                        message_age = unix_timestamp_seconds() - to_number(msg.get("messageTimestamp"))
                        if message_age > STATUS_EXPIRY_SECONDS:
                            await self.send_message_ack(node)
                            return

                    async def _retry() -> None:
                        if not getattr(self, "_is_transport_open", lambda: True)():
                            return
                        try:
                            await self.send_retry_request(node, force_include_keys=enc_node is None)
                            delay_ms = int(getattr(self.config, "retry_request_delay_ms", 0) or 0)
                            if delay_ms > 0:
                                await asyncio.sleep(delay_ms / 1000.0)
                        except Exception as error:
                            self._logger.error("failed to handle retry", extra={"error": str(error)})
                        await self.send_message_ack(node, NACK_REASONS["UnhandledError"])

                    await self._with_mutex("retry_mutex", _retry)
            else:
                if self.message_retry_manager and msg_id:
                    self.message_retry_manager.cancel_pending_phone_request(msg_id)

                is_newsletter = is_jid_newsletter((msg.get("key") or {}).get("remoteJid", ""))
                if not is_newsletter:
                    receipt_type: str | None = None
                    participant = (msg.get("key") or {}).get("participant")
                    if category == "peer":
                        receipt_type = "peer_msg"
                    elif (msg.get("key") or {}).get("fromMe"):
                        receipt_type = "sender"
                        remote = (msg.get("key") or {}).get("remoteJid")
                        remote_alt = (msg.get("key") or {}).get("remoteJidAlt")
                        if is_lid_user(remote) or is_lid_user(remote_alt):
                            participant = author
                    elif not self._send_active_receipts:
                        receipt_type = "inactive"

                    await self.send_receipt(
                        jid=(msg.get("key") or {}).get("remoteJid", ""),
                        participant=participant,
                        message_ids=[(msg.get("key") or {}).get("id", "")],
                        receipt_type=receipt_type,
                    )

                    if get_history_msg(msg.get("message") or {}):
                        await self.send_receipt(
                            jid=jid_normalized_user((msg.get("key") or {}).get("remoteJid", "")),
                            participant=None,
                            message_ids=[(msg.get("key") or {}).get("id", "")],
                            receipt_type="hist_sync",
                        )
                else:
                    await self.send_message_ack(node)

            clean_message(msg, me_id, me_lid)
            upsert = getattr(self, "upsert_message", None)
            if callable(upsert):
                await upsert(msg, "append" if node.attrs.get("offline") else "notify")
            else:
                await self.ev.emit(
                    "messages.upsert",
                    {"messages": [msg], "type": "append" if node.attrs.get("offline") else "notify"},
                )

        try:
            await self._with_mutex("message_mutex", _work)
        except Exception as error:
            self._logger.error("error in handling message", extra={"error": str(error), "node": binary_node_to_string(node)})

    async def _handle_receipt_node(self, node: BinaryNode) -> None:
        attrs = node.attrs
        from_jid = attrs.get("from") or ""
        me_info = {}
        auth = getattr(self.config, "auth", None)
        if auth:
            me_value = getattr(auth.creds, "me", None) or {}
            if hasattr(me_value, "model_dump"):
                me_info = me_value.model_dump(by_alias=True, exclude_none=True)
            elif isinstance(me_value, dict):
                me_info = me_value

        is_lid = "lid" in from_jid
        me_jid = me_info.get("lid") if is_lid else me_info.get("id")
        is_node_from_me = are_jids_same_user(attrs.get("participant") or from_jid, me_jid)

        remote_jid = from_jid if (not is_node_from_me or is_jid_group(from_jid)) else (attrs.get("recipient") or from_jid)
        should_ignore = getattr(self.config, "should_ignore_jid", lambda _jid: False)
        try:
            if should_ignore(remote_jid) and remote_jid != S_WHATSAPP_NET:
                return

            ids = [attrs.get("id", "")]
            if isinstance(node.content, list) and node.content:
                list_node = node.content[0] if isinstance(node.content[0], BinaryNode) else None
                for item in get_binary_node_children(list_node, "item"):
                    item_id = item.attrs.get("id")
                    if item_id:
                        ids.append(item_id)

            key_base = {
                "remoteJid": remote_jid,
                "id": "",
                "fromMe": (not attrs.get("recipient")) or ((attrs.get("type") in {"retry", "sender"}) and is_node_from_me),
                "participant": attrs.get("participant"),
            }
            status = get_status_from_receipt_type(attrs.get("type"))
            if is_jid_group(remote_jid) or is_jid_status_broadcast(remote_jid):
                participant = attrs.get("participant")
                if participant:
                    update_key = "receiptTimestamp" if status == 1 else "readTimestamp"
                    await self.ev.emit(
                        "message-receipt.update",
                        [
                            {
                                "key": {**key_base, "id": message_id},
                                "receipt": {"userJid": jid_normalized_user(participant), update_key: int(attrs.get("t") or 0)},
                            }
                            for message_id in ids
                        ],
                    )
            else:
                await self.ev.emit(
                    "messages.update",
                    [
                        {
                            "key": {**key_base, "id": message_id},
                            "update": {"status": status, "messageTimestamp": int(attrs.get("t") or 0)},
                        }
                        for message_id in ids
                    ],
                )

            if attrs.get("type") == "retry":
                participant = key_base.get("participant") or attrs.get("from")
                retry_node = get_binary_node_child(node, "retry") or BinaryNode(tag="retry", attrs={"count": "1"})
                if participant and key_base.get("fromMe"):
                    await self.send_messages_again({**key_base, "participant": participant}, ids, retry_node)
        finally:
            await self.send_message_ack(node)

    async def _handle_ack_node(self, node: BinaryNode) -> None:
        if node.attrs.get("class") != "message":
            return
        if not node.attrs.get("error"):
            return

        key = {
            "remoteJid": node.attrs.get("from"),
            "fromMe": True,
            "id": node.attrs.get("id"),
        }
        await self.ev.emit(
            "messages.update",
            [
                {
                    "key": key,
                    "update": {
                        "status": 0,
                        "messageStubParameters": [node.attrs.get("error")],
                    },
                }
            ],
        )

    async def _handle_notification_node(self, node: BinaryNode) -> None:
        remote_jid = node.attrs.get("from") or ""
        should_ignore = getattr(self.config, "should_ignore_jid", lambda _jid: False)
        try:
            if should_ignore(remote_jid) and remote_jid != S_WHATSAPP_NET:
                return

            result: dict[str, Any] = {}
            children = get_all_binary_node_children(node)
            child = children[0] if children else None
            notif_type = node.attrs.get("type")
            if notif_type == "encrypt":
                await self._handle_encrypt_notification(node)
                return

            if notif_type == "lid-mapping":
                lid = node.attrs.get("lid")
                pn = node.attrs.get("pn")
                if lid and pn:
                    self.ids.link_pn_lid(pn, lid)
                    await self.ev.emit("lid-mapping.update", {"lid": lid, "pn": pn})
                return

            if notif_type == "newsletter":
                await self._handle_newsletter_notification(node)
                return

            if notif_type == "mex":
                await self._handle_mex_newsletter_notification(node)
                return

            if notif_type == "mediaretry":
                event = decode_media_retry_node(node)
                await self.ev.emit("messages.media-update", [event])
                return

            if notif_type == "privacy_token":
                await self._handle_privacy_token_notification(node)
                return

            if notif_type == "w:gp2" and child:
                await self._handle_group_notification(node, child, result)

            if notif_type == "devices" and child:
                me_info = self._me_info()
                if are_jids_same_user(child.attrs.get("jid"), me_info.get("id")) or are_jids_same_user(
                    child.attrs.get("lid"), me_info.get("lid")
                ):
                    devices = get_binary_node_children(child, "device")
                    device_data = [{"id": d.attrs.get("jid"), "lid": d.attrs.get("lid")} for d in devices]
                    self._logger.info("my own devices changed", extra={"deviceData": device_data})

            if notif_type == "server_sync":
                update = get_binary_node_child(node, "collection")
                if update:
                    name = update.attrs.get("name")
                    resync = getattr(self, "resync_app_state", None)
                    if name and callable(resync):
                        await resync([name], False)
                return

            if notif_type == "picture":
                from_jid = jid_normalized_user(node.attrs.get("from")) or ""
                set_picture = get_binary_node_child(node, "set")
                del_picture = get_binary_node_child(node, "delete")
                picture_node = set_picture or del_picture
                await self.ev.emit(
                    "contacts.update",
                    [{"id": from_jid or (picture_node.attrs.get("hash") if picture_node else ""), "imgUrl": "changed" if set_picture else "removed"}],
                )

                if is_jid_group(from_jid):
                    result["messageStubType"] = int(WAMessageStubType.GROUP_CHANGE_ICON)
                    if set_picture:
                        result["messageStubParameters"] = [set_picture.attrs.get("id", "")]
                    result["participant"] = picture_node.attrs.get("author") if picture_node else None
                    result["key"] = {"participant": picture_node.attrs.get("author")} if picture_node else {}

            if notif_type == "account_sync":
                if child and child.tag == "disappearing_mode":
                    auth = getattr(self.config, "auth", None)
                    if auth:
                        account_settings = getattr(auth.creds, "account_settings", None) or {}
                        if hasattr(account_settings, "model_dump"):
                            account_settings = account_settings.model_dump(by_alias=True, exclude_none=True)
                        if not isinstance(account_settings, dict):
                            account_settings = {}
                        await self.ev.emit(
                            "creds.update",
                            {
                                "accountSettings": {
                                    **account_settings,
                                    "defaultDisappearingMode": {
                                        "ephemeralExpiration": int(child.attrs.get("duration") or 0),
                                        "ephemeralSettingTimestamp": int(child.attrs.get("t") or 0),
                                    },
                                }
                            },
                        )
                    return

                if child and child.tag == "blocklist":
                    for item in get_binary_node_children(child, "item"):
                        jid = item.attrs.get("jid")
                        if not jid:
                            continue
                        action = item.attrs.get("action")
                        update_type = "add" if action == "block" else "remove"
                        await self.ev.emit("blocklist.update", {"blocklist": [jid], "type": update_type})
                    return

            if notif_type == "link_code_companion_reg":
                await self._handle_link_code_companion_registration(node)
                return

            if result:
                await self._upsert_notification_message(node, result)
        finally:
            await self.send_message_ack(node)

    async def send_message_ack(self, node: BinaryNode, error_code: int | None = None) -> BinaryNode:
        unavailable = get_binary_node_child(node, "unavailable")
        attrs = {
            "to": node.attrs.get("from", ""),
            "id": node.attrs.get("id", ""),
            "class": node.tag,
        }
        if error_code is not None:
            attrs["error"] = str(error_code)
        if node.attrs.get("participant"):
            attrs["participant"] = node.attrs["participant"]
        if node.attrs.get("recipient"):
            attrs["recipient"] = node.attrs["recipient"]
        node_type = node.attrs.get("type")
        if node_type and (node.tag != "message" or unavailable is not None or error_code not in (None, 0)):
            attrs["type"] = node_type
        if node.tag == "message" and unavailable is not None:
            me_id = self._me_id()
            if me_id:
                attrs["from"] = me_id

        ack = BinaryNode(tag="ack", attrs=attrs)
        await self.send_node(ack)
        return ack

    # camelCase aliases for parity
    sendPeerDataOperationMessage = send_peer_data_operation_message
    fetchMessageHistory = fetch_message_history
    requestPlaceholderResend = request_placeholder_resend
    sendMessageAck = send_message_ack
    sendRetryRequest = send_retry_request
    handleBadAck = _handle_ack_node
    willSendMessageAgain = will_send_message_again
    updateSendMessageAgainCount = update_send_message_again_count
    sendMessagesAgain = send_messages_again
