from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, TypedDict, cast

from ..types.events import BufferedEventData
from ..types.message import WAMessageStatus
from .generics import trim_undefined
from .messages import update_message_with_reaction, update_message_with_receipt

BUFFERABLE_EVENT: tuple[str, ...] = (
    "messaging-history.set",
    "chats.upsert",
    "chats.update",
    "chats.delete",
    "contacts.upsert",
    "contacts.update",
    "messages.upsert",
    "messages.update",
    "messages.delete",
    "messages.reaction",
    "message-receipt.update",
    "groups.update",
)


class MessageUpsertItem(TypedDict):
    message: dict[str, Any]
    type: str


class MessageUpdateItem(TypedDict):
    key: dict[str, Any]
    update: dict[str, Any]


class MessageReactionItem(TypedDict):
    key: dict[str, Any]
    reactions: list[dict[str, Any]]


class MessageReceiptItem(TypedDict):
    key: dict[str, Any]
    userReceipt: list[dict[str, Any]]


class BaileysBufferableEventEmitter:
    def __init__(self, logger: Any) -> None:
        self._logger = logger
        self._handlers: dict[str, list[Callable[[Any], Awaitable[None] | None]]] = {}
        self._history_cache: set[str] = set()
        self._data = _make_buffer_data()
        self._is_buffering = False
        self._buffer_timeout: asyncio.TimerHandle | None = None
        self._flush_pending_timeout: asyncio.TimerHandle | None = None
        self._buffer_count = 0
        self._max_history_cache_size = 10_000
        self._buffer_timeout_ms = 30_000
        self._lock = asyncio.Lock()

    def on(self, event: str, listener: Callable[[Any], Awaitable[None] | None]) -> None:
        self._handlers.setdefault(event, []).append(listener)

    def off(self, event: str, listener: Callable[[Any], Awaitable[None] | None]) -> None:
        handlers = self._handlers.get(event) or []
        if listener in handlers:
            handlers.remove(listener)

    def remove_all_listeners(self, event: str) -> None:
        self._handlers.pop(event, None)

    async def _emit_local(self, event: str, payload: Any) -> None:
        for handler in list(self._handlers.get(event, [])):
            result = handler(payload)
            if asyncio.iscoroutine(result):
                await cast(Awaitable[None], result)

    async def process(self, handler: Callable[[dict[str, Any]], Awaitable[None] | None]) -> Callable[[], None]:
        async def _listener(map_data: dict[str, Any]) -> None:
            result = handler(map_data)
            if asyncio.iscoroutine(result):
                await cast(Awaitable[None], result)

        self.on("event", _listener)

        def _unsubscribe() -> None:
            self.off("event", _listener)

        return _unsubscribe

    def buffer(self) -> None:
        if not self._is_buffering:
            self._logger.debug("Event buffer activated")
            self._is_buffering = True
            self._buffer_count = 0
            if self._buffer_timeout:
                self._buffer_timeout.cancel()
            loop = asyncio.get_running_loop()
            self._buffer_timeout = loop.call_later(self._buffer_timeout_ms / 1000.0, self._buffer_timeout_hit)
        self._buffer_count += 1

    def _buffer_timeout_hit(self) -> None:
        if self._is_buffering:
            self._logger.warning("Buffer timeout reached, auto-flushing")
            self.flush()

    def flush(self) -> bool:
        if not self._is_buffering:
            return False
        self._logger.debug("Flushing event buffer", extra={"bufferCount": self._buffer_count})
        self._is_buffering = False
        self._buffer_count = 0

        if self._buffer_timeout:
            self._buffer_timeout.cancel()
            self._buffer_timeout = None
        if self._flush_pending_timeout:
            self._flush_pending_timeout.cancel()
            self._flush_pending_timeout = None

        if len(self._history_cache) > self._max_history_cache_size:
            self._logger.debug("Clearing history cache", extra={"cacheSize": len(self._history_cache)})
            self._history_cache.clear()

        new_data = _make_buffer_data()
        chat_updates = list(self._data["chatUpdates"].values())
        conditional_left = 0
        for update in chat_updates:
            if update.get("conditional"):
                conditional_left += 1
                new_data["chatUpdates"][update["id"]] = update
                self._data["chatUpdates"].pop(update["id"], None)

        consolidated = _consolidate_events(self._data)
        if consolidated:
            asyncio.create_task(self._emit_local("event", consolidated))
            for event_name, event_payload in consolidated.items():
                asyncio.create_task(self._emit_local(event_name, event_payload))

        self._data = new_data
        self._logger.debug("released buffered events", extra={"conditionalChatUpdatesLeft": conditional_left})
        return True

    def is_buffering(self) -> bool:
        return self._is_buffering

    def create_buffered_function(
        self,
        work: Callable[..., Awaitable[Any]],
    ) -> Callable[..., Awaitable[Any]]:
        async def _wrapper(*args: Any, **kwargs: Any) -> Any:
            self.buffer()
            try:
                result = await work(*args, **kwargs)
                if self._buffer_count == 1:
                    loop = asyncio.get_running_loop()
                    loop.call_later(0.1, self._maybe_flush_single)
                return result
            finally:
                self._buffer_count = max(0, self._buffer_count - 1)
                if self._buffer_count == 0 and not self._flush_pending_timeout:
                    loop = asyncio.get_running_loop()
                    self._flush_pending_timeout = loop.call_later(0.1, self.flush)

        return _wrapper

    def _maybe_flush_single(self) -> None:
        if self._is_buffering and self._buffer_count == 1:
            self.flush()

    async def emit(self, event: str, ev_data: Any) -> bool:
        async with self._lock:
            if event == "messages.upsert":
                event_type = ev_data.get("type")
                existing_upserts = list(self._data["messageUpserts"].values())
                if existing_upserts:
                    buffered_type = existing_upserts[0]["type"]
                    if buffered_type != event_type:
                        self._logger.debug(
                            "messages.upsert type mismatch, emitting buffered messages",
                            extra={"bufferedType": buffered_type, "newType": event_type},
                        )
                        payload = {
                            "messages.upsert": {
                                "messages": [item["message"] for item in existing_upserts],
                                "type": buffered_type,
                            }
                        }
                        await self._emit_local("event", payload)
                        await self._emit_local("messages.upsert", payload["messages.upsert"])
                        self._data["messageUpserts"] = {}

            if self._is_buffering and event in BUFFERABLE_EVENT:
                _append(self._data, self._history_cache, event, ev_data, self._logger)
                return True

        payload = {event: ev_data}
        await self._emit_local("event", payload)
        await self._emit_local(event, ev_data)
        return True


def make_event_buffer(logger: Any) -> BaileysBufferableEventEmitter:
    return BaileysBufferableEventEmitter(logger)


def _make_buffer_data() -> dict[str, Any]:
    data = BufferedEventData().model_dump(by_alias=True)
    data["historySets"]["empty"] = True
    return data


def _append(
    data: dict[str, Any],
    history_cache: set[str],
    event: str,
    event_data: Any,
    logger: Any,
) -> None:
    if event == "messaging-history.set":
        for chat in cast(list[dict[str, Any]], event_data.get("chats", [])):
            chat_id = chat.get("id") or ""
            existing_chat = data["historySets"]["chats"].get(chat_id)
            if existing_chat:
                existing_chat["endOfHistoryTransferType"] = chat.get("endOfHistoryTransferType")
            if not existing_chat and chat_id not in history_cache:
                data["historySets"]["chats"][chat_id] = chat
                history_cache.add(chat_id)
                _absorbing_chat_update(data, chat, logger)

        for contact in cast(list[dict[str, Any]], event_data.get("contacts", [])):
            contact_id = contact["id"]
            existing_contact = data["historySets"]["contacts"].get(contact_id)
            if existing_contact:
                existing_contact.update(trim_undefined(contact))
            else:
                history_id = f"c:{contact_id}"
                has_any_name = contact.get("notify") or contact.get("name") or contact.get("verifiedName")
                if history_id not in history_cache or has_any_name:
                    data["historySets"]["contacts"][contact_id] = contact
                    history_cache.add(history_id)

        for message in cast(list[dict[str, Any]], event_data.get("messages", [])):
            key = _stringify_message_key(message.get("key", {}))
            existing_msg = data["historySets"]["messages"].get(key)
            if not existing_msg and key not in history_cache:
                data["historySets"]["messages"][key] = message
                history_cache.add(key)

        data["historySets"]["empty"] = False
        data["historySets"]["syncType"] = event_data.get("syncType")
        data["historySets"]["progress"] = event_data.get("progress")
        data["historySets"]["peerDataRequestSessionId"] = event_data.get("peerDataRequestSessionId")
        data["historySets"]["isLatest"] = event_data.get("isLatest") or data["historySets"]["isLatest"]
        return

    if event == "chats.upsert":
        for chat in cast(list[dict[str, Any]], event_data):
            chat_id = chat.get("id") or ""
            upsert = data["chatUpserts"].get(chat_id)
            if not upsert:
                upsert = data["historySets"]["chats"].get(chat_id)
                if upsert:
                    logger.debug("absorbed chat upsert in chat set", extra={"chatId": chat_id})
            if upsert:
                upsert = _concat_chats(upsert, chat)
            else:
                upsert = chat
                data["chatUpserts"][chat_id] = upsert
            _absorbing_chat_update(data, upsert, logger)
            if chat_id in data["chatDeletes"]:
                data["chatDeletes"].discard(chat_id)
        return

    if event == "chats.update":
        for update in cast(list[dict[str, Any]], event_data):
            chat_id = update.get("id")
            if not chat_id:
                continue
            conditional = update.get("conditional")
            condition_matches = conditional(data) if callable(conditional) else True
            if condition_matches is True:
                update.pop("conditional", None)
                upsert = data["historySets"]["chats"].get(chat_id) or data["chatUpserts"].get(chat_id)
                if upsert:
                    _concat_chats(upsert, update)
                else:
                    chat_update = data["chatUpdates"].get(chat_id, {})
                    data["chatUpdates"][chat_id] = _concat_chats(chat_update, update)
            elif condition_matches is None:
                data["chatUpdates"][chat_id] = update

            if chat_id in data["chatDeletes"]:
                data["chatDeletes"].discard(chat_id)
        return

    if event == "chats.delete":
        for chat_id in cast(list[str], event_data):
            data["chatDeletes"].add(chat_id)
            data["chatUpdates"].pop(chat_id, None)
            data["chatUpserts"].pop(chat_id, None)
            data["historySets"]["chats"].pop(chat_id, None)
        return

    if event == "contacts.upsert":
        for contact in cast(list[dict[str, Any]], event_data):
            contact_id = contact["id"]
            upsert = data["contactUpserts"].get(contact_id)
            if not upsert:
                upsert = data["historySets"]["contacts"].get(contact_id)
                if upsert:
                    logger.debug("absorbed contact upsert in contact set", extra={"contactId": contact_id})
            if upsert:
                upsert.update(trim_undefined(contact))
            else:
                upsert = contact
                data["contactUpserts"][contact_id] = upsert
            if data["contactUpdates"].get(contact_id):
                upsert.update(trim_undefined(contact))
                data["contactUpdates"].pop(contact_id, None)
        return

    if event == "contacts.update":
        for update in cast(list[dict[str, Any]], event_data):
            contact_id = update.get("id")
            if not contact_id:
                continue
            upsert = data["historySets"]["contacts"].get(contact_id) or data["contactUpserts"].get(contact_id)
            if upsert:
                upsert.update(update)
            else:
                contact_update = data["contactUpdates"].get(contact_id, {})
                contact_update.update(update)
                data["contactUpdates"][contact_id] = contact_update
        return

    if event == "messages.upsert":
        messages = cast(list[dict[str, Any]], event_data.get("messages", []))
        upsert_type = event_data.get("type", "notify")
        for message in messages:
            key = _stringify_message_key(message.get("key", {}))
            existing = (data["messageUpserts"].get(key) or {}).get("message")
            if not existing:
                existing = data["historySets"]["messages"].get(key)
                if existing:
                    logger.debug("absorbed message upsert in message set", extra={"messageId": key})
            if existing:
                message["messageTimestamp"] = existing.get("messageTimestamp")
            if data["messageUpdates"].get(key):
                logger.debug("absorbed prior message update in message upsert")
                message.update(data["messageUpdates"][key]["update"])
                data["messageUpdates"].pop(key, None)

            if data["historySets"]["messages"].get(key):
                data["historySets"]["messages"][key] = message
            else:
                prev_type = (data["messageUpserts"].get(key) or {}).get("type")
                data["messageUpserts"][key] = {
                    "message": message,
                    "type": "notify" if upsert_type == "notify" or prev_type == "notify" else upsert_type,
                }
        return

    if event == "messages.update":
        for item in cast(list[dict[str, Any]], event_data):
            key = _stringify_message_key(item["key"])
            existing = data["historySets"]["messages"].get(key) or (data["messageUpserts"].get(key) or {}).get("message")
            if existing:
                existing.update(item["update"])
                if item["update"].get("status") == int(WAMessageStatus.READ) and not item["key"].get("fromMe"):
                    _decrement_chat_read_counter_if_msg_did_unread(data, existing, logger)
            else:
                msg_update = data["messageUpdates"].get(key) or {"key": item["key"], "update": {}}
                msg_update["update"].update(item["update"])
                data["messageUpdates"][key] = cast(MessageUpdateItem, msg_update)
        return

    if event == "messages.delete":
        keys = event_data.get("keys")
        if keys:
            for key_obj in cast(list[dict[str, Any]], keys):
                key = _stringify_message_key(key_obj)
                if key not in data["messageDeletes"]:
                    data["messageDeletes"][key] = key_obj
                data["messageUpserts"].pop(key, None)
                data["messageUpdates"].pop(key, None)
        return

    if event == "messages.reaction":
        for item in cast(list[dict[str, Any]], event_data):
            key = _stringify_message_key(item["key"])
            existing = data["messageUpserts"].get(key)
            if existing:
                update_message_with_reaction(existing["message"], item["reaction"])
            else:
                if key not in data["messageReactions"]:
                    data["messageReactions"][key] = {"key": item["key"], "reactions": []}
                update_message_with_reaction(data["messageReactions"][key], item["reaction"])
        return

    if event == "message-receipt.update":
        for item in cast(list[dict[str, Any]], event_data):
            key = _stringify_message_key(item["key"])
            existing = data["messageUpserts"].get(key)
            if existing:
                update_message_with_receipt(existing["message"], item["receipt"])
            else:
                if key not in data["messageReceipts"]:
                    data["messageReceipts"][key] = {"key": item["key"], "userReceipt": []}
                update_message_with_receipt(data["messageReceipts"][key], item["receipt"])
        return

    if event == "groups.update":
        for update in cast(list[dict[str, Any]], event_data):
            group_id = update.get("id")
            if not group_id:
                continue
            group_update = data["groupUpdates"].get(group_id, {})
            if group_id not in data["groupUpdates"]:
                data["groupUpdates"][group_id] = group_update
            group_update.update(update)
        return

    raise ValueError(f'"{event}" cannot be buffered')


def _absorbing_chat_update(data: dict[str, Any], existing: dict[str, Any], logger: Any) -> None:
    chat_id = existing.get("id") or ""
    update = data["chatUpdates"].get(chat_id)
    if not update:
        return
    conditional = update.get("conditional")
    condition_matches = conditional(data) if callable(conditional) else True
    if condition_matches is True:
        update.pop("conditional", None)
        logger.debug("absorbed chat update in existing chat", extra={"chatId": chat_id})
        existing.update(_concat_chats(update, existing))
        data["chatUpdates"].pop(chat_id, None)
    elif condition_matches is False:
        logger.debug("chat update condition fail, removing", extra={"chatId": chat_id})
        data["chatUpdates"].pop(chat_id, None)


def _decrement_chat_read_counter_if_msg_did_unread(data: dict[str, Any], message: dict[str, Any], logger: Any) -> None:
    # delayed import to avoid circular dependency at import time.
    from .process_message import is_real_message, should_increment_chat_unread

    chat_id = cast(dict[str, Any], message.get("key", {})).get("remoteJid")
    if not chat_id:
        return
    chat = data["chatUpdates"].get(chat_id) or data["chatUpserts"].get(chat_id)
    unread_count = chat.get("unreadCount") if isinstance(chat, dict) else None
    if (
        chat
        and is_real_message(message)
        and should_increment_chat_unread(message)
        and isinstance(unread_count, int)
        and unread_count > 0
    ):
        logger.debug("decrementing chat counter", extra={"chatId": chat.get("id")})
        chat["unreadCount"] = unread_count - 1
        if chat["unreadCount"] == 0:
            chat.pop("unreadCount", None)


def _consolidate_events(data: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    history = data["historySets"]
    if not history["empty"]:
        result["messaging-history.set"] = {
            "chats": list(history["chats"].values()),
            "messages": list(history["messages"].values()),
            "contacts": list(history["contacts"].values()),
            "syncType": history.get("syncType"),
            "progress": history.get("progress"),
            "isLatest": history.get("isLatest"),
            "peerDataRequestSessionId": history.get("peerDataRequestSessionId"),
        }

    chat_upserts = list(data["chatUpserts"].values())
    if chat_upserts:
        result["chats.upsert"] = chat_upserts

    chat_updates = list(data["chatUpdates"].values())
    if chat_updates:
        result["chats.update"] = chat_updates

    chat_deletes = list(data["chatDeletes"])
    if chat_deletes:
        result["chats.delete"] = chat_deletes

    message_upserts = list(data["messageUpserts"].values())
    if message_upserts:
        result["messages.upsert"] = {"messages": [item["message"] for item in message_upserts], "type": message_upserts[0]["type"]}

    message_updates = list(data["messageUpdates"].values())
    if message_updates:
        result["messages.update"] = message_updates

    message_deletes = list(data["messageDeletes"].values())
    if message_deletes:
        result["messages.delete"] = {"keys": message_deletes}

    message_reactions = []
    for item in data["messageReactions"].values():
        for reaction in item["reactions"]:
            message_reactions.append({"key": item["key"], "reaction": reaction})
    if message_reactions:
        result["messages.reaction"] = message_reactions

    message_receipts = []
    for item in data["messageReceipts"].values():
        for receipt in item["userReceipt"]:
            message_receipts.append({"key": item["key"], "receipt": receipt})
    if message_receipts:
        result["message-receipt.update"] = message_receipts

    contact_upserts = list(data["contactUpserts"].values())
    if contact_upserts:
        result["contacts.upsert"] = contact_upserts

    contact_updates = list(data["contactUpdates"].values())
    if contact_updates:
        result["contacts.update"] = contact_updates

    group_updates = list(data["groupUpdates"].values())
    if group_updates:
        result["groups.update"] = group_updates

    return result


def _concat_chats(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    if b.get("unreadCount") is None and isinstance(a.get("unreadCount"), int) and a["unreadCount"] < 0:
        a["unreadCount"] = None
        b = dict(b)
        b["unreadCount"] = None
    if isinstance(a.get("unreadCount"), int) and isinstance(b.get("unreadCount"), int):
        b = dict(b)
        if b["unreadCount"] >= 0:
            b["unreadCount"] = max(b["unreadCount"], 0) + max(a["unreadCount"], 0)
    a.update(b)
    return a


def _stringify_message_key(key: dict[str, Any]) -> str:
    return f'{key.get("remoteJid")},{key.get("id")},{"1" if key.get("fromMe") else "0"}'


# camelCase aliases
makeEventBuffer = make_event_buffer


__all__ = ["make_event_buffer", "BaileysBufferableEventEmitter", "BUFFERABLE_EVENT"]
