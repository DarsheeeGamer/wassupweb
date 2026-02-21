from __future__ import annotations

import asyncio
import base64
import time
from typing import Any

from pydantic import BaseModel

from ..defaults import DEFAULT_CACHE_TTLS, S_WHATSAPP_NET, WA_DEFAULT_EPHEMERAL
from ..types.identity import JidKind, SendMessageInput, SendTextInput
from ..utils.generics import (
    encode_newsletter_message,
    encode_wa_message,
    generate_message_id_v2,
    generate_participant_hash_v2,
    unix_timestamp_seconds,
)
from ..utils.make_mutex import make_keyed_mutex
from ..utils.link_preview import get_url_info
from ..utils.messages import (
    aggregate_message_keys_not_from_me,
    assert_media_content,
    generate_wa_message,
    normalize_message_content,
)
from ..utils.messages_media import (
    decrypt_media_retry_data,
    encrypt_media_retry_request,
    get_status_code_for_media_retry,
    get_url_from_direct_path,
    get_wa_upload_to_server,
)
from ..utils.reporting_utils import get_message_reporting_token, should_include_reporting_token
from ..utils.signal import extract_device_jids, parse_and_inject_e2e_sessions
from ..utils.validate_connection import encode_signed_device_identity
from ..wausync import USyncQuery, USyncUser
from ..wabinary import (
    BinaryNode,
    are_jids_same_user,
    is_hosted_lid_user,
    is_hosted_pn_user,
    is_jid_group,
    is_jid_newsletter,
    is_jid_status_broadcast,
    is_lid_user,
    is_pn_user,
    jid_decode,
    jid_encode,
    jid_normalized_user,
    get_binary_node_child,
    get_binary_node_children,
)
from .socket import CoreSocket


class _ExpiringMap:
    def __init__(self, ttl_seconds: int) -> None:
        self._ttl_ms = max(int(ttl_seconds * 1000), 1)
        self._store: dict[str, tuple[Any, int]] = {}

    def _now_ms(self) -> int:
        return int(time.time() * 1000)

    def get(self, key: str) -> Any:
        item = self._store.get(key)
        if item is None:
            return None
        value, expires_at = item
        if expires_at <= self._now_ms():
            self._store.pop(key, None)
            return None
        return value

    def set(self, key: str, value: Any) -> None:
        self._store[key] = (value, self._now_ms() + self._ttl_ms)


class MessagesSendSocket(CoreSocket):
    _signal_repository: Any = None

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        cache_ttl = int(DEFAULT_CACHE_TTLS.get("user_devices", 300))
        self._user_devices_cache = _ExpiringMap(cache_ttl)
        self._peer_sessions_cache = _ExpiringMap(cache_ttl)
        self._encryption_mutex = make_keyed_mutex()
        self._media_conn: dict[str, Any] | None = None
        self.wa_upload_to_server = get_wa_upload_to_server(self.config, self.refresh_media_conn)

    @staticmethod
    def _as_dict(value: Any) -> dict[str, Any]:
        if isinstance(value, BaseModel):
            return value.model_dump(by_alias=True, exclude_none=True)
        if isinstance(value, dict):
            return dict(value)
        return {}

    @staticmethod
    def _as_list(value: Any) -> list[Any]:
        if isinstance(value, list):
            return value
        if isinstance(value, tuple):
            return list(value)
        return []

    @staticmethod
    def _wire_normalize(value: Any) -> Any:
        if isinstance(value, (bytes, bytearray)):
            return base64.b64encode(bytes(value)).decode("ascii")
        if isinstance(value, BaseModel):
            return MessagesSendSocket._wire_normalize(value.model_dump(by_alias=True, exclude_none=True))
        if isinstance(value, dict):
            return {k: MessagesSendSocket._wire_normalize(v) for k, v in value.items()}
        if isinstance(value, list):
            return [MessagesSendSocket._wire_normalize(v) for v in value]
        return value

    def _encode_message(self, message: dict[str, Any]) -> bytes:
        return encode_wa_message(self._wire_normalize(message))

    async def _run_keys_transaction(self, work: Any, key: str) -> Any:
        auth = self._require_auth()
        tx = getattr(auth.keys, "transaction", None)
        if callable(tx):
            try:
                return await tx(work, key)
            except TypeError:
                return await tx(work)
        return await work()

    async def _patch_message_before_sending(
        self,
        message: dict[str, Any],
        recipient_jids: list[str] | None = None,
    ) -> Any:
        patcher = getattr(self.config, "patch_message_before_sending", None)
        if not callable(patcher):
            return message
        patched = patcher(message, recipient_jids)
        if asyncio.iscoroutine(patched):
            patched = await patched
        return patched

    def _ensure_signal_repository(self) -> Any:
        if self._signal_repository is not None:
            return self._signal_repository
        auth = getattr(self.config, "auth", None)
        maker = getattr(self.config, "make_signal_repository", None)
        if not auth or not callable(maker):
            return None
        try:
            self._signal_repository = maker(auth, self._logger, None)
        except Exception as error:
            self._logger.warning("failed to initialize signal repository", extra={"error": str(error)})
            self._signal_repository = None
        return self._signal_repository

    def _me_jids(self) -> tuple[str, str | None]:
        auth = self._require_auth()
        creds = auth.creds
        me_raw = getattr(creds, "me", None)
        me = self._as_dict(me_raw)
        me_id = me.get("id")
        if not me_id:
            raise RuntimeError("not authenticated")
        return str(me_id), me.get("lid")

    def _external_user_devices_cache(self) -> Any:
        return getattr(self.config, "user_devices_cache", None)

    async def _load_user_devices_cache(self, users: list[str]) -> dict[str, Any]:
        cache = self._external_user_devices_cache()
        if cache is None or not users:
            return {}

        unique_users = list(dict.fromkeys(users))
        mget = getattr(cache, "mget", None)
        if callable(mget):
            result = mget(unique_users)
            if asyncio.iscoroutine(result):
                result = await result
            if isinstance(result, dict):
                return result

        get_one = getattr(cache, "get", None)
        output: dict[str, Any] = {}
        if callable(get_one):
            for user in unique_users:
                value = get_one(user)
                if asyncio.iscoroutine(value):
                    value = await value
                if value is not None:
                    output[user] = value
        return output

    async def _store_user_devices_cache(self, device_map: dict[str, list[dict[str, Any]]]) -> None:
        cache = self._external_user_devices_cache()
        if cache is None or not device_map:
            return

        mset = getattr(cache, "mset", None)
        if callable(mset):
            entries = [{"key": user, "value": devices} for user, devices in device_map.items()]
            result = mset(entries)
            if asyncio.iscoroutine(result):
                await result
            return

        set_one = getattr(cache, "set", None)
        if callable(set_one):
            for user, devices in device_map.items():
                result = set_one(user, devices)
                if asyncio.iscoroutine(result):
                    await result

    async def refresh_media_conn(self, force_get: bool = False) -> dict[str, Any]:
        now = int(time.time())
        media = self._media_conn
        if media and not force_get:
            fetch_ts = int(media.get("fetchTimestamp") or now)
            ttl = int(media.get("ttl") or 0)
            if now - fetch_ts < ttl:
                return media

        result = await self.query_node(
            BinaryNode(
                tag="iq",
                attrs={"type": "set", "xmlns": "w:m", "to": S_WHATSAPP_NET},
                content=[BinaryNode(tag="media_conn", attrs={})],
            )
        )
        media_conn_node = get_binary_node_child(result, "media_conn")
        if media_conn_node is None:
            raise RuntimeError("missing media_conn node")
        hosts: list[dict[str, Any]] = []
        for host_node in get_binary_node_children(media_conn_node, "host"):
            hosts.append(
                {
                    "hostname": host_node.attrs.get("hostname", ""),
                    "maxContentLengthBytes": int(host_node.attrs.get("maxContentLengthBytes") or 0),
                }
            )

        media = {
            "hosts": hosts,
            "auth": media_conn_node.attrs.get("auth", ""),
            "ttl": int(media_conn_node.attrs.get("ttl") or 0),
            "fetchTimestamp": now,
        }
        self._media_conn = media
        return media

    async def send_receipt(
        self,
        jid: str,
        participant: str | None,
        message_ids: list[str],
        receipt_type: str = "read",
    ) -> BinaryNode:
        if not message_ids:
            raise ValueError("missing ids in receipt")

        resolved_jid = self.resolve_chat_jid(jid)
        node = BinaryNode(tag="receipt", attrs={"id": message_ids[0]})

        is_read_receipt = receipt_type in {"read", "read-self"}
        if is_read_receipt:
            node.attrs["t"] = str(int(time.time()))

        if receipt_type == "sender" and (is_pn_user(resolved_jid) or is_lid_user(resolved_jid)):
            node.attrs["recipient"] = resolved_jid
            node.attrs["to"] = self.resolve_chat_jid(participant) if participant else ""
        else:
            node.attrs["to"] = resolved_jid
            if participant:
                node.attrs["participant"] = self.resolve_chat_jid(participant)

        if receipt_type:
            node.attrs["type"] = receipt_type

        remaining = message_ids[1:]
        if remaining:
            node.content = [
                BinaryNode(
                    tag="list",
                    attrs={},
                    content=[BinaryNode(tag="item", attrs={"id": msg_id}) for msg_id in remaining],
                )
            ]

        await self.send_node(node)
        return node

    async def send_receipts(self, keys: list[dict[str, Any]], receipt_type: str) -> None:
        recps = aggregate_message_keys_not_from_me(keys)
        for item in recps:
            await self.send_receipt(
                jid=item["jid"],
                participant=item.get("participant"),
                message_ids=item.get("messageIds") or [],
                receipt_type=receipt_type,
            )

    async def read_messages(self, keys: list[dict[str, Any]]) -> None:
        privacy_settings: dict[str, Any] = {}
        fetch_privacy_settings = getattr(self, "fetch_privacy_settings", None)
        if callable(fetch_privacy_settings):
            privacy_settings = await fetch_privacy_settings()

        read_type = "read" if privacy_settings.get("readreceipts") == "all" else "read-self"
        await self.send_receipts(keys, read_type)

    async def get_usync_devices(
        self,
        jids: list[str],
        use_cache: bool = True,
        ignore_zero_devices: bool = False,
    ) -> list[dict[str, Any]]:
        device_results: list[dict[str, Any]] = []
        to_fetch: list[str] = []
        jids_with_user: list[dict[str, str]] = []

        for raw_jid in jids:
            decoded = jid_decode(raw_jid)
            user = decoded.get("user") if decoded else None
            device = decoded.get("device") if decoded else None
            is_explicit_device = isinstance(device, int) and device >= 0
            if is_explicit_device and user:
                device_results.append({"user": user, "device": int(device), "jid": raw_jid})
                continue
            normalized = jid_normalized_user(raw_jid)
            if normalized and user:
                jids_with_user.append({"jid": normalized, "user": user})

        external_cached_devices = await self._load_user_devices_cache([item["user"] for item in jids_with_user]) if use_cache else {}

        for item in jids_with_user:
            user = item["user"]
            cached = None
            if use_cache:
                cached = external_cached_devices.get(user)
                if cached is None:
                    cached = self._user_devices_cache.get(user)
            if cached:
                for entry in cached:
                    entry_dict = self._as_dict(entry)
                    entry_user = entry_dict.get("user") or user
                    entry_server = entry_dict.get("server") or "s.whatsapp.net"
                    entry_device = int(entry_dict.get("device") or 0)
                    device_results.append(
                        {
                            **entry_dict,
                            "user": entry_user,
                            "device": entry_device,
                            "jid": jid_encode(entry_user, entry_server, entry_device),
                        }
                    )
            else:
                to_fetch.append(item["jid"])

        if not to_fetch:
            return device_results

        requested_lid_users: set[str] = set()
        for jid in to_fetch:
            if is_lid_user(jid) or is_hosted_lid_user(jid):
                user = (jid_decode(jid) or {}).get("user")
                if user:
                    requested_lid_users.add(user)

        execute_usync_query = getattr(self, "execute_usync_query", None)
        if not callable(execute_usync_query):
            return device_results

        query = USyncQuery().with_context("message").with_device_protocol().with_lid_protocol()
        for jid in to_fetch:
            query.with_user(USyncUser().with_id(jid))

        result = await execute_usync_query(query)
        if not result:
            return device_results

        result_list = self._as_list(getattr(result, "list", None) or self._as_dict(result).get("list"))
        extracted_input: list[dict[str, Any]] = []
        lid_pairs: list[dict[str, str]] = []
        for item in result_list:
            item_dict = self._as_dict(item)
            item_id = item_dict.get("id")
            data = self._as_dict(item_dict.get("data"))
            if not item_id:
                continue
            lid = data.get("lid")
            if isinstance(lid, str) and lid:
                lid_pairs.append({"pn": str(item_id), "lid": lid})
            extracted_input.append({"id": str(item_id), "lid": lid, "devices": data.get("devices")})

        repository = self._ensure_signal_repository()
        mapping_store = getattr(repository, "lid_mapping", None) if repository else None
        if lid_pairs and mapping_store is not None:
            store_lids = getattr(mapping_store, "store_lid_pn_mappings", None) or getattr(
                mapping_store, "storeLIDPNMappings", None
            )
            if callable(store_lids):
                await store_lids(lid_pairs)
            lids = [item["lid"] for item in lid_pairs if item.get("lid")]
            if lids:
                try:
                    await self.assert_sessions(lids, True)
                except Exception as error:
                    self._logger.warning(
                        "failed to assert sessions for mapped lids",
                        extra={"error": str(error), "count": len(lids)},
                    )

        me_id, me_lid = self._me_jids()
        extracted = extract_device_jids(extracted_input, me_id, me_lid or "", ignore_zero_devices)

        device_map: dict[str, list[dict[str, Any]]] = {}
        for item in extracted:
            item_dict = self._as_dict(item)
            user = item_dict.get("user")
            if not user:
                continue
            server = str(item_dict.get("server") or "s.whatsapp.net")
            if user in requested_lid_users:
                if server == "s.whatsapp.net":
                    server = "lid"
                elif server == "hosted":
                    server = "hosted.lid"
            device = int(item_dict.get("device") or 0)
            final = {**item_dict, "server": server, "user": user, "device": device}
            final["jid"] = jid_encode(user, server, device)
            device_results.append(final)
            device_map.setdefault(user, []).append({k: v for k, v in final.items() if k != "jid"})

        for user, devices in device_map.items():
            self._user_devices_cache.set(user, devices)
        await self._store_user_devices_cache(device_map)

        auth = self._require_auth()
        updates: dict[str, list[str]] = {}
        for user, devices in device_map.items():
            if devices:
                updates[user] = [str(int(d.get("device") or 0)) for d in devices]
        if updates:
            try:
                await auth.keys.set({"device-list": updates})
            except Exception as error:
                self._logger.warning("failed to persist user device list cache", extra={"error": str(error)})

        return device_results

    async def assert_sessions(self, jids: list[str], force: bool = False) -> bool:
        repository = self._ensure_signal_repository()
        if repository is None:
            return False

        unique_jids = list(dict.fromkeys(jids))
        jids_requiring_fetch: list[str] = []
        jid_to_addr = getattr(repository, "jid_to_signal_protocol_address", None) or getattr(
            repository, "jidToSignalProtocolAddress", None
        )
        validate_session = getattr(repository, "validate_session", None) or getattr(repository, "validateSession", None)
        if not callable(jid_to_addr) or not callable(validate_session):
            return False

        for jid in unique_jids:
            signal_id = str(jid_to_addr(jid))
            cached = self._peer_sessions_cache.get(signal_id)
            if cached is True and not force:
                continue
            if cached is None:
                validation = await validate_session(jid)
                has_session = bool(self._as_dict(validation).get("exists"))
                self._peer_sessions_cache.set(signal_id, has_session)
                if has_session and not force:
                    continue
            jids_requiring_fetch.append(jid)

        if not jids_requiring_fetch:
            return False

        wire_jids: list[str] = []
        pn_jids: list[str] = []
        for jid in jids_requiring_fetch:
            if is_lid_user(jid) or is_hosted_lid_user(jid):
                wire_jids.append(jid)
            elif is_pn_user(jid) or is_hosted_pn_user(jid):
                pn_jids.append(jid)
            else:
                wire_jids.append(jid)

        mapping_store = getattr(repository, "lid_mapping", None)
        if pn_jids and mapping_store is not None:
            get_lids = getattr(mapping_store, "get_lids_for_pns", None) or getattr(mapping_store, "getLIDsForPNs", None)
            if callable(get_lids):
                pairs = await get_lids(pn_jids)
                mapped: dict[str, str] = {}
                for item in self._as_list(pairs):
                    item_dict = self._as_dict(item)
                    pn = item_dict.get("pn")
                    lid = item_dict.get("lid")
                    if pn and lid:
                        mapped[str(pn)] = str(lid)
                for pn in pn_jids:
                    wire_jids.append(mapped.get(pn, pn))
            else:
                wire_jids.extend(pn_jids)
        else:
            wire_jids.extend(pn_jids)

        wire_jids = list(dict.fromkeys(wire_jids))
        if not wire_jids:
            return False

        result = await self.query_node(
            BinaryNode(
                tag="iq",
                attrs={"xmlns": "encrypt", "type": "get", "to": S_WHATSAPP_NET},
                content=[
                    BinaryNode(
                        tag="key",
                        attrs={},
                        content=[
                            BinaryNode(
                                tag="user",
                                attrs={"jid": jid, **({"reason": "identity"} if force else {})},
                            )
                            for jid in wire_jids
                        ],
                    )
                ],
            )
        )
        await parse_and_inject_e2e_sessions(result, repository)
        for wire_jid in wire_jids:
            signal_id = str(jid_to_addr(wire_jid))
            self._peer_sessions_cache.set(signal_id, True)
        return True

    async def create_participant_nodes(
        self,
        recipient_jids: list[str],
        message: dict[str, Any],
        extra_attrs: dict[str, str] | None = None,
        dsm_message: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not recipient_jids:
            return {"nodes": [], "shouldIncludeDeviceIdentity": False}

        repository = self._ensure_signal_repository()
        if repository is None:
            raise RuntimeError("signal repository unavailable")

        patched = await self._patch_message_before_sending(message, recipient_jids)
        patched_messages: list[tuple[str, dict[str, Any]]] = []
        if isinstance(patched, list):
            for index, item in enumerate(patched):
                item_dict = self._as_dict(item)
                recipient = item_dict.get("recipientJid") or item_dict.get("recipient_jid")
                if not recipient and index < len(recipient_jids):
                    recipient = recipient_jids[index]
                if not recipient:
                    continue
                payload = item_dict.get("message")
                if isinstance(payload, dict):
                    msg = payload
                else:
                    msg = {k: v for k, v in item_dict.items() if k not in {"recipientJid", "recipient_jid"}}
                patched_messages.append((str(recipient), msg))
        else:
            for recipient in recipient_jids:
                patched_messages.append((recipient, self._as_dict(patched)))

        me_id, me_lid = self._me_jids()
        me_id_user = (jid_decode(me_id) or {}).get("user")
        me_lid_user = (jid_decode(me_lid) or {}).get("user") if me_lid else None

        should_include_device_identity = False
        nodes: list[BinaryNode] = []
        for jid, patched_message in patched_messages:
            if not jid:
                continue
            try:
                message_to_encrypt = patched_message
                if dsm_message:
                    target_user = (jid_decode(jid) or {}).get("user")
                    is_own_user = target_user == me_id_user or (me_lid_user and target_user == me_lid_user)
                    is_exact_sender = jid == me_id or (me_lid and jid == me_lid)
                    if is_own_user and not is_exact_sender:
                        message_to_encrypt = dsm_message

                payload = self._encode_message(message_to_encrypt)

                async def _encrypt() -> dict[str, Any]:
                    return await repository.encrypt_message({"jid": jid, "data": payload})

                encrypted = await self._encryption_mutex.mutex(jid, _encrypt)
                enc_type = str(self._as_dict(encrypted).get("type") or "msg")
                ciphertext = self._as_dict(encrypted).get("ciphertext")
                if isinstance(ciphertext, bytearray):
                    ciphertext = bytes(ciphertext)
                if not isinstance(ciphertext, bytes):
                    continue
                if enc_type == "pkmsg":
                    should_include_device_identity = True
                nodes.append(
                    BinaryNode(
                        tag="to",
                        attrs={"jid": jid},
                        content=[
                            BinaryNode(
                                tag="enc",
                                attrs={"v": "2", "type": enc_type, **(extra_attrs or {})},
                                content=ciphertext,
                            )
                        ],
                    )
                )
            except Exception:
                self._logger.warning("recipient encryption failed", extra={"jid": jid})
                continue

        if recipient_jids and not nodes:
            raise RuntimeError("All encryptions failed")

        return {"nodes": nodes, "shouldIncludeDeviceIdentity": should_include_device_identity}

    async def relay_message(
        self,
        jid: str,
        message: dict[str, Any],
        message_id: str | None = None,
        participant: dict[str, Any] | None = None,
        additional_attributes: dict[str, str] | None = None,
        additional_nodes: list[BinaryNode] | None = None,
        use_user_devices_cache: bool = True,
        use_cached_group_metadata: bool = True,
        status_jid_list: list[str] | None = None,
    ) -> str:
        auth = self._require_auth()
        repository = self._ensure_signal_repository()
        if repository is None:
            raise RuntimeError("signal repository unavailable")

        me_id, me_lid = self._me_jids()
        resolved_jid = self.resolve_chat_jid(jid)
        decoded = jid_decode(resolved_jid) or {}
        server = decoded.get("server", "")
        user = decoded.get("user", "")
        is_group = server == "g.us"
        is_status = is_jid_status_broadcast(resolved_jid)
        is_lid = server == "lid"
        is_newsletter = is_jid_newsletter(resolved_jid)
        is_group_or_status = is_group or is_status
        is_retry_resend = bool(participant and participant.get("jid"))
        should_include_device_identity = is_retry_resend
        use_cached_group_metadata = bool(use_cached_group_metadata) and not is_status

        destination_jid = resolved_jid if not is_status else "status@broadcast"
        msg_id = message_id or generate_message_id_v2(me_id)

        participants: list[BinaryNode] = []
        binary_node_content: list[BinaryNode] = []
        devices: list[dict[str, Any]] = []
        reporting_message: dict[str, Any] | None = None
        if participant and participant.get("jid"):
            if not is_group and not is_status:
                additional_attributes = {**(additional_attributes or {}), "device_fanout": "false"}
            part_decoded = jid_decode(participant["jid"]) or {}
            devices.append(
                {
                    "user": part_decoded.get("user"),
                    "device": int(part_decoded.get("device") or 0),
                    "jid": participant["jid"],
                }
            )

        me_msg: dict[str, Any] = {
            "deviceSentMessage": {"destinationJid": destination_jid, "message": message},
            "messageContextInfo": message.get("messageContextInfo"),
        }
        extra_attrs: dict[str, str] = {}

        async def _work() -> None:
            nonlocal additional_attributes
            nonlocal reporting_message
            nonlocal should_include_device_identity

            media_type = self.get_media_type(message)
            if media_type:
                extra_attrs["mediatype"] = media_type

            if is_newsletter:
                patched = await self._patch_message_before_sending(message, [])
                binary_node_content.append(
                    BinaryNode(tag="plaintext", attrs={}, content=encode_newsletter_message(self._as_dict(patched)))
                )
                await self.send_node(
                    BinaryNode(
                        tag="message",
                        attrs={
                            "to": resolved_jid,
                            "id": msg_id,
                            "type": self.get_message_type(message),
                            **(additional_attributes or {}),
                        },
                        content=binary_node_content,
                    )
                )
                return

            normalized = normalize_message_content(message) or {}
            if normalized.get("pinInChatMessage") or normalized.get("reactionMessage"):
                extra_attrs["decrypt-fail"] = "hide"

            if is_group_or_status and not is_retry_resend:
                group_data: dict[str, Any] = {}
                if use_cached_group_metadata:
                    cached_group_metadata = getattr(self.config, "cached_group_metadata", None)
                    if callable(cached_group_metadata):
                        group_data = self._as_dict(await cached_group_metadata(resolved_jid))
                if not group_data and is_group:
                    fetch_group_metadata = getattr(self, "group_metadata", None)
                    if callable(fetch_group_metadata):
                        group_data = self._as_dict(await fetch_group_metadata(resolved_jid))

                sender_key_map: dict[str, bool] = {}
                if not participant and not is_status:
                    sender_memory = await auth.keys.get("sender-key-memory", [resolved_jid])
                    sender_key_map = self._as_dict(sender_memory.get(resolved_jid))

                participants_list = []
                for part in self._as_list(group_data.get("participants")):
                    part_dict = self._as_dict(part)
                    pid = part_dict.get("id")
                    if pid:
                        participants_list.append(pid)

                ephemeral_duration = group_data.get("ephemeralDuration") or group_data.get("ephemeral_duration")
                if isinstance(ephemeral_duration, int) and ephemeral_duration > 0:
                    additional_attributes = {
                        **(additional_attributes or {}),
                        "expiration": str(ephemeral_duration),
                    }

                if is_status and status_jid_list:
                    participants_list.extend(status_jid_list)
                devices.extend(await self.get_usync_devices(participants_list, use_user_devices_cache, False))

                if is_group:
                    addressing_mode = (
                        (additional_attributes or {}).get("addressing_mode")
                        or group_data.get("addressingMode")
                        or group_data.get("addressing_mode")
                        or "lid"
                    )
                    additional_attributes = {
                        **(additional_attributes or {}),
                        "addressing_mode": str(addressing_mode),
                    }

                patched = await self._patch_message_before_sending(message)
                if isinstance(patched, list):
                    raise ValueError("Per-jid patching is not supported in groups")
                patched_message = self._as_dict(patched)
                payload = self._encode_message(patched_message)
                reporting_message = patched_message
                group_addressing_mode = (
                    (additional_attributes or {}).get("addressing_mode")
                    or group_data.get("addressingMode")
                    or group_data.get("addressing_mode")
                    or "lid"
                )
                group_sender_identity = me_lid if group_addressing_mode == "lid" and me_lid else me_id
                encrypted_group = await repository.encrypt_group_message(
                    {"group": destination_jid, "data": payload, "meId": group_sender_identity}
                )
                encrypted_group_dict = self._as_dict(encrypted_group)
                ciphertext = encrypted_group_dict.get("ciphertext")
                if isinstance(ciphertext, bytearray):
                    ciphertext = bytes(ciphertext)
                if not isinstance(ciphertext, bytes):
                    raise RuntimeError("missing group ciphertext")
                sender_key_distribution_message = (
                    encrypted_group_dict.get("senderKeyDistributionMessage")
                    or encrypted_group_dict.get("sender_key_distribution_message")
                )

                sender_key_recipients: list[str] = []
                for device in devices:
                    device_jid = str(device.get("jid") or "")
                    has_key = bool(sender_key_map.get(device_jid))
                    if (
                        (not has_key or participant is not None)
                        and not is_hosted_lid_user(device_jid)
                        and not is_hosted_pn_user(device_jid)
                        and int(device.get("device") or 0) != 99
                    ):
                        sender_key_recipients.append(device_jid)
                        sender_key_map[device_jid] = True

                if sender_key_recipients:
                    sender_key_msg = {
                        "senderKeyDistributionMessage": {
                            "axolotlSenderKeyDistributionMessage": sender_key_distribution_message,
                            "groupId": destination_jid,
                        }
                    }
                    await self.assert_sessions(sender_key_recipients)
                    created = await self.create_participant_nodes(sender_key_recipients, sender_key_msg, extra_attrs)
                    should_include_device_identity = should_include_device_identity or bool(
                        created.get("shouldIncludeDeviceIdentity")
                    )
                    participants.extend(self._as_list(created.get("nodes")))

                binary_node_content.append(
                    BinaryNode(tag="enc", attrs={"v": "2", "type": "skmsg", **extra_attrs}, content=ciphertext)
                )
                await auth.keys.set({"sender-key-memory": {resolved_jid: sender_key_map}})
            else:
                own_id = me_lid if is_lid and me_lid else me_id
                own_user = (jid_decode(own_id) or {}).get("user")
                if not participant:
                    patched_for_reporting = await self._patch_message_before_sending(message, [resolved_jid])
                    if isinstance(patched_for_reporting, list):
                        selected = None
                        for item in patched_for_reporting:
                            item_dict = self._as_dict(item)
                            if item_dict.get("recipientJid") == resolved_jid or item_dict.get("recipient_jid") == resolved_jid:
                                selected = item_dict.get("message") or item_dict
                                break
                        reporting_message = self._as_dict(selected or patched_for_reporting[0])
                    else:
                        reporting_message = self._as_dict(patched_for_reporting)

                if not is_retry_resend:
                    target_server = "lid" if is_lid else "s.whatsapp.net"
                    devices.append({"user": user, "device": 0, "jid": jid_encode(user, target_server, 0)})
                    if user != own_user and own_user:
                        devices.append({"user": own_user, "device": 0, "jid": jid_encode(own_user, target_server, 0)})

                    if (additional_attributes or {}).get("category") != "peer":
                        devices.clear()
                        sender_identity = (
                            jid_encode((jid_decode(me_lid) or {}).get("user"), "lid")
                            if is_lid and me_lid
                            else jid_encode((jid_decode(me_id) or {}).get("user"), "s.whatsapp.net")
                        )
                        devices.extend(await self.get_usync_devices([sender_identity, resolved_jid], use_user_devices_cache, False))

                all_recipients: list[str] = []
                me_recipients: list[str] = []
                other_recipients: list[str] = []
                me_pn_user = (jid_decode(me_id) or {}).get("user")
                me_lid_user = (jid_decode(me_lid) or {}).get("user") if me_lid else None

                for device in devices:
                    device_jid = str(device.get("jid") or "")
                    if device_jid == me_id or (me_lid and device_jid == me_lid):
                        continue
                    device_user = device.get("user")
                    is_me = device_user == me_pn_user or (me_lid_user and device_user == me_lid_user)
                    all_recipients.append(device_jid)
                    if is_me:
                        me_recipients.append(device_jid)
                    else:
                        other_recipients.append(device_jid)

                await self.assert_sessions(all_recipients)
                mine = await self.create_participant_nodes(me_recipients, me_msg or message, extra_attrs)
                others = await self.create_participant_nodes(other_recipients, message, extra_attrs, me_msg)
                participants.extend(self._as_list(mine.get("nodes")))
                participants.extend(self._as_list(others.get("nodes")))
                should_include_device_identity = should_include_device_identity or bool(
                    mine.get("shouldIncludeDeviceIdentity")
                )
                should_include_device_identity = should_include_device_identity or bool(
                    others.get("shouldIncludeDeviceIdentity")
                )
                if me_recipients or other_recipients:
                    extra_attrs["phash"] = generate_participant_hash_v2([*me_recipients, *other_recipients])

            if is_retry_resend and participant:
                participant_jid = str(participant["jid"])
                is_participant_lid = is_lid_user(participant_jid)
                is_me = are_jids_same_user(participant_jid, me_lid if is_participant_lid else me_id)
                encoded_to_send = self._encode_message(
                    {"deviceSentMessage": {"destinationJid": destination_jid, "message": message}}
                    if is_me
                    else message
                )
                encrypted = await repository.encrypt_message({"data": encoded_to_send, "jid": participant_jid})
                encrypted_dict = self._as_dict(encrypted)
                enc_type = str(encrypted_dict.get("type") or "msg")
                encrypted_content = encrypted_dict.get("ciphertext")
                if isinstance(encrypted_content, bytearray):
                    encrypted_content = bytes(encrypted_content)
                if isinstance(encrypted_content, bytes):
                    binary_node_content.append(
                        BinaryNode(
                            tag="enc",
                            attrs={"v": "2", "type": enc_type, "count": str(int(participant.get("count") or 0))},
                            content=encrypted_content,
                        )
                    )

            if participants:
                if (additional_attributes or {}).get("category") == "peer":
                    peer = participants[0]
                    if isinstance(peer.content, list) and peer.content and isinstance(peer.content[0], BinaryNode):
                        binary_node_content.append(peer.content[0])
                else:
                    binary_node_content.append(BinaryNode(tag="participants", attrs={}, content=participants))

            stanza = BinaryNode(
                tag="message",
                attrs={
                    "id": msg_id,
                    "to": destination_jid,
                    "type": self.get_message_type(message),
                    **(additional_attributes or {}),
                },
                content=binary_node_content,
            )

            if participant and participant.get("jid"):
                participant_jid = str(participant["jid"])
                if is_jid_group(destination_jid):
                    stanza.attrs["to"] = destination_jid
                    stanza.attrs["participant"] = participant_jid
                elif are_jids_same_user(participant_jid, me_id):
                    stanza.attrs["to"] = participant_jid
                    stanza.attrs["recipient"] = destination_jid
                else:
                    stanza.attrs["to"] = participant_jid
            else:
                stanza.attrs["to"] = destination_jid

            account_payload = self._as_dict(getattr(auth.creds, "account", None))
            if should_include_device_identity and account_payload:
                stanza.content = self._as_list(stanza.content)
                stanza.content.append(
                    BinaryNode(
                        tag="device-identity",
                        attrs={},
                        content=encode_signed_device_identity(account_payload, True),
                    )
                )

            if (
                not is_newsletter
                and not is_retry_resend
                and reporting_message
                and self._as_dict(reporting_message.get("messageContextInfo")).get("messageSecret")
                and should_include_reporting_token(reporting_message)
            ):
                try:
                    report_key = {
                        "id": msg_id,
                        "fromMe": True,
                        "remoteJid": destination_jid,
                        "participant": participant.get("jid") if participant else None,
                    }
                    report_node = await get_message_reporting_token(
                        self._encode_message(reporting_message), reporting_message, report_key
                    )
                    if report_node:
                        stanza.content = self._as_list(stanza.content)
                        stanza.content.append(report_node)
                except Exception:
                    self._logger.warning("failed to attach reporting token", extra={"jid": destination_jid})

            if not is_group and not is_retry_resend and not is_status:
                tc_data = await auth.keys.get("tctoken", [destination_jid])
                token_entry = self._as_dict(tc_data.get(destination_jid))
                token = token_entry.get("token")
                if isinstance(token, (bytes, bytearray)):
                    stanza.content = self._as_list(stanza.content)
                    stanza.content.append(BinaryNode(tag="tctoken", attrs={}, content=bytes(token)))

            if additional_nodes:
                stanza.content = self._as_list(stanza.content)
                stanza.content.extend(additional_nodes)

            await self.send_node(stanza)
            retry_manager = getattr(self, "message_retry_manager", None)
            if retry_manager is not None and not participant and hasattr(retry_manager, "add_recent_message"):
                retry_manager.add_recent_message(destination_jid, msg_id, message)

        await self._run_keys_transaction(_work, me_id)
        return msg_id

    def get_message_type(self, message: dict[str, Any]) -> str:
        normalized = normalize_message_content(message) or {}
        if normalized.get("reactionMessage") or normalized.get("encReactionMessage"):
            return "reaction"
        if (
            normalized.get("pollCreationMessage")
            or normalized.get("pollCreationMessageV2")
            or normalized.get("pollCreationMessageV3")
            or normalized.get("pollUpdateMessage")
        ):
            return "poll"
        if normalized.get("eventMessage"):
            return "event"
        if self.get_media_type(normalized):
            return "media"
        return "text"

    @staticmethod
    def get_media_type(message: dict[str, Any]) -> str:
        if message.get("imageMessage"):
            return "image"
        if message.get("videoMessage"):
            video = message.get("videoMessage") or {}
            return "gif" if isinstance(video, dict) and video.get("gifPlayback") else "video"
        if message.get("audioMessage"):
            audio = message.get("audioMessage") or {}
            return "ptt" if isinstance(audio, dict) and audio.get("ptt") else "audio"
        if message.get("contactMessage"):
            return "vcard"
        if message.get("documentMessage"):
            return "document"
        if message.get("contactsArrayMessage"):
            return "contact_array"
        if message.get("liveLocationMessage"):
            return "livelocation"
        if message.get("stickerMessage"):
            return "sticker"
        if message.get("listMessage"):
            return "list"
        if message.get("listResponseMessage"):
            return "list_response"
        if message.get("buttonsResponseMessage"):
            return "buttons_response"
        if message.get("orderMessage"):
            return "order"
        if message.get("productMessage"):
            return "product"
        if message.get("interactiveResponseMessage"):
            return "native_flow_response"
        if message.get("groupInviteMessage"):
            return "url"
        return ""

    async def get_privacy_tokens(self, jids: list[str]) -> BinaryNode:
        ts = str(unix_timestamp_seconds())
        return await self.query_node(
            BinaryNode(
                tag="iq",
                attrs={"to": S_WHATSAPP_NET, "type": "set", "xmlns": "privacy"},
                content=[
                    BinaryNode(
                        tag="tokens",
                        attrs={},
                        content=[
                            BinaryNode(
                                tag="token",
                                attrs={
                                    "jid": jid_normalized_user(jid),
                                    "t": ts,
                                    "type": "trusted_contact",
                                },
                            )
                            for jid in jids
                        ],
                    )
                ],
            )
        )

    async def update_member_label(self, jid: str, member_label: str) -> str:
        return await self.relay_message(
            jid,
            {
                "protocolMessage": {
                    "type": "GROUP_MEMBER_LABEL_CHANGE",
                    "memberLabel": {"label": (member_label or "")[:30], "labelTimestamp": unix_timestamp_seconds()},
                }
            },
            additional_nodes=[BinaryNode(tag="meta", attrs={"tag_reason": "user_update", "appdata": "member_tag"})],
        )

    async def send_peer_data_operation_message(self, operation: dict[str, Any]) -> str:
        me_id, _ = self._me_jids()
        protocol_message = {
            "protocolMessage": {
                "peerDataOperationRequestMessage": operation,
                "type": "PEER_DATA_OPERATION_REQUEST_MESSAGE",
            }
        }
        return await self.relay_message(
            me_id,
            protocol_message,
            additional_attributes={"category": "peer", "push_priority": "high_force"},
            additional_nodes=[BinaryNode(tag="meta", attrs={"appdata": "default"})],
        )

    async def update_media_message(self, message: dict[str, Any]) -> dict[str, Any]:
        content = assert_media_content(self._as_dict(message.get("message")))
        media_key = content.get("mediaKey")
        if isinstance(media_key, str):
            media_key_bytes = base64.b64decode(media_key)
        elif isinstance(media_key, bytearray):
            media_key_bytes = bytes(media_key)
        elif isinstance(media_key, bytes):
            media_key_bytes = media_key
        else:
            raise ValueError("missing media key")

        key = self._as_dict(message.get("key"))
        me_id, _ = self._me_jids()
        node = encrypt_media_retry_request(key, media_key_bytes, me_id)
        wait_task = asyncio.create_task(
            self.wait_for(
                "messages.media-update",
                predicate=lambda updates: any(
                    self._as_dict(item).get("key", {}).get("id") == key.get("id") for item in self._as_list(updates)
                ),
                timeout_ms=self.config.default_query_timeout_ms,
            )
        )
        try:
            await self.send_node(node)
            media_updates = await wait_task
        except Exception:
            if not wait_task.done():
                wait_task.cancel()
            raise

        matched_update: dict[str, Any] | None = None
        for update in self._as_list(media_updates):
            update_dict = self._as_dict(update)
            if self._as_dict(update_dict.get("key")).get("id") == key.get("id"):
                matched_update = update_dict
                break

        if not matched_update:
            raise RuntimeError("media update missing")
        if matched_update.get("error"):
            raise RuntimeError(str(matched_update.get("error")))

        media_data = self._as_dict(matched_update.get("media"))
        decrypted = decrypt_media_retry_data(media_data, media_key_bytes, str(key.get("id") or ""))
        result_code = decrypted.get("result")
        success_tokens = {None, 0, "SUCCESS", "success"}
        if result_code not in success_tokens:
            try:
                status_code = get_status_code_for_media_retry(int(result_code))
            except Exception:
                status_code = 500
            raise RuntimeError(f"Media re-upload failed ({status_code})")

        direct_path = decrypted.get("directPath")
        if isinstance(direct_path, str) and direct_path:
            content["directPath"] = direct_path
            content["url"] = get_url_from_direct_path(direct_path)

        await self.ev.emit("messages.update", [{"key": key, "update": {"message": message.get("message")}}])
        return message

    async def send_message(
        self,
        jid: str,
        content: dict[str, Any],
        options: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        resolved_jid = self.resolve_chat_jid(jid)
        options = options or {}
        content_dict = self._as_dict(content)

        if (
            "disappearingMessagesInChat" in content_dict
            and content_dict.get("disappearingMessagesInChat") is not None
            and is_jid_group(resolved_jid)
        ):
            toggle = getattr(self, "group_toggle_ephemeral", None)
            if callable(toggle):
                value = content_dict["disappearingMessagesInChat"]
                if isinstance(value, bool):
                    value = WA_DEFAULT_EPHEMERAL if value else 0
                await toggle(resolved_jid, int(value))
            return None

        me_id, _ = self._me_jids()
        request_options = getattr(self.config, "options", {}) or {}
        profile_picture_url = getattr(self, "profile_picture_url", None)
        wa_upload = getattr(self, "wa_upload_to_server", None)
        get_call_link = getattr(self, "create_call_link", None)

        async def _url_info(text: str) -> dict[str, Any] | None:
            return await get_url_info(
                text,
                {
                    "thumbnailWidth": int(getattr(self.config, "link_preview_image_thumbnail_width", 192) or 192),
                    "fetchOpts": {"timeout": 3_000, **(request_options if isinstance(request_options, dict) else {})},
                    "logger": self._logger,
                    "uploadImage": wa_upload if bool(getattr(self.config, "generate_high_quality_link_preview", False)) else None,
                },
            )

        generated = await generate_wa_message(
            resolved_jid,
            content_dict,
            {
                "logger": self._logger,
                "userJid": me_id,
                "getUrlInfo": _url_info,
                "getProfilePicUrl": profile_picture_url,
                "getCallLink": get_call_link,
                "upload": wa_upload,
                "mediaCache": getattr(self.config, "media_cache", None),
                "options": request_options,
                "messageId": options.get("messageId") or generate_message_id_v2(me_id),
                **options,
            },
        )
        full_msg = generated.model_dump(by_alias=True, exclude_none=True)
        message_body = self._as_dict(full_msg.get("message"))
        message_key = self._as_dict(full_msg.get("key"))

        is_delete = bool(content_dict.get("delete"))
        is_edit = bool(content_dict.get("edit"))
        is_pin = bool(content_dict.get("pin"))
        is_poll = bool(content_dict.get("poll"))
        is_event = bool(content_dict.get("event"))

        additional_attributes: dict[str, str] = {}
        additional_nodes: list[BinaryNode] = []
        if is_delete:
            delete_key = self._as_dict(content_dict.get("delete"))
            if is_jid_group(delete_key.get("remoteJid")) and not bool(delete_key.get("fromMe")):
                additional_attributes["edit"] = "8"
            else:
                additional_attributes["edit"] = "7"
        elif is_edit:
            additional_attributes["edit"] = "1"
        elif is_pin:
            additional_attributes["edit"] = "2"
        elif is_poll:
            additional_nodes.append(BinaryNode(tag="meta", attrs={"polltype": "creation"}))
        elif is_event:
            additional_nodes.append(BinaryNode(tag="meta", attrs={"event_type": "creation"}))

        await self.relay_message(
            resolved_jid,
            message_body,
            message_id=message_key.get("id"),
            additional_attributes=additional_attributes,
            additional_nodes=additional_nodes,
            use_cached_group_metadata=options.get("useCachedGroupMetadata", True),
            status_jid_list=options.get("statusJidList"),
        )

        if bool(getattr(self.config, "emit_own_events", True)):
            upsert = getattr(self, "upsert_message", None)
            mutex = getattr(self, "message_mutex", None)
            if callable(upsert):
                if mutex and callable(getattr(mutex, "mutex", None)):
                    await mutex.mutex(lambda: upsert(full_msg, "append"))
                else:
                    await upsert(full_msg, "append")
        return full_msg

    async def send(self, request: SendMessageInput | dict[str, Any]) -> dict[str, Any] | None:
        payload = request if isinstance(request, SendMessageInput) else SendMessageInput.model_validate(request)
        try:
            jid = self.resolve_chat_jid(payload.to, prefer=payload.prefer)
        except TypeError:
            jid = self.resolve_chat_jid(payload.to)
        return await self.send_message(jid, payload.content, payload.options)

    async def send_text(self, request: SendTextInput | dict[str, Any]) -> dict[str, Any] | None:
        payload = request if isinstance(request, SendTextInput) else SendTextInput.model_validate(request)
        return await self.send(
            SendMessageInput(
                to=payload.to,
                content={"text": payload.text},
                options=payload.options,
                prefer=payload.prefer if isinstance(payload.prefer, JidKind) else JidKind.PN,
            )
        )

    # camelCase aliases for parity
    sendReceipt = send_receipt
    sendReceipts = send_receipts
    readMessages = read_messages
    refreshMediaConn = refresh_media_conn
    getUSyncDevices = get_usync_devices
    assertSessions = assert_sessions
    createParticipantNodes = create_participant_nodes
    relayMessage = relay_message
    getMessageType = get_message_type
    getMediaType = get_media_type
    getPrivacyTokens = get_privacy_tokens
    updateMemberLabel = update_member_label
    sendPeerDataOperationMessage = send_peer_data_operation_message
    updateMediaMessage = update_media_message
    sendMessage = send_message
    sendText = send_text
