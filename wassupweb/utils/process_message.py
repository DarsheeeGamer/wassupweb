from __future__ import annotations

import base64
import json
from typing import Any

from google.protobuf.json_format import MessageToDict

from ..types.message import WAMessageStubType
from ..utils.crypto import aes_decrypt_gcm, hmac_sign
from ..waproto import proto
from ..wabinary import (
    are_jids_same_user,
    is_hosted_lid_user,
    is_hosted_pn_user,
    is_jid_broadcast,
    is_jid_status_broadcast,
    jid_decode,
    jid_encode,
    jid_normalized_user,
)
from .generics import get_key_author, to_number
from .history import download_and_process_history_sync_notification
from .messages import get_content_type, normalize_message_content

_REAL_MSG_STUB_TYPES = {
    WAMessageStubType.CALL_MISSED_GROUP_VIDEO,
    WAMessageStubType.CALL_MISSED_GROUP_VOICE,
    WAMessageStubType.CALL_MISSED_VIDEO,
    WAMessageStubType.CALL_MISSED_VOICE,
}
_REAL_MSG_REQ_ME_STUB_TYPES = {WAMessageStubType.GROUP_PARTICIPANT_ADD}


def _stub_type_name(value: int | str | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    try:
        return WAMessageStubType(value).name
    except ValueError:
        return str(value)


def _stub_matches(value: int | str | None, target: WAMessageStubType) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return value == target.name
    return int(value) == int(target)


def clean_message(message: dict[str, Any], me_id: str, me_lid: str) -> None:
    key = message.setdefault("key", {})
    remote_jid = key.get("remoteJid")
    participant = key.get("participant")

    if is_hosted_pn_user(remote_jid) or is_hosted_lid_user(remote_jid):
        decoded = jid_decode(remote_jid) or {}
        server = "s.whatsapp.net" if is_hosted_pn_user(remote_jid) else "lid"
        key["remoteJid"] = jid_encode(decoded.get("user"), server)
    else:
        key["remoteJid"] = jid_normalized_user(remote_jid)

    if is_hosted_pn_user(participant) or is_hosted_lid_user(participant):
        decoded = jid_decode(participant) or {}
        server = "s.whatsapp.net" if is_hosted_pn_user(participant) else "lid"
        key["participant"] = jid_encode(decoded.get("user"), server)
    else:
        key["participant"] = jid_normalized_user(participant)

    content = normalize_message_content(message.get("message"))

    def _normalise_key(msg_key: dict[str, Any]) -> None:
        if not key.get("fromMe"):
            author = msg_key.get("participant") or msg_key.get("remoteJid")
            msg_key["fromMe"] = (
                are_jids_same_user(author, me_id) or are_jids_same_user(author, me_lid)
                if not msg_key.get("fromMe")
                else False
            )
            msg_key["remoteJid"] = key.get("remoteJid")
            msg_key["participant"] = msg_key.get("participant") or key.get("participant")

    if isinstance(content, dict):
        reaction = content.get("reactionMessage")
        if isinstance(reaction, dict) and isinstance(reaction.get("key"), dict):
            _normalise_key(reaction["key"])
        poll_update = content.get("pollUpdateMessage")
        if isinstance(poll_update, dict) and isinstance(poll_update.get("pollCreationMessageKey"), dict):
            _normalise_key(poll_update["pollCreationMessageKey"])


def is_real_message(message: dict[str, Any]) -> bool:
    normalized = normalize_message_content(message.get("message"))
    has_content = bool(get_content_type(normalized))
    stub_type = message.get("messageStubType")
    stub_is_real = any(_stub_matches(stub_type, value) for value in _REAL_MSG_STUB_TYPES)
    stub_req_me = any(_stub_matches(stub_type, value) for value in _REAL_MSG_REQ_ME_STUB_TYPES)
    protocol_message = normalized.get("protocolMessage") if isinstance(normalized, dict) else None
    reaction_message = normalized.get("reactionMessage") if isinstance(normalized, dict) else None
    poll_update_message = normalized.get("pollUpdateMessage") if isinstance(normalized, dict) else None

    return bool(normalized or stub_is_real or stub_req_me) and has_content and not protocol_message and not reaction_message and not poll_update_message


def should_increment_chat_unread(message: dict[str, Any]) -> bool:
    key = message.get("key", {})
    return not key.get("fromMe") and not message.get("messageStubType")


def get_chat_id(key: dict[str, Any]) -> str:
    remote_jid = key.get("remoteJid", "")
    participant = key.get("participant")
    from_me = bool(key.get("fromMe"))
    if is_jid_broadcast(remote_jid) and not is_jid_status_broadcast(remote_jid) and not from_me:
        return participant or remote_jid
    return remote_jid


def _to_bytes(value: Any) -> bytes:
    if value is None:
        return b""
    if isinstance(value, bytes):
        return value
    if isinstance(value, bytearray):
        return bytes(value)
    if isinstance(value, list):
        try:
            return bytes(value)
        except Exception:
            return b""
    if isinstance(value, str):
        padded = value + ("=" * ((4 - len(value) % 4) % 4))
        try:
            return base64.b64decode(padded)
        except Exception:
            return value.encode("latin1")
    return b""


def decrypt_event_response(
    response: dict[str, Any] | None,
    *,
    event_creator_jid: str,
    event_msg_id: str,
    event_enc_key: bytes,
    responder_jid: str,
) -> dict[str, Any]:
    if not response:
        return {}

    enc_payload = _to_bytes(response.get("encPayload") or response.get("enc_payload"))
    enc_iv = _to_bytes(response.get("encIv") or response.get("enc_iv"))
    if not enc_payload or not enc_iv:
        return {}

    sign = b"".join(
        [
            event_msg_id.encode("utf-8"),
            event_creator_jid.encode("utf-8"),
            responder_jid.encode("utf-8"),
            b"Event Response",
            bytes([1]),
        ]
    )
    key0 = hmac_sign(event_enc_key, bytes(32), "sha256")
    dec_key = hmac_sign(sign, key0, "sha256")
    aad = f"{event_msg_id}\u0000{responder_jid}".encode("utf-8")

    decrypted = aes_decrypt_gcm(enc_payload, dec_key, enc_iv, aad)
    decoded = proto.Message.EventResponseMessage.FromString(decrypted)
    try:
        return MessageToDict(decoded, preserving_proto_field_name=True)
    except Exception:
        return {"raw": decrypted}


def decrypt_poll_vote(
    vote: dict[str, Any] | None,
    *,
    poll_creator_jid: str,
    poll_msg_id: str,
    poll_enc_key: bytes,
    voter_jid: str,
) -> dict[str, Any]:
    if not vote:
        return {}

    enc_payload = _to_bytes(vote.get("encPayload") or vote.get("enc_payload"))
    enc_iv = _to_bytes(vote.get("encIv") or vote.get("enc_iv"))
    if not enc_payload or not enc_iv:
        return {}

    sign = b"".join(
        [
            poll_msg_id.encode("utf-8"),
            poll_creator_jid.encode("utf-8"),
            voter_jid.encode("utf-8"),
            b"Poll Vote",
            bytes([1]),
        ]
    )
    key0 = hmac_sign(poll_enc_key, bytes(32), "sha256")
    dec_key = hmac_sign(sign, key0, "sha256")
    aad = f"{poll_msg_id}\u0000{voter_jid}".encode("utf-8")

    decrypted = aes_decrypt_gcm(enc_payload, dec_key, enc_iv, aad)
    decoded = proto.Message.PollVoteMessage.FromString(decrypted)
    try:
        return MessageToDict(decoded, preserving_proto_field_name=True)
    except Exception:
        return {"raw": decrypted}


async def process_message(
    message: dict[str, Any],
    context: dict[str, Any],
) -> None:
    should_process_history_msg = bool(context.get("shouldProcessHistoryMsg"))
    placeholder_resend_cache = context.get("placeholderResendCache")
    ev = context["ev"]
    creds = context["creds"]
    signal_repository = context.get("signalRepository")
    key_store = context.get("keyStore")
    logger = context.get("logger")
    options = context.get("options") or {}
    get_message = context.get("getMessage") or (lambda _key: None)

    me = creds.get("me") or {}
    me_id = me.get("id", "")
    account_settings = creds.get("accountSettings") or {}

    chat: dict[str, Any] = {"id": jid_normalized_user(get_chat_id(message.get("key", {})))}
    is_real_msg = is_real_message(message)
    if is_real_msg:
        chat["messages"] = [{"message": message}]
        chat["conversationTimestamp"] = to_number(message.get("messageTimestamp"))
        if should_increment_chat_unread(message):
            chat["unreadCount"] = int(chat.get("unreadCount", 0)) + 1

    content = normalize_message_content(message.get("message"))
    if (is_real_msg or (content or {}).get("reactionMessage", {}).get("key", {}).get("fromMe")) and account_settings.get("unarchiveChats"):
        chat["archived"] = False
        chat["readOnly"] = False

    protocol_msg = (content or {}).get("protocolMessage") if isinstance(content, dict) else None
    if isinstance(protocol_msg, dict):
        protocol_type = protocol_msg.get("type")
        if protocol_type == "HISTORY_SYNC_NOTIFICATION" or protocol_type == 5:
            hist_notification = protocol_msg.get("historySyncNotification") or {}
            is_latest = not creds.get("processedHistoryMessages")
            if logger:
                logger.info(
                    "got history notification",
                    extra={"histNotification": hist_notification, "process": should_process_history_msg, "id": message.get("key", {}).get("id"), "isLatest": is_latest},
                )
            if should_process_history_msg:
                sync_type = hist_notification.get("syncType")
                if sync_type not in {"ON_DEMAND", 3}:
                    processed = list(creds.get("processedHistoryMessages") or [])
                    processed.append({"key": message.get("key"), "messageTimestamp": message.get("messageTimestamp")})
                    await ev.emit("creds.update", {"processedHistoryMessages": processed})

                data = await download_and_process_history_sync_notification(hist_notification, options, logger)

                lid_pn_mappings = data.get("lidPnMappings") or []
                if lid_pn_mappings and signal_repository and getattr(signal_repository, "lid_mapping", None):
                    try:
                        await signal_repository.lid_mapping.store_lid_pn_mappings(lid_pn_mappings)
                    except Exception as err:  # pragma: no cover - runtime storage failure
                        if logger:
                            logger.warning("failed to store LID-PN mappings from history sync", extra={"error": str(err)})

                await ev.emit(
                    "messaging-history.set",
                    {
                        **data,
                        "isLatest": (sync_type not in {"ON_DEMAND", 3}) and is_latest,
                        "peerDataRequestSessionId": hist_notification.get("peerDataRequestSessionId"),
                    },
                )

        elif protocol_type == "APP_STATE_SYNC_KEY_SHARE" or protocol_type == 10:
            key_share = (protocol_msg.get("appStateSyncKeyShare") or {}).get("keys") or []
            if key_share:
                new_app_state_sync_key_id = ""

                async def _tx_work() -> None:
                    nonlocal new_app_state_sync_key_id
                    new_keys: list[str] = []
                    for item in key_share:
                        key_id_raw = ((item.get("keyId") or {}).get("keyId")) or ""
                        str_key_id = key_id_raw if isinstance(key_id_raw, str) else ""
                        if not str_key_id:
                            continue
                        new_keys.append(str_key_id)
                        await key_store.set({"app-state-sync-key": {str_key_id: item.get("keyData")}})
                        new_app_state_sync_key_id = str_key_id
                    if logger:
                        logger.info("injecting new app state sync keys", extra={"newAppStateSyncKeyId": new_app_state_sync_key_id, "newKeys": new_keys})

                if key_store and hasattr(key_store, "transaction"):
                    await key_store.transaction(_tx_work, me_id)
                else:
                    await _tx_work()
                await ev.emit("creds.update", {"myAppStateKeyId": new_app_state_sync_key_id})
            else:
                if logger:
                    logger.info("recv app state sync with 0 keys", extra={"protocolMsg": protocol_msg})

        elif protocol_type == "REVOKE" or protocol_type == 0:
            await ev.emit(
                "messages.update",
                [
                    {
                        "key": {**message.get("key", {}), "id": (protocol_msg.get("key") or {}).get("id")},
                        "update": {"message": None, "messageStubType": "REVOKE", "key": message.get("key")},
                    }
                ],
            )
        elif protocol_type == "EPHEMERAL_SETTING" or protocol_type == 3:
            chat["ephemeralSettingTimestamp"] = to_number(message.get("messageTimestamp"))
            chat["ephemeralExpiration"] = protocol_msg.get("ephemeralExpiration")
        elif protocol_type == "PEER_DATA_OPERATION_REQUEST_RESPONSE_MESSAGE":
            response = protocol_msg.get("peerDataOperationRequestResponseMessage") or {}
            for result in response.get("peerDataOperationResult", []) or []:
                retry_response = (result or {}).get("placeholderMessageResendResponse") or {}
                web_message_info = retry_response.get("webMessageInfo")
                if not web_message_info:
                    continue
                msg_id = (web_message_info.get("key") or {}).get("id")
                cached_data = await placeholder_resend_cache.get(msg_id) if placeholder_resend_cache and msg_id else None
                if msg_id and placeholder_resend_cache:
                    if hasattr(placeholder_resend_cache, "del_"):
                        await placeholder_resend_cache.del_(msg_id)
                    else:
                        delete_fn = getattr(placeholder_resend_cache, "del", None)
                        if not delete_fn:
                            delete_fn = getattr(placeholder_resend_cache, "delete", None)
                        if not delete_fn:
                            delete_fn = getattr(placeholder_resend_cache, "remove", None)
                        if not delete_fn:
                            continue
                        maybe = delete_fn(msg_id)
                        if hasattr(maybe, "__await__"):
                            await maybe

                final_msg = dict(cached_data) if isinstance(cached_data, dict) else dict(web_message_info)
                if isinstance(cached_data, dict):
                    final_msg["message"] = web_message_info.get("message")
                    if web_message_info.get("messageTimestamp"):
                        final_msg["messageTimestamp"] = web_message_info["messageTimestamp"]

                if logger:
                    logger.debug("received placeholder resend", extra={"msgId": msg_id, "requestId": response.get("stanzaId")})
                await ev.emit(
                    "messages.upsert",
                    {"messages": [final_msg], "type": "notify", "requestId": response.get("stanzaId")},
                )
        elif protocol_type == "MESSAGE_EDIT":
            await ev.emit(
                "messages.update",
                [
                    {
                        "key": {**message.get("key", {}), "id": (protocol_msg.get("key") or {}).get("id")},
                        "update": {
                            "message": {"editedMessage": {"message": protocol_msg.get("editedMessage")}},
                            "messageTimestamp": (
                                to_number(protocol_msg.get("timestampMs")) // 1000
                                if protocol_msg.get("timestampMs")
                                else message.get("messageTimestamp")
                            ),
                        },
                    }
                ],
            )
        elif protocol_type == "GROUP_MEMBER_LABEL_CHANGE":
            label_msg = protocol_msg.get("memberLabel") or {}
            if label_msg.get("label"):
                await ev.emit(
                    "group.member-tag.update",
                    {
                        "groupId": chat["id"],
                        "label": label_msg["label"],
                        "participant": message.get("key", {}).get("participant"),
                        "participantAlt": message.get("key", {}).get("participantAlt"),
                        "messageTimestamp": to_number(message.get("messageTimestamp")),
                    },
                )
        elif protocol_type == "LID_MIGRATION_MAPPING_SYNC" and signal_repository:
            mapping_sync = (protocol_msg.get("lidMigrationMappingSyncMessage") or {}).get("encodedMappingPayload") or {}
            mappings = mapping_sync.get("pnToLidMappings") or []
            pairs = []
            for item in mappings:
                lid = item.get("latestLid") or item.get("assignedLid")
                pn = item.get("pn")
                if lid and pn:
                    pairs.append({"lid": f"{lid}@lid", "pn": f"{pn}@s.whatsapp.net"})
            if pairs and getattr(signal_repository, "lid_mapping", None):
                await signal_repository.lid_mapping.store_lid_pn_mappings(pairs)
                for pair in pairs:
                    await signal_repository.migrate_session(pair["pn"], pair["lid"])

    elif isinstance(content, dict) and content.get("reactionMessage"):
        reaction = {**content["reactionMessage"], "key": message.get("key")}
        await ev.emit(
            "messages.reaction",
            [{"reaction": reaction, "key": content["reactionMessage"].get("key")}],
        )
    elif isinstance(content, dict) and content.get("encEventResponseMessage"):
        enc_response = content.get("encEventResponseMessage") or {}
        creation_msg_key = enc_response.get("eventCreationMessageKey") or {}
        event_msg = await get_message(creation_msg_key)
        if event_msg:
            try:
                me_id_norm = jid_normalized_user(me_id)
                creator_key = creation_msg_key.get("participant") or creation_msg_key.get("remoteJid")
                creator_pn = creator_key
                if creator_key and creator_key.endswith("@lid") and signal_repository and getattr(signal_repository, "lid_mapping", None):
                    creator_pn = await signal_repository.lid_mapping.get_pn_for_lid(creator_key)
                event_creator_jid = get_key_author(
                    {
                        "remoteJid": jid_normalized_user(creator_pn),
                        "fromMe": me_id_norm == creator_pn,
                    },
                    me_id_norm,
                )
                responder_jid = get_key_author(message.get("key"), me_id_norm)
                event_enc_key = (event_msg.get("messageContextInfo") or {}).get("messageSecret")
                if not event_enc_key:
                    if logger:
                        logger.warning("event response: missing messageSecret for decryption", extra={"creationMsgKey": creation_msg_key})
                else:
                    response_msg = decrypt_event_response(
                        enc_response,
                        event_creator_jid=event_creator_jid,
                        event_msg_id=creation_msg_key.get("id"),
                        event_enc_key=event_enc_key if isinstance(event_enc_key, (bytes, bytearray)) else bytes(str(event_enc_key), "utf-8"),
                        responder_jid=responder_jid,
                    )
                    event_response = {
                        "eventResponseMessageKey": message.get("key"),
                        "senderTimestampMs": response_msg.get("timestampMs"),
                        "response": response_msg,
                    }
                    await ev.emit("messages.update", [{"key": creation_msg_key, "update": {"eventResponses": [event_response]}}])
            except Exception as err:  # pragma: no cover - response decryption branch is runtime dependent
                if logger:
                    logger.warning("failed to decrypt event response", extra={"error": str(err), "creationMsgKey": creation_msg_key})
        elif logger:
            logger.warning("event creation message not found, cannot decrypt response", extra={"creationMsgKey": creation_msg_key})
    elif message.get("messageStubType"):
        jid = message.get("key", {}).get("remoteJid")

        async def _emit_participants_update(action: str, participants: list[dict[str, Any]]) -> None:
            await ev.emit(
                "group-participants.update",
                {
                    "id": jid,
                    "author": message.get("key", {}).get("participant"),
                    "authorPn": message.get("key", {}).get("participantAlt"),
                    "participants": participants,
                    "action": action,
                },
            )

        async def _emit_group_update(update: dict[str, Any]) -> None:
            await ev.emit(
                "groups.update",
                [
                    {
                        "id": jid,
                        **update,
                        "author": message.get("key", {}).get("participant"),
                        "authorPn": message.get("key", {}).get("participantAlt"),
                    }
                ],
            )

        def _parse_participants() -> list[dict[str, Any]]:
            raw = message.get("messageStubParameters") or []
            out: list[dict[str, Any]] = []
            for item in raw:
                if isinstance(item, dict):
                    out.append(item)
                elif isinstance(item, str):
                    try:
                        parsed = json.loads(item)
                        if isinstance(parsed, dict):
                            out.append(parsed)
                    except Exception:
                        out.append({"lid": item, "phoneNumber": item})
            return out

        participants = _parse_participants()

        def _participants_include_me() -> bool:
            return any(are_jids_same_user(me_id, p.get("phoneNumber")) for p in participants)

        stub_type = message.get("messageStubType")
        if _stub_matches(stub_type, WAMessageStubType.GROUP_PARTICIPANT_CHANGE_NUMBER):
            await _emit_participants_update("modify", participants)
        elif _stub_matches(stub_type, WAMessageStubType.GROUP_PARTICIPANT_LEAVE) or _stub_matches(stub_type, WAMessageStubType.GROUP_PARTICIPANT_REMOVE):
            await _emit_participants_update("remove", participants)
            if _participants_include_me():
                chat["readOnly"] = True
        elif (
            _stub_matches(stub_type, WAMessageStubType.GROUP_PARTICIPANT_ADD)
            or _stub_matches(stub_type, WAMessageStubType.GROUP_PARTICIPANT_INVITE)
            or _stub_matches(stub_type, WAMessageStubType.GROUP_PARTICIPANT_ADD_REQUEST_JOIN)
        ):
            if _participants_include_me():
                chat["readOnly"] = False
            await _emit_participants_update("add", participants)
        elif _stub_matches(stub_type, WAMessageStubType.GROUP_PARTICIPANT_DEMOTE):
            await _emit_participants_update("demote", participants)
        elif _stub_matches(stub_type, WAMessageStubType.GROUP_PARTICIPANT_PROMOTE):
            await _emit_participants_update("promote", participants)
        elif _stub_matches(stub_type, WAMessageStubType.GROUP_CHANGE_ANNOUNCE):
            value = (message.get("messageStubParameters") or [None])[0]
            await _emit_group_update({"announce": value in ("true", "on", True)})
        elif _stub_matches(stub_type, WAMessageStubType.GROUP_CHANGE_RESTRICT):
            value = (message.get("messageStubParameters") or [None])[0]
            await _emit_group_update({"restrict": value in ("true", "on", True)})
        elif _stub_matches(stub_type, WAMessageStubType.GROUP_CHANGE_SUBJECT):
            name = (message.get("messageStubParameters") or [None])[0]
            chat["name"] = name
            await _emit_group_update({"subject": name})
        elif _stub_matches(stub_type, WAMessageStubType.GROUP_CHANGE_DESCRIPTION):
            description = (message.get("messageStubParameters") or [None])[0]
            chat["description"] = description
            await _emit_group_update({"desc": description})
        elif _stub_matches(stub_type, WAMessageStubType.GROUP_CHANGE_INVITE_LINK):
            code = (message.get("messageStubParameters") or [None])[0]
            await _emit_group_update({"inviteCode": code})
        elif _stub_matches(stub_type, WAMessageStubType.GROUP_MEMBER_ADD_MODE):
            value = (message.get("messageStubParameters") or [None])[0]
            await _emit_group_update({"memberAddMode": value == "all_member_add"})
        elif _stub_matches(stub_type, WAMessageStubType.GROUP_MEMBERSHIP_JOIN_APPROVAL_MODE):
            value = (message.get("messageStubParameters") or [None])[0]
            await _emit_group_update({"joinApprovalMode": value == "on"})
        elif _stub_matches(stub_type, WAMessageStubType.GROUP_MEMBERSHIP_JOIN_APPROVAL_REQUEST_NON_ADMIN_ADD):
            params = message.get("messageStubParameters") or []
            participant = json.loads(params[0]) if params and isinstance(params[0], str) else {}
            await ev.emit(
                "group.join-request",
                {
                    "id": jid,
                    "author": message.get("key", {}).get("participant"),
                    "authorPn": message.get("key", {}).get("participantAlt"),
                    "participant": participant.get("lid"),
                    "participantPn": participant.get("pn"),
                    "action": params[1] if len(params) > 1 else None,
                    "method": params[2] if len(params) > 2 else None,
                },
            )

    if len(chat.keys()) > 1:
        await ev.emit("chats.update", [chat])


# camelCase aliases
cleanMessage = clean_message
isRealMessage = is_real_message
shouldIncrementChatUnread = should_increment_chat_unread
getChatId = get_chat_id
decryptEventResponse = decrypt_event_response
decryptPollVote = decrypt_poll_vote
processMessage = process_message


__all__ = [
    "clean_message",
    "is_real_message",
    "should_increment_chat_unread",
    "get_chat_id",
    "decrypt_event_response",
    "decrypt_poll_vote",
    "process_message",
]
