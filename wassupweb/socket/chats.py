from __future__ import annotations

import asyncio
import base64
import copy
import inspect
import json
import time
from collections.abc import Awaitable, Callable, Sequence
from typing import Any, Literal

from pydantic import BaseModel

from ..defaults import S_WHATSAPP_NET
from ..types.business import QuickReplyAction
from ..types.chat import (
    ALL_WA_PATCH_NAMES,
    ArchiveChatInput,
    BlockStatusInput,
    CallLinkCreateInput,
    ChatLabelInput,
    ChatModifyInput,
    CleanDirtyBitsInput,
    ContactRemoveInput,
    ContactUpsertInput,
    DisableLinkPreviewsPrivacyInput,
    FetchManyJidsInput,
    LabelUpsertInput,
    MarkReadInput,
    MessageLabelInput,
    MuteChatInput,
    OnWhatsAppInput,
    PnFromLidUSyncInput,
    PresenceSubscribeInput,
    PresenceUpdateInput,
    ProfileNameInput,
    ProfilePictureRemoveInput,
    ProfilePictureUpdateInput,
    ProfilePictureUrlInput,
    ProfileStatusInput,
    QuickReplyRemoveInput,
    QuickReplyUpsertInput,
    StarMessagesInput,
)
from ..types.common import WABusinessHoursConfig, WABusinessProfile
from ..types.label import LabelActionBody
from ..types.state import SyncState
from ..utils.chat_utils import (
    chat_modification_to_app_patch,
    decode_patches,
    decode_syncd_snapshot,
    encode_syncd_patch,
    extract_syncd_patches,
    new_lthash_state,
    process_sync_action,
)
from ..utils.history import get_history_msg
from ..utils.make_mutex import make_mutex
from ..utils.messages_media import generate_profile_picture
from ..utils.process_message import process_message
from ..utils.tc_token_utils import build_tc_token_from_jid
from ..wausync import USyncQuery, USyncUser
from ..wabinary import BinaryNode
from ..wabinary import (
    get_binary_node_child,
    get_binary_node_children,
    is_lid_user,
    jid_decode,
    jid_normalized_user,
)
from ..wabinary.generic_utils import reduce_binary_node_to_dictionary
from .messages_recv import MessagesRecvSocket

MAX_SYNC_ATTEMPTS = 2
_PROCESSABLE_HISTORY_TYPES = {
    0,
    1,
    2,
    3,
    4,
    5,
    6,
    "INITIAL_BOOTSTRAP",
    "PUSH_NAME",
    "RECENT",
    "FULL",
    "ON_DEMAND",
    "NON_BLOCKING_DATA",
    "INITIAL_STATUS_V3",
}


def _to_dict(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(by_alias=True, exclude_none=True)
    return value


def _json_default(value: Any) -> Any:
    if isinstance(value, (bytes, bytearray)):
        return {"__type__": "bytes", "base64": base64.b64encode(bytes(value)).decode("ascii")}
    raise TypeError(f"Object of type {type(value)!r} is not JSON serializable")


def _clone_lthash_state(state: dict[str, Any]) -> dict[str, Any]:
    return {
        "version": int(state.get("version") or 0),
        "hash": bytes(state.get("hash") or b"\x00" * 128),
        "indexValueMap": copy.deepcopy(state.get("indexValueMap") or {}),
    }


def _node_text(node: BinaryNode | None) -> str | None:
    if not node:
        return None
    content = node.content
    if isinstance(content, str):
        return content
    if isinstance(content, (bytes, bytearray)):
        return bytes(content).decode("utf-8", errors="ignore")
    return None


class ChatsSocket(MessagesRecvSocket):
    _presence_handlers_attached: bool = False
    _sync_handlers_attached: bool = False
    _privacy_settings: dict[str, str] | None = None
    _awaiting_sync_timeout: asyncio.TimerHandle | None = None
    _sync_state: SyncState = SyncState.Connecting
    _upsert_message_fn: Callable[[dict[str, Any], str], Awaitable[None]]

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.message_mutex = make_mutex()
        self.receipt_mutex = make_mutex()
        self.app_state_patch_mutex = make_mutex()
        self.notification_mutex = make_mutex()
        self._upsert_message_fn = self._make_buffered(self._upsert_message_impl)

    async def connect(self) -> None:
        await super().connect()
        if not self._presence_handlers_attached:
            self.ev.on("node:presence", self._handle_presence_update)
            self.ev.on("node:chatstate", self._handle_presence_update)
            self._presence_handlers_attached = True
        if not self._sync_handlers_attached:
            self.ev.on("connection.update", self._handle_connection_update)
            self.ev.on("node:ib", self._handle_ib_dirty)
            self.ev.on("lid-mapping.update", self._handle_lid_mapping_update)
            self._sync_handlers_attached = True

    def _me_info(self) -> dict[str, Any]:
        auth = getattr(self.config, "auth", None)
        if not auth:
            return {}
        creds = getattr(auth, "creds", None)
        me = getattr(creds, "me", None) if creds else None
        if hasattr(me, "model_dump"):
            return me.model_dump(by_alias=True, exclude_none=True)
        return me if isinstance(me, dict) else {}

    def _account_settings(self) -> dict[str, Any]:
        auth = getattr(self.config, "auth", None)
        creds = getattr(auth, "creds", None) if auth else None
        settings = getattr(creds, "account_settings", None) if creds else None
        if hasattr(settings, "model_dump"):
            return settings.model_dump(by_alias=True, exclude_none=True)
        return settings if isinstance(settings, dict) else {}

    def _make_buffered(
        self,
        fn: Callable[[dict[str, Any], str], Awaitable[None]],
    ) -> Callable[[dict[str, Any], str], Awaitable[None]]:
        maker = getattr(self.ev, "create_buffered_function", None)
        if not callable(maker):
            maker = getattr(self.ev, "createBufferedFunction", None)
        if callable(maker):
            return maker(fn)
        return fn

    def _ev_buffer(self) -> None:
        buffer_fn = getattr(self.ev, "buffer", None)
        if callable(buffer_fn):
            buffer_fn()

    def _ev_flush(self) -> None:
        flush_fn = getattr(self.ev, "flush", None)
        if callable(flush_fn):
            flush_fn()

    async def _run_safe(self, coro: Awaitable[Any], label: str) -> None:
        try:
            await coro
        except Exception as err:  # pragma: no cover - runtime safety path
            self.on_unexpected_error(err, label)

    async def _run_keys_transaction(self, work: Callable[[], Awaitable[Any]], key: str) -> Any:
        auth = self._require_auth()
        tx = getattr(auth.keys, "transaction", None)
        if callable(tx):
            return await tx(work, key)
        return await work()

    async def _get_app_state_sync_key(self, key_id: str) -> dict[str, Any] | None:
        auth = self._require_auth()
        result = await auth.keys.get("app-state-sync-key", [key_id])
        key = result.get(key_id)
        if key is None:
            return None
        if isinstance(key, dict):
            return key
        if isinstance(key, (bytes, bytearray)):
            return {"keyData": bytes(key)}
        return {"keyData": key}

    async def _new_app_state_chunk_handler(self, mutation: dict[str, Any], is_initial_sync: bool) -> None:
        opts = {"accountSettings": self._account_settings()} if is_initial_sync else None
        await process_sync_action(mutation, self.ev, self._me_info(), opts, self._logger)

    async def resync_app_state(self, collections: Sequence[str], is_initial_sync: bool) -> None:
        auth = self._require_auth()
        if not collections:
            return

        app_state_sync_key_cache: dict[str, dict[str, Any] | None] = {}

        async def get_cached_sync_key(key_id: str) -> dict[str, Any] | None:
            if key_id in app_state_sync_key_cache:
                return app_state_sync_key_cache[key_id]
            key = await self._get_app_state_sync_key(key_id)
            app_state_sync_key_cache[key_id] = key
            return key

        initial_version_map: dict[str, int] = {}
        global_mutation_map: dict[str, dict[str, Any]] = {}

        async def _tx() -> None:
            collections_to_handle = set(collections)
            attempts_map: dict[str, int] = {}

            while collections_to_handle:
                states: dict[str, dict[str, Any]] = {}
                nodes: list[BinaryNode] = []

                for name in tuple(collections_to_handle):
                    sync_version = await auth.keys.get("app-state-sync-version", [name])
                    state = sync_version.get(name)
                    if state:
                        if name not in initial_version_map:
                            initial_version_map[name] = int(state.get("version") or 0)
                    else:
                        state = new_lthash_state()

                    states[name] = _clone_lthash_state(state)
                    nodes.append(
                        BinaryNode(
                            tag="collection",
                            attrs={
                                "name": name,
                                "version": str(int(states[name].get("version") or 0)),
                                "return_snapshot": str(not int(states[name].get("version") or 0)).lower(),
                            },
                        )
                    )

                result = await self.query_node(
                    BinaryNode(
                        tag="iq",
                        attrs={"to": S_WHATSAPP_NET, "xmlns": "w:sync:app:state", "type": "set"},
                        content=[
                            BinaryNode(
                                tag="sync",
                                attrs={},
                                content=nodes,
                            )
                        ],
                    )
                )

                decoded = await extract_syncd_patches(result, self.config.options)
                for name, decoded_item in decoded.items():
                    patches = decoded_item.get("patches") or []
                    has_more_patches = bool(decoded_item.get("hasMorePatches"))
                    snapshot = decoded_item.get("snapshot")
                    try:
                        if snapshot:
                            snapshot_result = await decode_syncd_snapshot(
                                name,
                                snapshot,
                                get_cached_sync_key,
                                initial_version_map.get(name),
                                bool((self.config.app_state_mac_verification or {}).get("snapshot")),
                            )
                            state = snapshot_result["state"]
                            states[name] = _clone_lthash_state(state)
                            global_mutation_map.update(snapshot_result.get("mutationMap") or {})
                            await auth.keys.set({"app-state-sync-version": {name: state}})

                        if patches:
                            patch_result = await decode_patches(
                                name,
                                patches,
                                states[name],
                                get_cached_sync_key,
                                self.config.options,
                                initial_version_map.get(name),
                                self._logger,
                                bool((self.config.app_state_mac_verification or {}).get("patch")),
                            )
                            new_state = patch_result["state"]
                            await auth.keys.set({"app-state-sync-version": {name: new_state}})
                            initial_version_map[name] = int(new_state.get("version") or 0)
                            states[name] = _clone_lthash_state(new_state)
                            global_mutation_map.update(patch_result.get("mutationMap") or {})

                        if has_more_patches:
                            self._logger.info("%s has more patches...", name)
                        else:
                            collections_to_handle.discard(name)
                    except Exception as error:
                        attempts_map[name] = attempts_map.get(name, 0) + 1
                        status_code = None
                        output = getattr(error, "output", None)
                        if isinstance(output, dict):
                            status_code = output.get("statusCode")
                        is_irrecoverable = attempts_map[name] >= MAX_SYNC_ATTEMPTS or status_code == 404 or isinstance(error, TypeError)
                        self._logger.info(
                            "failed to sync state from version%s",
                            "" if is_irrecoverable else ", removing and trying from scratch",
                            extra={"name": name, "error": str(error)},
                        )
                        await auth.keys.set({"app-state-sync-version": {name: None}})
                        if is_irrecoverable:
                            collections_to_handle.discard(name)

        await self._run_keys_transaction(_tx, (self._me_info().get("id") or "resync-app-state"))

        for mutation in global_mutation_map.values():
            await self._new_app_state_chunk_handler(mutation, is_initial_sync)

    async def app_patch(self, patch_create: dict[str, Any]) -> dict[str, Any]:
        auth = self._require_auth()
        patch_data = _to_dict(patch_create)
        patch_name = patch_data.get("type")
        if patch_name not in ALL_WA_PATCH_NAMES:
            raise ValueError(f"Invalid patch type: {patch_name!r}")

        my_app_state_key_id = auth.creds.my_app_state_key_id
        if not my_app_state_key_id:
            raise RuntimeError("App state key not present")

        initial_state: dict[str, Any] | None = None
        encode_result: dict[str, Any] | None = None

        async def _apply_patch() -> None:
            nonlocal initial_state
            nonlocal encode_result

            async def _tx() -> None:
                nonlocal initial_state
                nonlocal encode_result

                await self.resync_app_state([patch_name], False)
                current_sync_version = await auth.keys.get("app-state-sync-version", [patch_name])
                current_state = _clone_lthash_state(current_sync_version.get(patch_name) or new_lthash_state())
                initial_state = _clone_lthash_state(current_state)

                encode_result = await encode_syncd_patch(
                    patch_data,
                    my_app_state_key_id,
                    current_state,
                    self._get_app_state_sync_key,
                )
                patch = encode_result["patch"]
                state = encode_result["state"]

                await self.query_node(
                    BinaryNode(
                        tag="iq",
                        attrs={"to": S_WHATSAPP_NET, "type": "set", "xmlns": "w:sync:app:state"},
                        content=[
                            BinaryNode(
                                tag="sync",
                                attrs={},
                                content=[
                                    BinaryNode(
                                        tag="collection",
                                        attrs={
                                            "name": str(patch_name),
                                            "version": str(int(state.get("version") or 0) - 1),
                                            "return_snapshot": "false",
                                        },
                                        content=[
                                            BinaryNode(
                                                tag="patch",
                                                attrs={},
                                                content=json.dumps(patch, default=_json_default, separators=(",", ":")).encode("utf-8"),
                                            )
                                        ],
                                    )
                                ],
                            )
                        ],
                    )
                )
                await auth.keys.set({"app-state-sync-version": {patch_name: state}})

            await self._run_keys_transaction(_tx, (self._me_info().get("id") or "app-patch"))

        await self.app_state_patch_mutex.mutex(_apply_patch)

        if self.config.emit_own_events and encode_result and initial_state:
            patch_with_version = dict(encode_result["patch"])
            patch_with_version["version"] = {"version": int((encode_result["state"] or {}).get("version") or 0)}
            mutation_result = await decode_patches(
                str(patch_name),
                [patch_with_version],
                initial_state,
                self._get_app_state_sync_key,
                self.config.options,
                None,
                self._logger,
                bool((self.config.app_state_mac_verification or {}).get("patch")),
            )
            for mutation in (mutation_result.get("mutationMap") or {}).values():
                await self._new_app_state_chunk_handler(mutation, False)

        return encode_result or {}

    async def fetch_props(self) -> dict[str, str]:
        auth = getattr(self.config, "auth", None)
        result = await self.query_node(
            BinaryNode(
                tag="iq",
                attrs={"to": S_WHATSAPP_NET, "xmlns": "w", "type": "get"},
                content=[
                    BinaryNode(
                        tag="props",
                        attrs={"protocol": "2", "hash": (auth.creds.last_prop_hash or "") if auth else ""},
                    )
                ],
            )
        )

        props_node = get_binary_node_child(result, "props")
        props: dict[str, str] = {}
        if props_node:
            props = reduce_binary_node_to_dictionary(props_node, "prop")
            prop_hash = props_node.attrs.get("hash")
            if prop_hash and auth:
                auth.creds.last_prop_hash = prop_hash
                await self.ev.emit("creds.update", {"lastPropHash": prop_hash})
        return props

    async def execute_init_queries(self) -> tuple[dict[str, str], list[str], dict[str, str]]:
        props, blocklist, privacy = await asyncio.gather(
            self.fetch_props(),
            self.fetch_blocklist(),
            self.fetch_privacy_settings(),
        )
        return props, blocklist, privacy

    async def chat_modify(self, mod: dict[str, Any] | BaseModel, jid: str) -> BinaryNode:
        mod_dict = _to_dict(mod)
        resolved_jid = self.resolve_chat_jid(jid) if jid else ""
        patch = chat_modification_to_app_patch(mod_dict, resolved_jid)
        await self.app_patch(patch)
        return BinaryNode(tag="iq", attrs={"type": "result"})

    async def update_disable_link_previews_privacy(self, is_previews_disabled: bool) -> BinaryNode:
        return await self.chat_modify({"disableLinkPreviews": {"isPreviewsDisabled": bool(is_previews_disabled)}}, "")

    async def star(self, jid: str, messages: list[dict[str, Any]], star: bool) -> BinaryNode:
        return await self.chat_modify({"star": {"messages": messages, "star": bool(star)}}, jid)

    async def add_or_edit_contact(self, jid: str, contact: dict[str, Any] | BaseModel) -> BinaryNode:
        return await self.chat_modify({"contact": _to_dict(contact)}, jid)

    async def remove_contact(self, jid: str) -> BinaryNode:
        return await self.chat_modify({"contact": None}, jid)

    async def add_label(self, jid: str, labels: LabelActionBody | dict[str, Any]) -> BinaryNode:
        payload = _to_dict(labels)
        return await self.chat_modify({"addLabel": payload}, jid)

    async def add_chat_label(self, jid: str, label_id: str) -> BinaryNode:
        return await self.chat_modify({"addChatLabel": {"labelId": label_id}}, jid)

    async def remove_chat_label(self, jid: str, label_id: str) -> BinaryNode:
        return await self.chat_modify({"removeChatLabel": {"labelId": label_id}}, jid)

    async def add_message_label(self, jid: str, message_id: str, label_id: str) -> BinaryNode:
        return await self.chat_modify({"addMessageLabel": {"messageId": message_id, "labelId": label_id}}, jid)

    async def remove_message_label(self, jid: str, message_id: str, label_id: str) -> BinaryNode:
        return await self.chat_modify({"removeMessageLabel": {"messageId": message_id, "labelId": label_id}}, jid)

    async def add_or_edit_quick_reply(self, quick_reply: QuickReplyAction | dict[str, Any]) -> BinaryNode:
        return await self.chat_modify({"quickReply": _to_dict(quick_reply)}, "")

    async def remove_quick_reply(self, timestamp: str) -> BinaryNode:
        return await self.chat_modify({"quickReply": {"timestamp": timestamp, "deleted": True}}, "")

    async def archive_chat(self, jid: str, archive: bool, last_messages: list[dict[str, Any]] | None = None) -> BinaryNode:
        return await self.chat_modify({"archive": archive, "lastMessages": last_messages or []}, jid)

    async def mute_chat(self, jid: str, mute_seconds: int | None) -> BinaryNode:
        return await self.chat_modify({"mute": mute_seconds}, jid)

    async def mark_read(self, jid: str, message_ids: list[str], read: bool = True) -> BinaryNode:
        receipt_type = "read" if read else "played"
        return await self.send_receipt(jid=jid, participant=None, message_ids=message_ids, receipt_type=receipt_type)

    async def fetch_status_iq(self, jid: str) -> BinaryNode:
        resolved_jid = self.resolve_chat_jid(jid)
        return await self.query_node(
            BinaryNode(
                tag="iq",
                attrs={"to": S_WHATSAPP_NET, "type": "get", "xmlns": "status"},
                content=[BinaryNode(tag="status", attrs={"jid": resolved_jid})],
            )
        )

    async def fetch_status(self, *jids: str) -> list[Any] | None:
        return await self.fetch_statuses(*jids)

    async def fetch_profile_picture_url(self, jid: str, picture_type: str = "image") -> BinaryNode:
        resolved_jid = self.resolve_chat_jid(jid)
        return await self.query_node(
            BinaryNode(
                tag="iq",
                attrs={"to": S_WHATSAPP_NET, "type": "get", "xmlns": "w:profile:picture"},
                content=[BinaryNode(tag="picture", attrs={"jid": resolved_jid, "type": picture_type})],
            )
        )

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

        list_node = BinaryNode(tag="list", attrs={}, content=user_nodes)
        query_node = BinaryNode(
            tag="query",
            attrs={},
            content=[protocol.get_query_element() for protocol in usync_query.protocols],
        )
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
                    content=[query_node, list_node],
                )
            ],
        )
        result = await self.query_node(iq)
        return usync_query.parse_usync_query_result(result)

    async def fetch_statuses(self, *jids: str) -> list[Any] | None:
        query = USyncQuery().with_status_protocol()
        for jid in jids:
            query.with_user(USyncUser().with_id(self.resolve_chat_jid(jid)))
        result = await self.execute_usync_query(query)
        return result.list if result else None

    async def fetch_disappearing_duration(self, *jids: str) -> list[Any] | None:
        query = USyncQuery().with_disappearing_mode_protocol()
        for jid in jids:
            query.with_user(USyncUser().with_id(self.resolve_chat_jid(jid)))
        result = await self.execute_usync_query(query)
        return result.list if result else None

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
            usync_query.with_user(USyncUser().with_id(self.resolve_chat_jid(jid)))

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

    async def fetch_privacy_settings(self, force: bool = False) -> dict[str, str]:
        if self._privacy_settings is None or force:
            result = await self.query_node(
                BinaryNode(
                    tag="iq",
                    attrs={"xmlns": "privacy", "to": S_WHATSAPP_NET, "type": "get"},
                    content=[BinaryNode(tag="privacy", attrs={})],
                )
            )
            privacy_node = get_binary_node_child(result, "privacy")
            settings: dict[str, str] = reduce_binary_node_to_dictionary(privacy_node, "category") if privacy_node else {}
            self._privacy_settings = settings
        return dict(self._privacy_settings or {})

    async def privacy_query(self, name: str, value: str) -> None:
        await self.query_node(
            BinaryNode(
                tag="iq",
                attrs={"xmlns": "privacy", "to": S_WHATSAPP_NET, "type": "set"},
                content=[
                    BinaryNode(
                        tag="privacy",
                        attrs={},
                        content=[BinaryNode(tag="category", attrs={"name": name, "value": value})],
                    )
                ],
            )
        )

    async def update_messages_privacy(self, value: str) -> None:
        await self.privacy_query("messages", value)

    async def update_call_privacy(self, value: str) -> None:
        await self.privacy_query("calladd", value)

    async def update_last_seen_privacy(self, value: str) -> None:
        await self.privacy_query("last", value)

    async def update_online_privacy(self, value: str) -> None:
        await self.privacy_query("online", value)

    async def update_profile_picture_privacy(self, value: str) -> None:
        await self.privacy_query("profile", value)

    async def update_status_privacy(self, value: str) -> None:
        await self.privacy_query("status", value)

    async def update_read_receipts_privacy(self, value: str) -> None:
        await self.privacy_query("readreceipts", value)

    async def update_groups_add_privacy(self, value: str) -> None:
        await self.privacy_query("groupadd", value)

    async def update_default_disappearing_mode(self, duration: int) -> None:
        await self.query_node(
            BinaryNode(
                tag="iq",
                attrs={"xmlns": "disappearing_mode", "to": S_WHATSAPP_NET, "type": "set"},
                content=[BinaryNode(tag="disappearing_mode", attrs={"duration": str(duration)})],
            )
        )

    async def get_bot_list_v2(self) -> list[dict[str, str]]:
        response = await self.query_node(
            BinaryNode(
                tag="iq",
                attrs={"xmlns": "bot", "to": S_WHATSAPP_NET, "type": "get"},
                content=[BinaryNode(tag="bot", attrs={"v": "2"})],
            )
        )
        bot_node = get_binary_node_child(response, "bot")
        bot_list: list[dict[str, str]] = []
        for section in get_binary_node_children(bot_node, "section"):
            if section.attrs.get("type") == "all":
                for bot in get_binary_node_children(section, "bot"):
                    bot_list.append(
                        {
                            "jid": bot.attrs.get("jid", ""),
                            "personaId": bot.attrs.get("persona_id", ""),
                        }
                    )
        return bot_list

    async def get_business_profile(self, jid: str) -> WABusinessProfile | None:
        resolved_jid = self.resolve_chat_jid(jid)
        results = await self.query_node(
            BinaryNode(
                tag="iq",
                attrs={"to": S_WHATSAPP_NET, "xmlns": "w:biz", "type": "get"},
                content=[
                    BinaryNode(
                        tag="business_profile",
                        attrs={"v": "244"},
                        content=[BinaryNode(tag="profile", attrs={"jid": resolved_jid})],
                    )
                ],
            )
        )

        profile_node = get_binary_node_child(results, "business_profile")
        profile = get_binary_node_child(profile_node, "profile")
        if not profile:
            return None

        address = _node_text(get_binary_node_child(profile, "address"))
        description = _node_text(get_binary_node_child(profile, "description")) or ""
        website = _node_text(get_binary_node_child(profile, "website"))
        email = _node_text(get_binary_node_child(profile, "email"))
        categories = get_binary_node_child(profile, "categories")
        category = _node_text(get_binary_node_child(categories, "category"))
        business_hours = get_binary_node_child(profile, "business_hours")
        business_hours_config = [
            WABusinessHoursConfig.model_validate(dict(item.attrs)).model_dump(by_alias=True, exclude_none=True)
            for item in get_binary_node_children(business_hours, "business_hours_config")
        ]

        return WABusinessProfile(
            wid=profile.attrs.get("jid"),
            address=address,
            description=description,
            website=[website] if website else [],
            email=email,
            category=category,
            business_hours={
                "timezone": business_hours.attrs.get("timezone") if business_hours else None,
                "business_config": business_hours_config,
            },
        )

    async def update_profile_picture(
        self,
        jid: str,
        content: Any,
        dimensions: dict[str, int] | None = None,
    ) -> BinaryNode:
        if not jid:
            raise ValueError("jid is required for profile picture updates")
        me_id = jid_normalized_user(self._me_info().get("id"))
        normalized = jid_normalized_user(jid)
        target_jid = normalized if me_id and normalized != me_id else None
        picture = await generate_profile_picture(content, dimensions)
        return await self.query_node(
            BinaryNode(
                tag="iq",
                attrs={
                    "to": S_WHATSAPP_NET,
                    "type": "set",
                    "xmlns": "w:profile:picture",
                    **({"target": target_jid} if target_jid else {}),
                },
                content=[BinaryNode(tag="picture", attrs={"type": "image"}, content=picture["img"])],
            )
        )

    async def remove_profile_picture(self, jid: str) -> BinaryNode:
        if not jid:
            raise ValueError("jid is required for profile picture updates")
        me_id = jid_normalized_user(self._me_info().get("id"))
        normalized = jid_normalized_user(jid)
        target_jid = normalized if me_id and normalized != me_id else None
        return await self.query_node(
            BinaryNode(
                tag="iq",
                attrs={
                    "to": S_WHATSAPP_NET,
                    "type": "set",
                    "xmlns": "w:profile:picture",
                    **({"target": target_jid} if target_jid else {}),
                },
            )
        )

    async def update_profile_status(self, status: str) -> None:
        await self.query_node(
            BinaryNode(
                tag="iq",
                attrs={"to": S_WHATSAPP_NET, "type": "set", "xmlns": "status"},
                content=[BinaryNode(tag="status", attrs={}, content=status.encode("utf-8"))],
            )
        )

    async def update_profile_name(self, name: str) -> BinaryNode:
        return await self.chat_modify({"pushNameSetting": name}, "")

    async def fetch_blocklist(self) -> list[str]:
        result = await self.query_node(
            BinaryNode(
                tag="iq",
                attrs={"xmlns": "blocklist", "to": S_WHATSAPP_NET, "type": "get"},
            )
        )
        list_node = get_binary_node_child(result, "list")
        return [node.attrs.get("jid", "") for node in get_binary_node_children(list_node, "item")]

    async def update_block_status(self, jid: str, action: Literal["block", "unblock"]) -> None:
        resolved_jid = self.resolve_chat_jid(jid)
        await self.query_node(
            BinaryNode(
                tag="iq",
                attrs={"xmlns": "blocklist", "to": S_WHATSAPP_NET, "type": "set"},
                content=[BinaryNode(tag="item", attrs={"action": action, "jid": resolved_jid})],
            )
        )

    async def clean_dirty_bits(self, type: Literal["account_sync", "groups"], from_timestamp: int | str | None = None) -> None:
        attrs: dict[str, str] = {"type": type}
        if from_timestamp is not None:
            attrs["timestamp"] = str(from_timestamp)
        await self.send_node(
            BinaryNode(
                tag="iq",
                attrs={
                    "to": S_WHATSAPP_NET,
                    "type": "set",
                    "xmlns": "urn:xmpp:whatsapp:dirty",
                    "id": self.generate_message_tag(),
                },
                content=[BinaryNode(tag="clean", attrs=attrs)],
            )
        )

    async def profile_picture_url(
        self,
        jid: str,
        picture_type: Literal["preview", "image"] = "preview",
        timeout_ms: int | None = None,
    ) -> str | None:
        normalized = jid_normalized_user(jid)
        auth = getattr(self.config, "auth", None)
        base_content = [BinaryNode(tag="picture", attrs={"type": picture_type, "query": "url"})]
        tc_token_content = await build_tc_token_from_jid(
            auth_state={"keys": auth.keys} if auth else {},
            jid=normalized,
            base_content=base_content,
        )
        result = await self.query_node(
            BinaryNode(
                tag="iq",
                attrs={
                    "target": normalized,
                    "to": S_WHATSAPP_NET,
                    "type": "get",
                    "xmlns": "w:profile:picture",
                },
                content=tc_token_content or base_content,
            ),
            timeout_ms=timeout_ms,
        )
        child = get_binary_node_child(result, "picture")
        return child.attrs.get("url") if child else None

    async def create_call_link(
        self,
        media: Literal["audio", "video"],
        event: dict[str, int] | None = None,
        timeout_ms: int | None = None,
    ) -> str | None:
        link_create_content: list[BinaryNode] | None = None
        if event and event.get("startTime") is not None:
            link_create_content = [BinaryNode(tag="event", attrs={"start_time": str(int(event["startTime"]))})]

        result = await self.query_node(
            BinaryNode(
                tag="call",
                attrs={"id": self.generate_message_tag(), "to": "@call"},
                content=[
                    BinaryNode(
                        tag="link_create",
                        attrs={"media": media},
                        content=link_create_content,
                    )
                ],
            ),
            timeout_ms=timeout_ms,
        )
        child = get_binary_node_child(result, "link_create")
        return child.attrs.get("token") if child else None

    async def send_presence_update(self, type: str, to_jid: str | None = None) -> None:
        me = self._me_info()
        is_available_type = type == "available"
        if is_available_type or type == "unavailable":
            name = me.get("name")
            if not name:
                self._logger.warning("no name present, ignoring presence update request")
                return
            await self.ev.emit("connection.update", {"isOnline": is_available_type})
            if is_available_type:
                maybe_send_unified = self.send_unified_session()
                if inspect.isawaitable(maybe_send_unified):
                    asyncio.create_task(self._run_safe(maybe_send_unified, "send unified session"))
            await self.send_node(
                BinaryNode(
                    tag="presence",
                    attrs={"name": str(name).replace("@", ""), "type": type},
                )
            )
            return

        if not to_jid:
            raise ValueError("to_jid is required for chatstate presence updates")
        decoded = jid_decode(to_jid) or {}
        is_lid = decoded.get("server") == "lid"
        from_jid = (me.get("lid") if is_lid else me.get("id")) or me.get("id") or ""
        await self.send_node(
            BinaryNode(
                tag="chatstate",
                attrs={"from": str(from_jid), "to": to_jid},
                content=[
                    BinaryNode(
                        tag="composing" if type == "recording" else type,
                        attrs={"media": "audio"} if type == "recording" else {},
                    )
                ],
            )
        )

    async def presence_subscribe(self, to_jid: str) -> None:
        auth = getattr(self.config, "auth", None)
        tc_token_content = await build_tc_token_from_jid(
            auth_state={"keys": auth.keys} if auth else {},
            jid=to_jid,
        )
        await self.send_node(
            BinaryNode(
                tag="presence",
                attrs={"to": to_jid, "id": self.generate_message_tag(), "type": "subscribe"},
                content=tc_token_content,
            )
        )

    async def _handle_presence_update(self, node: BinaryNode) -> None:
        presence: dict[str, Any] | None = None
        jid = node.attrs.get("from")
        participant = node.attrs.get("participant") or jid
        if not jid or not participant:
            return

        if self.config.should_ignore_jid(jid) and jid != S_WHATSAPP_NET:
            return

        if node.tag == "presence":
            raw_last = node.attrs.get("last")
            presence = {
                "lastKnownPresence": "unavailable" if node.attrs.get("type") == "unavailable" else "available",
                "lastSeen": int(raw_last) if raw_last and raw_last != "deny" else None,
            }
        elif isinstance(node.content, list) and node.content:
            first_child = node.content[0]
            if isinstance(first_child, BinaryNode):
                state = first_child.tag
                if state == "paused":
                    state = "available"
                if first_child.attrs.get("media") == "audio":
                    state = "recording"
                presence = {"lastKnownPresence": state}
        else:
            self._logger.error("recv invalid presence node", extra={"node": node})

        if presence:
            await self.ev.emit("presence.update", {"id": jid, "presences": {participant: presence}})

    async def _handle_ib_dirty(self, node: BinaryNode) -> None:
        dirty = get_binary_node_child(node, "dirty")
        if not dirty:
            return
        dirty_type = dirty.attrs.get("type")
        if dirty_type == "account_sync":
            timestamp = dirty.attrs.get("timestamp")
            auth = getattr(self.config, "auth", None)
            if timestamp and auth:
                if auth.creds.last_account_sync_timestamp:
                    await self.clean_dirty_bits("account_sync", auth.creds.last_account_sync_timestamp)
                auth.creds.last_account_sync_timestamp = int(timestamp)
                await self.ev.emit("creds.update", {"lastAccountSyncTimestamp": auth.creds.last_account_sync_timestamp})
        elif dirty_type == "groups":
            return
        else:
            self._logger.info("received unknown sync", extra={"node": node})

    async def _handle_lid_mapping_update(self, payload: dict[str, Any]) -> None:
        lid = payload.get("lid")
        pn = payload.get("pn")
        if not lid or not pn:
            return
        repo = getattr(self, "_signal_repository", None)
        if not repo:
            return
        mapping_store = getattr(repo, "lid_mapping", None) or getattr(repo, "lidMapping", None)
        if not mapping_store:
            return
        store_fn = getattr(mapping_store, "store_lid_pn_mappings", None) or getattr(mapping_store, "storeLIDPNMappings", None)
        if not callable(store_fn):
            return
        try:
            maybe_awaitable = store_fn([{"lid": lid, "pn": pn}])
            if inspect.isawaitable(maybe_awaitable):
                await maybe_awaitable
        except Exception as error:  # pragma: no cover - runtime store failures
            self._logger.warning("Failed to store LID-PN mapping", extra={"lid": lid, "pn": pn, "error": str(error)})

    def _clear_sync_timeout(self) -> None:
        if self._awaiting_sync_timeout:
            self._awaiting_sync_timeout.cancel()
            self._awaiting_sync_timeout = None

    def _start_sync_timeout(self) -> None:
        self._clear_sync_timeout()

        def _on_timeout() -> None:
            if self._sync_state == SyncState.AwaitingInitialSync:
                self._logger.warning("Timeout in AwaitingInitialSync, forcing state to Online and flushing buffer")
                self._sync_state = SyncState.Online
                self._ev_flush()

        self._awaiting_sync_timeout = asyncio.get_running_loop().call_later(20.0, _on_timeout)

    async def _handle_connection_update(self, update: dict[str, Any]) -> None:
        connection = update.get("connection")
        received_pending_notifications = update.get("receivedPendingNotifications")

        if connection == "open":
            if self.config.fire_init_queries:
                asyncio.create_task(self._run_safe(self.execute_init_queries(), "init queries"))
            presence = "available" if self.config.mark_online_on_connect else "unavailable"
            asyncio.create_task(self._run_safe(self.send_presence_update(presence), "presence update requests"))

        if not received_pending_notifications or self._sync_state != SyncState.Connecting:
            return

        self._sync_state = SyncState.AwaitingInitialSync
        self._ev_buffer()

        will_sync_history = True
        try:
            will_sync_history = bool(self.config.should_sync_history_message({"syncType": "RECENT"}))
        except Exception:
            will_sync_history = True

        if not will_sync_history:
            self._sync_state = SyncState.Online
            asyncio.get_running_loop().call_soon(self._ev_flush)
            return

        self._start_sync_timeout()

    async def _handle_message_node(self, node: BinaryNode) -> None:
        await super()._handle_message_node(node)

    async def upsert_message(self, msg: dict[str, Any], type: str) -> None:
        await self._upsert_message_fn(msg, type)

    async def _upsert_message_impl(self, msg: dict[str, Any], type: str) -> None:
        await self.ev.emit("messages.upsert", {"messages": [msg], "type": type})

        push_name = msg.get("pushName")
        key = msg.get("key") or {}
        me_info = self._me_info()
        if push_name:
            jid = key.get("participant") or key.get("remoteJid")
            if key.get("fromMe"):
                jid = me_info.get("id") or jid
            jid = jid_normalized_user(jid) if jid else jid

            if not key.get("fromMe") and jid:
                await self.ev.emit("contacts.update", [{"id": jid, "notify": push_name, "verifiedName": msg.get("verifiedBizName")}])
            elif (me_info.get("name") != push_name) and push_name:
                await self.ev.emit("creds.update", {"me": {**me_info, "name": push_name}})

        history_msg = get_history_msg(msg.get("message"))
        should_process_history_msg = False
        if history_msg:
            history_type = history_msg.get("syncType")
            should_process_history_msg = bool(self.config.should_sync_history_message(history_msg)) and history_type in _PROCESSABLE_HISTORY_TYPES

        if history_msg and self._sync_state == SyncState.AwaitingInitialSync:
            self._clear_sync_timeout()
            if should_process_history_msg:
                self._sync_state = SyncState.Syncing
            else:
                self._sync_state = SyncState.Online
                self._ev_flush()

        async def do_app_state_sync() -> None:
            if self._sync_state == SyncState.Syncing:
                await self.resync_app_state(ALL_WA_PATCH_NAMES, True)
                self._sync_state = SyncState.Online
                self._ev_flush()

                auth = getattr(self.config, "auth", None)
                if auth:
                    account_sync_counter = int(auth.creds.account_sync_counter or 0) + 1
                    auth.creds.account_sync_counter = account_sync_counter
                    await self.ev.emit("creds.update", {"accountSyncCounter": account_sync_counter})

        auth = getattr(self.config, "auth", None)
        creds: dict[str, Any]
        if auth:
            auth_creds = auth.creds
            if hasattr(auth_creds, "model_dump"):
                creds = auth_creds.model_dump(by_alias=True, exclude_none=True)
            elif isinstance(auth_creds, dict):
                creds = dict(auth_creds)
            else:
                creds = dict(getattr(auth_creds, "__dict__", {}))
        else:
            creds = {"me": {"id": ""}, "accountSettings": {"unarchiveChats": False}}

        if "accountSettings" not in creds and "account_settings" in creds:
            creds["accountSettings"] = creds.get("account_settings")
        if "me" not in creds:
            creds["me"] = me_info

        async def _get_message(key_data: dict[str, Any]) -> dict[str, Any] | None:
            if self.config.get_message:
                maybe_msg = self.config.get_message(key_data)
                if inspect.isawaitable(maybe_msg):
                    return await maybe_msg
                return maybe_msg
            return None

        placeholder_resend_cache = getattr(self.config, "placeholder_resend_cache", None)
        if placeholder_resend_cache is None:
            placeholder_resend_cache = getattr(self.config, "placeholderResendCache", None)

        tasks: list[Awaitable[Any]] = [
            process_message(
                msg,
                {
                    "signalRepository": self._signal_repository,
                    "shouldProcessHistoryMsg": should_process_history_msg,
                    "placeholderResendCache": placeholder_resend_cache,
                    "ev": self.ev,
                    "creds": creds,
                    "keyStore": auth.keys if auth else None,
                    "logger": self._logger,
                    "options": self.config.options,
                    "getMessage": _get_message,
                },
            )
        ]
        if should_process_history_msg:
            tasks.append(do_app_state_sync())

        await asyncio.gather(*tasks)

        protocol_message = ((msg.get("message") or {}).get("protocolMessage") or {}) if isinstance(msg.get("message"), dict) else {}
        if protocol_message.get("appStateSyncKeyShare") and self._sync_state == SyncState.Syncing:
            await do_app_state_sync()

    # typed convenience interfaces
    async def modify_chat(self, request: ChatModifyInput | dict[str, Any]) -> BinaryNode:
        payload = request if isinstance(request, ChatModifyInput) else ChatModifyInput.model_validate(request)
        return await self.chat_modify(payload.mod, payload.jid)

    async def set_link_previews_privacy(
        self, request: DisableLinkPreviewsPrivacyInput | dict[str, Any]
    ) -> BinaryNode:
        payload = (
            request
            if isinstance(request, DisableLinkPreviewsPrivacyInput)
            else DisableLinkPreviewsPrivacyInput.model_validate(request)
        )
        return await self.update_disable_link_previews_privacy(payload.is_previews_disabled)

    async def set_star_messages(self, request: StarMessagesInput | dict[str, Any]) -> BinaryNode:
        payload = request if isinstance(request, StarMessagesInput) else StarMessagesInput.model_validate(request)
        return await self.star(payload.jid, payload.messages, payload.star)

    async def upsert_contact_entry(self, request: ContactUpsertInput | dict[str, Any]) -> BinaryNode:
        payload = request if isinstance(request, ContactUpsertInput) else ContactUpsertInput.model_validate(request)
        return await self.add_or_edit_contact(payload.jid, payload.contact)

    async def delete_contact_entry(self, request: ContactRemoveInput | dict[str, Any]) -> BinaryNode:
        payload = request if isinstance(request, ContactRemoveInput) else ContactRemoveInput.model_validate(request)
        return await self.remove_contact(payload.jid)

    async def upsert_label_entry(self, request: LabelUpsertInput | dict[str, Any]) -> BinaryNode:
        payload = request if isinstance(request, LabelUpsertInput) else LabelUpsertInput.model_validate(request)
        return await self.add_label(payload.jid, payload.labels)

    async def add_chat_label_entry(self, request: ChatLabelInput | dict[str, Any]) -> BinaryNode:
        payload = request if isinstance(request, ChatLabelInput) else ChatLabelInput.model_validate(request)
        return await self.add_chat_label(payload.jid, payload.label_id)

    async def remove_chat_label_entry(self, request: ChatLabelInput | dict[str, Any]) -> BinaryNode:
        payload = request if isinstance(request, ChatLabelInput) else ChatLabelInput.model_validate(request)
        return await self.remove_chat_label(payload.jid, payload.label_id)

    async def add_message_label_entry(self, request: MessageLabelInput | dict[str, Any]) -> BinaryNode:
        payload = request if isinstance(request, MessageLabelInput) else MessageLabelInput.model_validate(request)
        return await self.add_message_label(payload.jid, payload.message_id, payload.label_id)

    async def remove_message_label_entry(self, request: MessageLabelInput | dict[str, Any]) -> BinaryNode:
        payload = request if isinstance(request, MessageLabelInput) else MessageLabelInput.model_validate(request)
        return await self.remove_message_label(payload.jid, payload.message_id, payload.label_id)

    async def upsert_quick_reply_entry(self, request: QuickReplyUpsertInput | dict[str, Any]) -> BinaryNode:
        payload = (
            request if isinstance(request, QuickReplyUpsertInput) else QuickReplyUpsertInput.model_validate(request)
        )
        return await self.add_or_edit_quick_reply(payload.quick_reply)

    async def delete_quick_reply_entry(self, request: QuickReplyRemoveInput | dict[str, Any]) -> BinaryNode:
        payload = (
            request if isinstance(request, QuickReplyRemoveInput) else QuickReplyRemoveInput.model_validate(request)
        )
        return await self.remove_quick_reply(payload.timestamp)

    async def set_archive_chat(self, request: ArchiveChatInput | dict[str, Any]) -> BinaryNode:
        payload = request if isinstance(request, ArchiveChatInput) else ArchiveChatInput.model_validate(request)
        return await self.archive_chat(payload.jid, payload.archive, payload.last_messages)

    async def set_mute_chat(self, request: MuteChatInput | dict[str, Any]) -> BinaryNode:
        payload = request if isinstance(request, MuteChatInput) else MuteChatInput.model_validate(request)
        return await self.mute_chat(payload.jid, payload.mute_seconds)

    async def set_mark_read(self, request: MarkReadInput | dict[str, Any]) -> BinaryNode:
        payload = request if isinstance(request, MarkReadInput) else MarkReadInput.model_validate(request)
        return await self.mark_read(payload.jid, payload.message_ids, payload.read)

    async def fetch_status_for(self, request: FetchManyJidsInput | dict[str, Any]) -> list[Any] | None:
        payload = request if isinstance(request, FetchManyJidsInput) else FetchManyJidsInput.model_validate(request)
        return await self.fetch_status(*payload.jids)

    async def fetch_disappearing_duration_for(self, request: FetchManyJidsInput | dict[str, Any]) -> list[Any] | None:
        payload = request if isinstance(request, FetchManyJidsInput) else FetchManyJidsInput.model_validate(request)
        return await self.fetch_disappearing_duration(*payload.jids)

    async def check_on_whatsapp(self, request: OnWhatsAppInput | dict[str, Any]) -> list[dict[str, Any]]:
        payload = request if isinstance(request, OnWhatsAppInput) else OnWhatsAppInput.model_validate(request)
        return await self.on_whatsapp(*payload.phone_numbers)

    async def resolve_pn_from_lid(self, request: PnFromLidUSyncInput | dict[str, Any]) -> list[dict[str, str]]:
        payload = request if isinstance(request, PnFromLidUSyncInput) else PnFromLidUSyncInput.model_validate(request)
        return await self.pn_from_lid_usync(payload.jids)

    async def fetch_profile_picture(self, request: ProfilePictureUrlInput | dict[str, Any]) -> str | None:
        payload = (
            request if isinstance(request, ProfilePictureUrlInput) else ProfilePictureUrlInput.model_validate(request)
        )
        return await self.profile_picture_url(payload.jid, payload.picture_type, payload.timeout_ms)

    async def set_profile_picture(self, request: ProfilePictureUpdateInput | dict[str, Any]) -> BinaryNode:
        payload = (
            request if isinstance(request, ProfilePictureUpdateInput) else ProfilePictureUpdateInput.model_validate(request)
        )
        return await self.update_profile_picture(payload.jid, payload.content, payload.dimensions)

    async def clear_profile_picture(self, request: ProfilePictureRemoveInput | dict[str, Any]) -> BinaryNode:
        payload = (
            request if isinstance(request, ProfilePictureRemoveInput) else ProfilePictureRemoveInput.model_validate(request)
        )
        return await self.remove_profile_picture(payload.jid)

    async def set_profile_status(self, request: ProfileStatusInput | dict[str, Any]) -> None:
        payload = request if isinstance(request, ProfileStatusInput) else ProfileStatusInput.model_validate(request)
        await self.update_profile_status(payload.status)

    async def set_profile_name(self, request: ProfileNameInput | dict[str, Any]) -> BinaryNode:
        payload = request if isinstance(request, ProfileNameInput) else ProfileNameInput.model_validate(request)
        return await self.update_profile_name(payload.name)

    async def set_block_status_entry(self, request: BlockStatusInput | dict[str, Any]) -> None:
        payload = request if isinstance(request, BlockStatusInput) else BlockStatusInput.model_validate(request)
        await self.update_block_status(payload.jid, payload.action)

    async def clean_dirty_entry(self, request: CleanDirtyBitsInput | dict[str, Any]) -> None:
        payload = request if isinstance(request, CleanDirtyBitsInput) else CleanDirtyBitsInput.model_validate(request)
        await self.clean_dirty_bits(payload.type, payload.from_timestamp)

    async def create_call_link_entry(self, request: CallLinkCreateInput | dict[str, Any]) -> str | None:
        payload = request if isinstance(request, CallLinkCreateInput) else CallLinkCreateInput.model_validate(request)
        return await self.create_call_link(payload.media, payload.event, payload.timeout_ms)

    async def set_presence_state(self, request: PresenceUpdateInput | dict[str, Any]) -> None:
        payload = request if isinstance(request, PresenceUpdateInput) else PresenceUpdateInput.model_validate(request)
        await self.send_presence_update(payload.type, payload.to_jid)

    async def subscribe_presence_updates(self, request: PresenceSubscribeInput | dict[str, Any]) -> None:
        payload = request if isinstance(request, PresenceSubscribeInput) else PresenceSubscribeInput.model_validate(request)
        await self.presence_subscribe(payload.to_jid)

    # camelCase aliases for Baileys parity
    executeUSyncQuery = execute_usync_query
    fetchStatus = fetch_status
    fetchStatuses = fetch_statuses
    fetchDisappearingDuration = fetch_disappearing_duration
    onWhatsApp = on_whatsapp
    pnFromLIDUSync = pn_from_lid_usync
    fetchPrivacySettings = fetch_privacy_settings
    privacyQuery = privacy_query
    updateMessagesPrivacy = update_messages_privacy
    updateCallPrivacy = update_call_privacy
    updateLastSeenPrivacy = update_last_seen_privacy
    updateOnlinePrivacy = update_online_privacy
    updateProfilePicturePrivacy = update_profile_picture_privacy
    updateStatusPrivacy = update_status_privacy
    updateReadReceiptsPrivacy = update_read_receipts_privacy
    updateGroupsAddPrivacy = update_groups_add_privacy
    updateDefaultDisappearingMode = update_default_disappearing_mode
    getBotListV2 = get_bot_list_v2
    updateProfilePicture = update_profile_picture
    removeProfilePicture = remove_profile_picture
    updateProfileStatus = update_profile_status
    updateProfileName = update_profile_name
    createCallLink = create_call_link
    fetchBlocklist = fetch_blocklist
    updateBlockStatus = update_block_status
    getBusinessProfile = get_business_profile
    cleanDirtyBits = clean_dirty_bits
    profilePictureUrl = profile_picture_url
    sendPresenceUpdate = send_presence_update
    presenceSubscribe = presence_subscribe
    resyncAppState = resync_app_state
    appPatch = app_patch
    fetchProps = fetch_props
    executeInitQueries = execute_init_queries
    chatModify = chat_modify
    updateDisableLinkPreviewsPrivacy = update_disable_link_previews_privacy
    addOrEditContact = add_or_edit_contact
    removeContact = remove_contact
    addLabel = add_label
    addChatLabel = add_chat_label
    removeChatLabel = remove_chat_label
    addMessageLabel = add_message_label
    removeMessageLabel = remove_message_label
    addOrEditQuickReply = add_or_edit_quick_reply
    removeQuickReply = remove_quick_reply
    upsertMessage = upsert_message
    modifyChat = modify_chat
    setLinkPreviewsPrivacy = set_link_previews_privacy
    setStarMessages = set_star_messages
    upsertContactEntry = upsert_contact_entry
    deleteContactEntry = delete_contact_entry
    upsertLabelEntry = upsert_label_entry
    addChatLabelEntry = add_chat_label_entry
    removeChatLabelEntry = remove_chat_label_entry
    addMessageLabelEntry = add_message_label_entry
    removeMessageLabelEntry = remove_message_label_entry
    upsertQuickReplyEntry = upsert_quick_reply_entry
    deleteQuickReplyEntry = delete_quick_reply_entry
    setArchiveChat = set_archive_chat
    setMuteChat = set_mute_chat
    setMarkRead = set_mark_read
    fetchStatusFor = fetch_status_for
    fetchDisappearingDurationFor = fetch_disappearing_duration_for
    checkOnWhatsApp = check_on_whatsapp
    resolvePnFromLid = resolve_pn_from_lid
    fetchProfilePicture = fetch_profile_picture
    setProfilePicture = set_profile_picture
    clearProfilePicture = clear_profile_picture
    setProfileStatus = set_profile_status
    setProfileName = set_profile_name
    setBlockStatusEntry = set_block_status_entry
    cleanDirtyEntry = clean_dirty_entry
    createCallLinkEntry = create_call_link_entry
    setPresenceState = set_presence_state
    subscribePresenceUpdates = subscribe_presence_updates
