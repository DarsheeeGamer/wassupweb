from __future__ import annotations

import json
from typing import Any

from google.protobuf.json_format import MessageToDict

from ..waproto import proto
from ..types.message import WAMessageStatus, WAMessageStubType
from ..wabinary import (
    are_jids_same_user,
    get_binary_node_child,
    is_hosted_lid_user,
    is_hosted_pn_user,
    is_jid_broadcast,
    is_jid_group,
    is_jid_meta_ai,
    is_jid_newsletter,
    is_jid_status_broadcast,
    is_lid_user,
    is_pn_user,
)
from ..wabinary.types import BinaryNode
from .generics import unpad_random_max16

NO_MESSAGE_FOUND_ERROR_TEXT = "Message absent from node"
MISSING_KEYS_ERROR_TEXT = "Key used already or never filled"

DECRYPTION_RETRY_CONFIG = {
    "maxRetries": 3,
    "baseDelayMs": 100,
    "sessionRecordErrors": ["No session record", "SessionError: No session record"],
}

NACK_REASONS = {
    "ParsingError": 487,
    "UnrecognizedStanza": 488,
    "UnrecognizedStanzaClass": 489,
    "UnrecognizedStanzaType": 490,
    "InvalidProtobuf": 491,
    "InvalidHostedCompanionStanza": 493,
    "MissingMessageSecret": 495,
    "SignalErrorOldCounter": 496,
    "MessageDeletedOnPeer": 499,
    "UnhandledError": 500,
    "UnsupportedAdminRevoke": 550,
    "UnsupportedLIDGroup": 551,
    "DBOperationFailed": 552,
}


async def get_decryption_jid(sender: str, repository: Any) -> str:
    if is_lid_user(sender) or is_hosted_lid_user(sender):
        return sender
    mapped = await repository.lid_mapping.get_lid_for_pn(sender) if repository and getattr(repository, "lid_mapping", None) else None
    return mapped or sender


def extract_addressing_context(stanza: BinaryNode) -> dict[str, Any]:
    sender_alt: str | None = None
    recipient_alt: str | None = None
    sender = stanza.attrs.get("participant") or stanza.attrs.get("from")
    addressing_mode = stanza.attrs.get("addressing_mode") or ("lid" if (sender or "").endswith("lid") else "pn")
    if addressing_mode == "lid":
        sender_alt = stanza.attrs.get("participant_pn") or stanza.attrs.get("sender_pn") or stanza.attrs.get("peer_recipient_pn")
        recipient_alt = stanza.attrs.get("recipient_pn")
    else:
        sender_alt = stanza.attrs.get("participant_lid") or stanza.attrs.get("sender_lid") or stanza.attrs.get("peer_recipient_lid")
        recipient_alt = stanza.attrs.get("recipient_lid")
    return {"addressingMode": addressing_mode, "senderAlt": sender_alt, "recipientAlt": recipient_alt}


def decode_message_node(stanza: BinaryNode, me_id: str, me_lid: str) -> dict[str, Any]:
    msg_id = stanza.attrs.get("id")
    from_jid = stanza.attrs.get("from")
    participant = stanza.attrs.get("participant")
    recipient = stanza.attrs.get("recipient")
    if not from_jid:
        raise ValueError("message stanza missing 'from'")

    addressing_context = extract_addressing_context(stanza)
    from_me = False

    def _is_me(jid: str | None) -> bool:
        return bool(jid and are_jids_same_user(jid, me_id))

    def _is_me_lid(jid: str | None) -> bool:
        return bool(jid and are_jids_same_user(jid, me_lid))

    if is_pn_user(from_jid) or is_lid_user(from_jid) or is_hosted_lid_user(from_jid) or is_hosted_pn_user(from_jid):
        if recipient and not is_jid_meta_ai(recipient):
            if not _is_me(from_jid) and not _is_me_lid(from_jid):
                raise ValueError("recipient present but message not from me")
            from_me = True
            chat_id = recipient
        else:
            chat_id = from_jid
        msg_type = "chat"
        author = from_jid
    elif is_jid_group(from_jid):
        if not participant:
            raise ValueError("No participant in group message")
        if _is_me(participant) or _is_me_lid(participant):
            from_me = True
        msg_type = "group"
        author = participant
        chat_id = from_jid
    elif is_jid_broadcast(from_jid):
        if not participant:
            raise ValueError("No participant in broadcast message")
        is_participant_me = _is_me(participant)
        msg_type = "direct_peer_status" if (is_jid_status_broadcast(from_jid) and is_participant_me) else (
            "other_status" if is_jid_status_broadcast(from_jid) else ("peer_broadcast" if is_participant_me else "other_broadcast")
        )
        from_me = is_participant_me
        chat_id = from_jid
        author = participant
    elif is_jid_newsletter(from_jid):
        msg_type = "newsletter"
        chat_id = from_jid
        author = from_jid
        if _is_me(from_jid) or _is_me_lid(from_jid):
            from_me = True
    else:
        raise ValueError("Unknown message type")

    push_name = stanza.attrs.get("notify")
    key = {
        "remoteJid": chat_id,
        "remoteJidAlt": addressing_context["senderAlt"] if not is_jid_group(chat_id) else None,
        "fromMe": from_me,
        "id": msg_id,
        "participant": participant,
        "participantAlt": addressing_context["senderAlt"] if is_jid_group(chat_id) else None,
        "addressingMode": addressing_context["addressingMode"],
    }
    if msg_type == "newsletter" and stanza.attrs.get("server_id"):
        key["server_id"] = stanza.attrs.get("server_id")

    full_message: dict[str, Any] = {
        "key": key,
        "category": stanza.attrs.get("category"),
        "messageTimestamp": int(stanza.attrs.get("t") or 0),
        "pushName": push_name,
        "broadcast": is_jid_broadcast(from_jid),
    }
    if key["fromMe"]:
        full_message["status"] = int(WAMessageStatus.SERVER_ACK)
    return {"fullMessage": full_message, "author": author, "sender": author if msg_type == "chat" else chat_id}


def decrypt_message_node(
    stanza: BinaryNode,
    me_id: str,
    me_lid: str,
    repository: Any,
    logger: Any,
) -> dict[str, Any]:
    decoded = decode_message_node(stanza, me_id, me_lid)
    full_message = decoded["fullMessage"]
    author = decoded["author"]
    sender = decoded["sender"]

    async def decrypt() -> None:
        decryptables = 0
        if isinstance(stanza.content, list):
            for item in stanza.content:
                tag = item.tag
                attrs = item.attrs
                content = item.content

                if tag == "unavailable" and attrs.get("type") == "view_once":
                    full_message["key"]["isViewOnce"] = True
                if attrs.get("count") and tag == "enc":
                    full_message["retryCount"] = int(attrs["count"])

                if tag not in {"enc", "plaintext"}:
                    continue
                if not isinstance(content, (bytes, bytearray)):
                    continue

                decryptables += 1
                decryption_jid = await get_decryption_jid(author, repository)
                try:
                    e2e_type = "plaintext" if tag == "plaintext" else attrs.get("type")
                    if e2e_type == "skmsg":
                        msg_buffer = await repository.decrypt_group_message({"group": sender, "authorJid": author, "msg": bytes(content)})
                    elif e2e_type in {"pkmsg", "msg"}:
                        msg_buffer = await repository.decrypt_message({"jid": decryption_jid, "type": e2e_type, "ciphertext": bytes(content)})
                    elif e2e_type == "plaintext":
                        msg_buffer = bytes(content)
                    else:
                        raise ValueError(f"Unknown e2e type: {e2e_type}")

                    payload = unpad_random_max16(msg_buffer) if e2e_type != "plaintext" else msg_buffer
                    parsed_message: dict[str, Any]
                    try:
                        message_proto = proto.Message.FromString(payload)
                        parsed_message = MessageToDict(message_proto, preserving_proto_field_name=True)
                    except Exception:
                        try:
                            parsed_message = json.loads(payload.decode("utf-8"))
                            if not isinstance(parsed_message, dict):
                                parsed_message = {"raw": payload}
                        except Exception:
                            parsed_message = {"raw": payload}

                    if full_message.get("message"):
                        full_message["message"].update(parsed_message)
                    else:
                        full_message["message"] = parsed_message
                except Exception as err:  # pragma: no cover - runtime decryption path
                    logger.error("failed to decrypt message", extra={"key": full_message.get("key"), "error": str(err), "sender": sender, "author": author})
                    full_message["messageStubType"] = int(WAMessageStubType.CIPHERTEXT)
                    full_message["messageStubParameters"] = [str(err)]

        if not decryptables and not full_message.get("key", {}).get("isViewOnce"):
            full_message["messageStubType"] = int(WAMessageStubType.CIPHERTEXT)
            full_message["messageStubParameters"] = [NO_MESSAGE_FOUND_ERROR_TEXT]

    return {
        "fullMessage": full_message,
        "category": stanza.attrs.get("category"),
        "author": author,
        "decrypt": decrypt,
    }


def is_session_record_error(error: Any) -> bool:
    error_message = str(getattr(error, "message", None) or error)
    return any(pattern in error_message for pattern in DECRYPTION_RETRY_CONFIG["sessionRecordErrors"])


# camelCase aliases
getDecryptionJid = get_decryption_jid
extractAddressingContext = extract_addressing_context
decodeMessageNode = decode_message_node
decryptMessageNode = decrypt_message_node
