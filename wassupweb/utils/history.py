from __future__ import annotations

import json
import zlib
from typing import Any

from ..wabinary import (
    is_hosted_lid_user,
    is_hosted_pn_user,
    is_lid_user,
    is_pn_user,
)
from .generics import to_number
from .messages import normalize_message_content


def _extract_pn_from_messages(messages: list[dict[str, Any]]) -> str | None:
    for item in messages:
        message = item.get("message") or item
        key = message.get("key", {})
        user_receipt = message.get("userReceipt") or []
        if not key.get("fromMe") or not user_receipt:
            continue
        user_jid = (user_receipt[0] or {}).get("userJid")
        if user_jid and (is_pn_user(user_jid) or is_hosted_pn_user(user_jid)):
            return user_jid
    return None


async def download_history(msg: dict[str, Any], _options: dict[str, Any] | None = None) -> dict[str, Any]:
    # Python port expects already-fetched bytes or inline dict payload.
    if isinstance(msg.get("historySync"), dict):
        return msg["historySync"]

    payload = msg.get("historyData")
    if isinstance(payload, (bytes, bytearray)):
        try:
            inflated = zlib.decompress(bytes(payload))
            return json.loads(inflated.decode("utf-8"))
        except Exception:
            try:
                return json.loads(bytes(payload).decode("utf-8"))
            except Exception as error:  # pragma: no cover - malformed external payload
                raise ValueError("failed to decode history payload") from error

    raise ValueError("history payload missing; expected historySync dict or historyData bytes")


def process_history_message(item: dict[str, Any], logger: Any = None) -> dict[str, Any]:
    messages: list[dict[str, Any]] = []
    contacts: list[dict[str, Any]] = []
    chats: list[dict[str, Any]] = []
    lid_pn_mappings: list[dict[str, str]] = []

    if logger:
        logger.debug("processing history payload", extra={"progress": item.get("progress"), "syncType": item.get("syncType")})

    for mapping in item.get("phoneNumberToLidMappings", []) or []:
        lid_jid = mapping.get("lidJid")
        pn_jid = mapping.get("pnJid")
        if lid_jid and pn_jid:
            lid_pn_mappings.append({"lid": lid_jid, "pn": pn_jid})

    sync_type = item.get("syncType")
    if sync_type in {"INITIAL_BOOTSTRAP", "RECENT", "FULL", "ON_DEMAND", 0, 1, 2, 3}:
        for chat in item.get("conversations", []) or []:
            chat_obj = dict(chat)
            chat_id = chat_obj.get("id", "")
            phone_number = chat_obj.get("pnJid")
            if not phone_number and (is_pn_user(chat_id) or is_hosted_pn_user(chat_id)):
                phone_number = chat_id
            contacts.append(
                {
                    "id": chat_id,
                    "name": chat_obj.get("displayName") or chat_obj.get("name") or chat_obj.get("username"),
                    "lid": chat_obj.get("lidJid") or chat_obj.get("accountLid"),
                    "phoneNumber": phone_number,
                }
            )

            is_lid = is_lid_user(chat_id) or is_hosted_lid_user(chat_id)
            is_pn = is_pn_user(chat_id) or is_hosted_pn_user(chat_id)
            if is_lid and chat_obj.get("pnJid"):
                lid_pn_mappings.append({"lid": chat_id, "pn": chat_obj["pnJid"]})
            elif is_pn and chat_obj.get("lidJid"):
                lid_pn_mappings.append({"lid": chat_obj["lidJid"], "pn": chat_id})
            elif is_lid and not chat_obj.get("pnJid"):
                pn_from_receipt = _extract_pn_from_messages(chat_obj.get("messages") or [])
                if pn_from_receipt:
                    lid_pn_mappings.append({"lid": chat_id, "pn": pn_from_receipt})

            msg_items = chat_obj.get("messages") or []
            chat_obj.pop("messages", None)
            for item_msg in msg_items:
                message = item_msg.get("message") if isinstance(item_msg, dict) else item_msg
                if isinstance(message, dict):
                    messages.append(message)
                    if not chat_obj.get("messages"):
                        chat_obj["messages"] = [{"message": message}]
                    if not message.get("key", {}).get("fromMe") and not chat_obj.get("lastMessageRecvTimestamp"):
                        chat_obj["lastMessageRecvTimestamp"] = to_number(message.get("messageTimestamp"))

                    stub_type = message.get("messageStubType")
                    params = message.get("messageStubParameters") or []
                    if stub_type in {"BIZ_PRIVACY_MODE_TO_BSP", "BIZ_PRIVACY_MODE_TO_FB", 120, 121} and params:
                        contacts.append(
                            {
                                "id": message.get("key", {}).get("participant") or message.get("key", {}).get("remoteJid"),
                                "verifiedName": params[0],
                            }
                        )
            chats.append(chat_obj)
    elif sync_type in {"PUSH_NAME", 4}:
        for contact in item.get("pushnames", []) or []:
            contacts.append({"id": contact.get("id"), "notify": contact.get("pushname")})

    return {
        "chats": chats,
        "contacts": contacts,
        "messages": messages,
        "lidPnMappings": lid_pn_mappings,
        "syncType": sync_type,
        "progress": item.get("progress"),
    }


async def download_and_process_history_sync_notification(
    msg: dict[str, Any],
    options: dict[str, Any] | None = None,
    logger: Any = None,
) -> dict[str, Any]:
    if msg.get("initialHistBootstrapInlinePayload"):
        try:
            payload = zlib.decompress(msg["initialHistBootstrapInlinePayload"])
            history_msg = json.loads(payload.decode("utf-8"))
        except Exception:
            history_msg = msg.get("initialHistBootstrapInlinePayload")
    else:
        history_msg = await download_history(msg, options)
    if not isinstance(history_msg, dict):
        raise ValueError("history sync notification payload was not a dict")
    return process_history_message(history_msg, logger)


def get_history_msg(message: dict[str, Any] | None) -> dict[str, Any] | None:
    normalized = normalize_message_content(message) if message else None
    protocol_msg = normalized.get("protocolMessage") if isinstance(normalized, dict) else None
    if isinstance(protocol_msg, dict):
        return protocol_msg.get("historySyncNotification")
    return None


# camelCase aliases
downloadAndProcessHistorySyncNotification = download_and_process_history_sync_notification
processHistoryMessage = process_history_message
downloadHistory = download_history
getHistoryMsg = get_history_msg


__all__ = [
    "download_history",
    "process_history_message",
    "download_and_process_history_sync_notification",
    "get_history_msg",
]
