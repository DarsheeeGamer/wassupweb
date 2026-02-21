from __future__ import annotations

import asyncio
import base64
import contextlib
import copy
import hashlib
import os
import re
import secrets
from datetime import UTC, datetime
from typing import Any, AsyncIterator, Literal, cast
from urllib.request import Request, urlopen

from ..defaults import CALL_AUDIO_PREFIX, CALL_VIDEO_PREFIX, WA_DEFAULT_EPHEMERAL
from ..types.message import (
    AnyMessageContent,
    MessageUserReceipt,
    WAMessage,
    WAMessageContent,
    WAMessageKey,
)
from ..wabinary import is_jid_group, is_jid_newsletter, is_jid_status_broadcast, jid_normalized_user
from .generics import generate_message_id_v2, get_key_author, unix_timestamp_seconds
from .messages_media import (
    download_content_from_message,
    encrypted_stream,
    generate_thumbnail,
    get_audio_duration,
    get_audio_waveform,
    get_raw_media_upload_data,
)
from .reporting_utils import should_include_reporting_token

URL_REGEX = re.compile(
    r"https://(?![^:@/\s]+:[^:@/\s]+@)[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}(:\d+)?(/[^\s]*)?",
    re.IGNORECASE,
)
_REUPLOAD_REQUIRED_STATUS = {404, 410}


def extract_url_from_text(text: str) -> str | None:
    match = URL_REGEX.search(text)
    return match.group(0) if match else None


async def generate_link_preview_if_required(
    text: str,
    get_url_info: Any,
    logger: Any = None,
) -> dict[str, Any] | None:
    url = extract_url_from_text(text)
    if get_url_info and url:
        try:
            return await get_url_info(url)
        except Exception as error:  # pragma: no cover - runtime resolver failure
            if logger:
                logger.warn("url generation failed", extra={"error": str(error)})
    return None


def prepare_disappearing_message_setting_content(ephemeral_expiration: int | None = None) -> dict[str, Any]:
    expiration = ephemeral_expiration or 0
    return {
        "ephemeralMessage": {
            "message": {
                "protocolMessage": {
                    "type": "EPHEMERAL_SETTING",
                    "ephemeralExpiration": expiration,
                }
            }
        }
    }


def generate_forward_message_content(message: WAMessage | dict[str, Any], force_forward: bool | None = None) -> dict[str, Any]:
    message_data = message.model_dump(by_alias=True, exclude_none=False) if isinstance(message, WAMessage) else message
    content = message_data.get("message")
    if not content:
        raise ValueError("no content in message")

    normalized = normalize_message_content(copy.deepcopy(cast(dict[str, Any], content)))
    if not normalized:
        raise ValueError("cannot normalize forward content")

    key = get_content_type(normalized)
    if not key:
        return normalized

    inner = normalized.get(key)
    score = 0
    if isinstance(inner, dict):
        score = int(inner.get("contextInfo", {}).get("forwardingScore", 0))
    score += 0 if message_data.get("key", {}).get("fromMe") and not force_forward else 1

    if key == "conversation":
        normalized["extendedTextMessage"] = {"text": normalized.get("conversation", "")}
        normalized.pop("conversation", None)
        key = "extendedTextMessage"

    target = normalized.get(key) or {}
    if not isinstance(target, dict):
        target = {}
        normalized[key] = target
    if score > 0:
        target["contextInfo"] = {"forwardingScore": score, "isForwarded": True}
    else:
        target["contextInfo"] = {}
    return normalized


def has_non_nullish_property(message: AnyMessageContent, key: str) -> bool:
    return isinstance(message, dict) and key in message and message[key] is not None


def _merge_mentions(m: dict[str, Any], mentions: Any) -> None:
    if not mentions:
        return
    message_type = get_content_type(m)
    if not message_type:
        return
    key = m.get(message_type)
    if not isinstance(key, dict):
        return
    context = key.setdefault("contextInfo", {})
    context["mentionedJid"] = mentions


def _merge_context_info_after_wrap(m: dict[str, Any], context_info: Any) -> None:
    if not context_info:
        return
    message_type = get_content_type(m)
    if not message_type:
        return
    key = m.get(message_type)
    if not isinstance(key, dict):
        return
    if isinstance(key.get("contextInfo"), dict):
        key["contextInfo"] = {**cast(dict[str, Any], key["contextInfo"]), **cast(dict[str, Any], context_info)}
    else:
        key["contextInfo"] = dict(cast(dict[str, Any], context_info))


_MEDIA_KIND_MAP = {
    "image": "imageMessage",
    "video": "videoMessage",
    "audio": "audioMessage",
    "document": "documentMessage",
    "sticker": "stickerMessage",
}

_MEDIA_MIMETYPE_DEFAULTS = {
    "image": "image/jpeg",
    "video": "video/mp4",
    "audio": "audio/ogg; codecs=opus",
    "document": "application/octet-stream",
    "sticker": "image/webp",
}


async def _maybe_await(value: Any) -> Any:
    if hasattr(value, "__await__"):
        return await value
    return value


def _build_cacheable_media_key(media_kind: str, media_value: Any, media_cache: Any) -> str | None:
    if not media_cache or not isinstance(media_value, dict):
        return None
    url = media_value.get("url")
    if not url:
        return None
    return f"{media_kind}:{url}"


async def _cache_get(cache: Any, key: str) -> Any:
    getter = getattr(cache, "get", None)
    if callable(getter):
        return await _maybe_await(getter(key))
    if isinstance(cache, dict):
        return cache.get(key)
    return None


async def _cache_set(cache: Any, key: str, value: Any) -> None:
    setter = getattr(cache, "set", None)
    if callable(setter):
        await _maybe_await(setter(key, value))
        return
    if isinstance(cache, dict):
        cache[key] = value


def _assert_color(color: Any) -> int:
    if isinstance(color, int):
        return color if color > 0 else 0xFFFFFFFF + int(color) + 1
    if isinstance(color, str):
        hex_text = color.strip().replace("#", "")
        if len(hex_text) <= 6:
            hex_text = "FF" + hex_text.zfill(6)
        return int(hex_text, 16)
    raise ValueError("invalid color")


def _fetch_bytes(url: str, timeout_ms: int = 3000) -> bytes | None:
    try:
        req = Request(url, method="GET")
        with urlopen(req, timeout=timeout_ms / 1000.0) as resp:  # noqa: S310 - remote image fetch for WA parity
            if getattr(resp, "status", 200) >= 400:
                return None
            return resp.read()
    except Exception:
        return None


def _copy_media_fields(target: dict[str, Any], source: dict[str, Any], keys: list[str]) -> None:
    for key in keys:
        if key in source and source[key] is not None:
            target[key] = source[key]


async def _prepare_media_message(
    message: dict[str, Any],
    options: dict[str, Any],
) -> dict[str, Any] | None:
    for media_kind, message_key in _MEDIA_KIND_MAP.items():
        if not has_non_nullish_property(message, media_kind):
            continue

        media_value = message[media_kind]
        media_input = media_value
        media_opts = media_value if isinstance(media_value, dict) else {}

        media_cache = options.get("mediaCache")
        cache_key = _build_cacheable_media_key(media_kind, media_value, media_cache)
        if cache_key and media_cache:
            cached = await _cache_get(media_cache, cache_key)
            if isinstance(cached, dict):
                media_message = dict(cached)
                _copy_media_fields(media_message, media_opts, ["caption", "fileName", "ptt", "seconds", "gifPlayback", "mimetype"])
                return {message_key: media_message}

        if isinstance(media_value, dict) and "url" in media_value and "mediaKey" in media_value:
            media_message = dict(media_value)
        else:
            upload = options.get("upload")
            if not callable(upload):
                raise ValueError(f"upload function is required for {media_kind} messages")

            is_newsletter = bool(options.get("jid")) and is_jid_newsletter(str(options.get("jid")))
            if is_newsletter:
                raw = await get_raw_media_upload_data(media_input, media_kind, options.get("logger"))
                file_path = raw["filePath"]
                try:
                    upload_result = await _maybe_await(
                        upload(
                            file_path,
                            {
                                "mediaType": media_kind,
                                "fileEncSha256B64": base64.b64encode(raw["fileSha256"]).decode("ascii"),
                                "timeoutMs": options.get("mediaUploadTimeoutMs"),
                            },
                        )
                    )
                    upload_data = cast(dict[str, Any], upload_result or {})
                    media_message = {
                        "url": upload_data.get("mediaUrl") or upload_data.get("url"),
                        "directPath": upload_data.get("directPath"),
                        "fileSha256": raw["fileSha256"],
                        "fileLength": raw["fileLength"],
                    }
                finally:
                    with contextlib.suppress(Exception):
                        os.unlink(file_path)
            else:
                seconds_value = media_opts.get("seconds", message.get("seconds"))
                ptt_enabled = bool(media_opts.get("ptt", message.get("ptt")))
                requires_duration = media_kind == "audio" and seconds_value is None
                requires_thumb = media_kind in {"image", "video"} and media_opts.get("jpegThumbnail") is None
                requires_waveform = media_kind == "audio" and ptt_enabled
                requires_original = requires_duration or requires_thumb or requires_waveform

                encrypted = await encrypted_stream(
                    media_input,
                    media_kind,
                    {
                        "logger": options.get("logger"),
                        "saveOriginalFileIfRequired": requires_original,
                        "opts": options.get("options"),
                    },
                )
                enc_file_path = encrypted["encFilePath"]
                original_file_path = encrypted.get("originalFilePath")
                try:
                    upload_result = await _maybe_await(
                        upload(
                            enc_file_path,
                            {
                                "mediaType": media_kind,
                                "fileEncSha256B64": base64.b64encode(encrypted["fileEncSha256"]).decode("ascii"),
                                "timeoutMs": options.get("mediaUploadTimeoutMs"),
                            },
                        )
                    )
                    upload_data = cast(dict[str, Any], upload_result or {})

                    media_message = {
                        "url": upload_data.get("mediaUrl") or upload_data.get("url"),
                        "directPath": upload_data.get("directPath"),
                        "mediaKey": encrypted["mediaKey"],
                        "fileEncSha256": encrypted["fileEncSha256"],
                        "fileSha256": encrypted["fileSha256"],
                        "fileLength": encrypted["fileLength"],
                        "mediaKeyTimestamp": unix_timestamp_seconds(),
                    }

                    try:
                        if requires_thumb and isinstance(original_file_path, str):
                            thumb = await generate_thumbnail(
                                original_file_path,
                                cast(Literal["image", "video"], media_kind),
                                {"logger": options.get("logger")},
                            )
                            thumb_b64 = thumb.get("thumbnail")
                            if isinstance(thumb_b64, str):
                                with contextlib.suppress(Exception):
                                    media_message["jpegThumbnail"] = base64.b64decode(thumb_b64)
                            dims = thumb.get("originalImageDimensions") or {}
                            if isinstance(dims, dict):
                                if dims.get("width") is not None:
                                    media_message["width"] = int(dims["width"])
                                if dims.get("height") is not None:
                                    media_message["height"] = int(dims["height"])

                        if requires_duration and isinstance(original_file_path, str):
                            duration = await get_audio_duration(original_file_path)
                            if duration is not None:
                                media_message["seconds"] = duration

                        if requires_waveform and isinstance(original_file_path, str):
                            waveform = await get_audio_waveform(original_file_path, options.get("logger"))
                            if waveform is not None:
                                media_message["waveform"] = waveform

                        if media_kind == "audio" and ptt_enabled and options.get("backgroundColor") is not None:
                            media_message["backgroundArgb"] = _assert_color(options.get("backgroundColor"))
                    except Exception as error:
                        logger = options.get("logger")
                        if logger:
                            logger.debug("media enrichment failed", extra={"mediaType": media_kind, "error": str(error)})
                finally:
                    with contextlib.suppress(Exception):
                        os.unlink(enc_file_path)
                    if isinstance(original_file_path, str):
                        with contextlib.suppress(Exception):
                            os.unlink(original_file_path)

        media_message.setdefault("mimetype", _MEDIA_MIMETYPE_DEFAULTS.get(media_kind, "application/octet-stream"))
        _copy_media_fields(
            media_message,
            message,
            [
                "caption",
                "fileName",
                "ptt",
                "seconds",
                "gifPlayback",
                "jpegThumbnail",
                "waveform",
                "mentions",
                "contextInfo",
            ],
        )
        _copy_media_fields(
            media_message,
            media_opts,
            [
                "caption",
                "fileName",
                "ptt",
                "seconds",
                "gifPlayback",
                "jpegThumbnail",
                "waveform",
                "mimetype",
                "mentions",
                "contextInfo",
            ],
        )
        if media_kind == "document" and "fileName" not in media_message:
            media_message["fileName"] = media_opts.get("fileName") or "file"
        if media_kind == "sticker":
            media_message.setdefault("stickerSentTs", int(datetime.now(UTC).timestamp() * 1000))
        if cache_key and media_cache:
            await _cache_set(media_cache, cache_key, media_message)
        return {message_key: media_message}
    return None


async def prepare_wa_message_media(
    message: dict[str, Any],
    options: dict[str, Any],
) -> dict[str, Any]:
    prepared = await _prepare_media_message(message, options)
    if not prepared:
        raise ValueError("invalid media message payload")
    return prepared


async def generate_wa_message_content(message: AnyMessageContent, options: dict[str, Any]) -> dict[str, Any]:
    m: dict[str, Any] = {}
    if message.get("ptv") and has_non_nullish_property(message, "video"):
        prepared_video = await _prepare_media_message({"video": message["video"]}, options)
        video_message = cast(dict[str, Any], prepared_video.get("videoMessage") if prepared_video else {})
        m["ptvMessage"] = video_message
    else:
        prepared_media = await _prepare_media_message(cast(dict[str, Any], message), options)
        if prepared_media:
            m = prepared_media
    if not m and has_non_nullish_property(message, "text"):
        ext: dict[str, Any] = {"text": message["text"]}
        url_info = message.get("linkPreview")
        if url_info is None:
            url_info = await generate_link_preview_if_required(
                message["text"],
                options.get("getUrlInfo"),
                options.get("logger"),
            )
        if url_info:
            ext["matchedText"] = url_info.get("matched-text")
            ext["jpegThumbnail"] = url_info.get("jpegThumbnail")
            ext["description"] = url_info.get("description")
            ext["title"] = url_info.get("title")
            ext["previewType"] = 0
            img = url_info.get("highQualityThumbnail")
            if img:
                ext["thumbnailDirectPath"] = img.get("directPath")
                ext["mediaKey"] = img.get("mediaKey")
                ext["mediaKeyTimestamp"] = img.get("mediaKeyTimestamp")
                ext["thumbnailWidth"] = img.get("width")
                ext["thumbnailHeight"] = img.get("height")
                ext["thumbnailSha256"] = img.get("fileSha256")
                ext["thumbnailEncSha256"] = img.get("fileEncSha256")
        if options.get("backgroundColor"):
            ext["backgroundArgb"] = options["backgroundColor"]
        if options.get("font") is not None:
            ext["font"] = options["font"]
        m["extendedTextMessage"] = ext
    elif not m and has_non_nullish_property(message, "contacts"):
        contacts = message["contacts"].get("contacts", [])
        if not contacts:
            raise ValueError("require at least 1 contact")
        if len(contacts) == 1:
            m["contactMessage"] = contacts[0]
        else:
            m["contactsArrayMessage"] = message["contacts"]
    elif not m and has_non_nullish_property(message, "location"):
        m["locationMessage"] = message["location"]
    elif not m and has_non_nullish_property(message, "react"):
        react = dict(message["react"])
        react.setdefault("senderTimestampMs", int(datetime.now(UTC).timestamp() * 1000))
        m["reactionMessage"] = react
    elif not m and has_non_nullish_property(message, "delete"):
        m["protocolMessage"] = {"key": message["delete"], "type": "REVOKE"}
    elif not m and has_non_nullish_property(message, "forward"):
        m = generate_forward_message_content(message["forward"], message.get("force"))
    elif not m and has_non_nullish_property(message, "disappearingMessagesInChat"):
        setting = message["disappearingMessagesInChat"]
        if isinstance(setting, bool):
            setting = WA_DEFAULT_EPHEMERAL if setting else 0
        m = prepare_disappearing_message_setting_content(int(setting))
    elif not m and has_non_nullish_property(message, "groupInvite"):
        invite = message["groupInvite"]
        m["groupInviteMessage"] = {
            "inviteCode": invite.get("inviteCode"),
            "inviteExpiration": invite.get("inviteExpiration"),
            "caption": invite.get("text"),
            "groupJid": invite.get("jid"),
            "groupName": invite.get("subject"),
        }
        get_profile_pic = options.get("getProfilePicUrl")
        if callable(get_profile_pic) and invite.get("jid"):
            try:
                pic_url = await _maybe_await(get_profile_pic(invite.get("jid"), "preview"))
                if isinstance(pic_url, str) and pic_url:
                    thumb = await asyncio.to_thread(_fetch_bytes, pic_url, 3000)
                    if isinstance(thumb, (bytes, bytearray)):
                        m["groupInviteMessage"]["jpegThumbnail"] = bytes(thumb)
            except Exception as error:
                logger = options.get("logger")
                if logger:
                    logger.debug("group invite thumbnail fetch failed", extra={"error": str(error)})
    elif not m and has_non_nullish_property(message, "pin"):
        m["pinInChatMessage"] = {
            "key": message["pin"],
            "type": message.get("type"),
            "senderTimestampMs": int(datetime.now(UTC).timestamp() * 1000),
        }
        m["messageContextInfo"] = {
            "messageAddOnDurationInSecs": int(message.get("time", 86400) if message.get("type") == 1 else 0)
        }
    elif not m and has_non_nullish_property(message, "buttonReply"):
        reply = message["buttonReply"]
        reply_type = message.get("type")
        if reply_type == "template":
            m["templateButtonReplyMessage"] = {
                "selectedDisplayText": reply.get("displayText"),
                "selectedId": reply.get("id"),
                "selectedIndex": reply.get("index"),
            }
        else:
            m["buttonsResponseMessage"] = {
                "selectedButtonId": reply.get("id"),
                "selectedDisplayText": reply.get("displayText"),
                "type": "DISPLAY_TEXT",
            }
    elif not m and has_non_nullish_property(message, "product"):
        product = dict(message["product"])
        product_image = product.get("productImage")
        if product_image is not None:
            prepared_image = await _prepare_media_message({"image": product_image}, options)
            image_msg = cast(dict[str, Any], prepared_image.get("imageMessage") if prepared_image else {})
            product["productImage"] = image_msg
        m["productMessage"] = {"product": product}
    elif not m and has_non_nullish_property(message, "listReply"):
        m["listResponseMessage"] = message["listReply"]
    elif not m and has_non_nullish_property(message, "event"):
        event = message["event"]
        start_time = int(event["startDate"].timestamp()) if isinstance(event.get("startDate"), datetime) else int(event.get("startDate", 0))
        end_time = (
            int(event["endDate"].timestamp())
            if isinstance(event.get("endDate"), datetime)
            else (int(event["endDate"]) if event.get("endDate") else None)
        )
        get_call_link = options.get("getCallLink")
        join_link: str | None = None
        if event.get("call") in {"audio", "video"} and callable(get_call_link):
            token = await _maybe_await(get_call_link(event.get("call"), {"startTime": start_time}))
            if isinstance(token, str) and token:
                join_link = (CALL_AUDIO_PREFIX if event.get("call") == "audio" else CALL_VIDEO_PREFIX) + token
        m["eventMessage"] = {
            "name": event.get("name"),
            "description": event.get("description"),
            "startTime": start_time,
            "endTime": end_time,
            "isCanceled": event.get("isCancelled", False),
            "extraGuestsAllowed": event.get("extraGuestsAllowed"),
            "isScheduleCall": event.get("isScheduleCall", False),
            "location": event.get("location"),
            **({"joinLink": join_link} if join_link else {}),
        }
        m["messageContextInfo"] = {"messageSecret": event.get("messageSecret") or secrets.token_bytes(32)}
    elif not m and has_non_nullish_property(message, "poll"):
        poll = message["poll"]
        selectable = int(poll.get("selectableCount", 0))
        values = poll.get("values")
        if not isinstance(values, list):
            raise ValueError("Invalid poll values")
        if selectable < 0 or selectable > len(values):
            raise ValueError(f"poll.selectableCount should be >= 0 and <= {len(values)}")
        poll_creation = {
            "name": poll.get("name"),
            "selectableOptionsCount": selectable,
            "options": [{"optionName": option_name} for option_name in values],
        }
        m["messageContextInfo"] = {"messageSecret": poll.get("messageSecret") or secrets.token_bytes(32)}
        if poll.get("toAnnouncementGroup"):
            m["pollCreationMessageV2"] = poll_creation
        elif selectable == 1:
            m["pollCreationMessageV3"] = poll_creation
        else:
            m["pollCreationMessage"] = poll_creation
    elif not m and has_non_nullish_property(message, "sharePhoneNumber"):
        m["protocolMessage"] = {"type": "SHARE_PHONE_NUMBER"}
    elif not m and has_non_nullish_property(message, "requestPhoneNumber"):
        m["requestPhoneNumberMessage"] = {}
    elif not m and has_non_nullish_property(message, "limitSharing"):
        m["protocolMessage"] = {
            "type": "LIMIT_SHARING",
            "limitSharing": {
                "sharingLimited": message["limitSharing"] is True,
                "trigger": 1,
                "limitSharingSettingTimestamp": int(datetime.now(UTC).timestamp() * 1000),
                "initiatedByMe": True,
            },
        }
    elif not m:
        m = await prepare_wa_message_media(cast(dict[str, Any], message), options)

    if message.get("viewOnce"):
        m = {"viewOnceMessage": {"message": m}}

    _merge_mentions(m, message.get("mentions"))

    if message.get("edit"):
        m = {
            "protocolMessage": {
                "key": message["edit"],
                "editedMessage": m,
                "timestampMs": int(datetime.now(UTC).timestamp() * 1000),
                "type": "MESSAGE_EDIT",
            }
        }

    _merge_context_info_after_wrap(m, message.get("contextInfo"))

    if should_include_reporting_token(m):
        ctx = m.setdefault("messageContextInfo", {})
        if not ctx.get("messageSecret"):
            ctx["messageSecret"] = secrets.token_bytes(32)
    return m


def generate_wa_message_from_content(
    jid: str,
    message: WAMessageContent,
    options: dict[str, Any],
) -> WAMessage:
    timestamp = options.get("timestamp") or datetime.now(UTC)
    if isinstance(timestamp, datetime):
        ts = unix_timestamp_seconds(timestamp)
    else:
        ts = int(timestamp)

    user_jid = options.get("userJid")
    quoted = options.get("quoted")
    inner_message = normalize_message_content(message) or message
    key = get_content_type(inner_message)

    if quoted and key and not is_jid_newsletter(jid):
        quoted_data = quoted.model_dump(by_alias=True) if isinstance(quoted, WAMessage) else dict(quoted)
        quoted_key = quoted_data.get("key", {})
        participant = quoted_data.get("participant") or quoted_key.get("participant") or quoted_key.get("remoteJid")
        if quoted_key.get("fromMe"):
            participant = user_jid

        quoted_msg = normalize_message_content(quoted_data.get("message")) or {}
        quoted_type = get_content_type(quoted_msg)
        if quoted_type:
            quoted_msg = {quoted_type: quoted_msg.get(quoted_type)}
            quoted_content = quoted_msg.get(quoted_type)
            if isinstance(quoted_content, dict) and "contextInfo" in quoted_content:
                quoted_content.pop("contextInfo", None)
        context = inner_message.setdefault(key, {}).setdefault("contextInfo", {})
        if participant:
            context["participant"] = jid_normalized_user(participant)
        context["stanzaId"] = quoted_key.get("id")
        context["quotedMessage"] = quoted_msg
        if jid != quoted_key.get("remoteJid"):
            context["remoteJid"] = quoted_key.get("remoteJid")

    ephemeral_expiration = options.get("ephemeralExpiration")
    if (
        ephemeral_expiration
        and key not in {"protocolMessage", "ephemeralMessage"}
        and not is_jid_newsletter(jid)
        and key
        and isinstance(inner_message.get(key), dict)
    ):
        target = inner_message[key]
        context = target.setdefault("contextInfo", {})
        context["expiration"] = int(ephemeral_expiration or WA_DEFAULT_EPHEMERAL)

    message_json = {
        "key": {
            "remoteJid": jid,
            "fromMe": True,
            "id": options.get("messageId") or generate_message_id_v2(),
        },
        "message": inner_message,
        "messageTimestamp": ts,
        "messageStubParameters": [],
        "participant": user_jid if (is_jid_group(jid) or is_jid_status_broadcast(jid)) else None,
        "status": 1,
    }
    return WAMessage.model_validate(message_json)


async def generate_wa_message(
    jid: str,
    content: AnyMessageContent,
    options: dict[str, Any],
) -> WAMessage:
    message_options = dict(options)
    logger = message_options.get("logger")
    if logger is not None and hasattr(logger, "child"):
        try:
            message_options["logger"] = logger.child({"msgId": message_options.get("messageId")})
        except Exception:
            message_options["logger"] = logger
    return generate_wa_message_from_content(
        jid,
        await generate_wa_message_content(content, {**message_options, "jid": jid}),
        message_options,
    )


def get_content_type(content: dict[str, Any] | None) -> str | None:
    if not content:
        return None
    for key in content.keys():
        if (key == "conversation" or "Message" in key) and key != "senderKeyDistributionMessage":
            return key
    return None


def normalize_message_content(content: WAMessageContent | None) -> WAMessageContent | None:
    if not content:
        return None
    current = content
    for _ in range(5):
        inner = (
            current.get("ephemeralMessage")
            or current.get("viewOnceMessage")
            or current.get("documentWithCaptionMessage")
            or current.get("viewOnceMessageV2")
            or current.get("viewOnceMessageV2Extension")
            or current.get("editedMessage")
            or current.get("associatedChildMessage")
            or current.get("groupStatusMessage")
            or current.get("groupStatusMessageV2")
        )
        if not inner or not isinstance(inner, dict):
            break
        msg = inner.get("message")
        if not isinstance(msg, dict):
            break
        current = msg
    return current


def _extract_from_template_message(msg: dict[str, Any]) -> dict[str, Any]:
    if msg.get("imageMessage"):
        return {"imageMessage": msg["imageMessage"]}
    if msg.get("documentMessage"):
        return {"documentMessage": msg["documentMessage"]}
    if msg.get("videoMessage"):
        return {"videoMessage": msg["videoMessage"]}
    if msg.get("locationMessage"):
        return {"locationMessage": msg["locationMessage"]}
    return {"conversation": msg.get("contentText") or msg.get("hydratedContentText") or ""}


def extract_message_content(content: WAMessageContent | None) -> WAMessageContent | None:
    normalized = normalize_message_content(content)
    if not normalized:
        return None
    if normalized.get("buttonsMessage"):
        return _extract_from_template_message(cast(dict[str, Any], normalized["buttonsMessage"]))
    template = cast(dict[str, Any], normalized.get("templateMessage") or {})
    if template.get("hydratedFourRowTemplate"):
        return _extract_from_template_message(cast(dict[str, Any], template["hydratedFourRowTemplate"]))
    if template.get("hydratedTemplate"):
        return _extract_from_template_message(cast(dict[str, Any], template["hydratedTemplate"]))
    if template.get("fourRowTemplate"):
        return _extract_from_template_message(cast(dict[str, Any], template["fourRowTemplate"]))
    return normalized


def get_device(message_id: str) -> Literal["ios", "web", "android", "desktop", "unknown"]:
    if re.match(r"^3A.{18}$", message_id):
        return "ios"
    if re.match(r"^3E.{20}$", message_id):
        return "web"
    if re.match(r"^(.{21}|.{32})$", message_id):
        return "android"
    if re.match(r"^(3F|.{18}$)", message_id):
        return "desktop"
    return "unknown"


def update_message_with_receipt(msg: dict[str, Any] | WAMessage, receipt: MessageUserReceipt) -> None:
    target = msg.model_dump(by_alias=True, exclude_none=False) if isinstance(msg, WAMessage) else msg
    receipts = cast(list[dict[str, Any]], target.setdefault("userReceipt", []))
    existing = next((item for item in receipts if item.get("userJid") == receipt.get("userJid")), None)
    if existing:
        existing.update(receipt)
    else:
        receipts.append(dict(receipt))
    if isinstance(msg, WAMessage):
        msg.user_receipt = receipts


def update_message_with_reaction(msg: dict[str, Any] | WAMessage, reaction: dict[str, Any]) -> None:
    target = msg.model_dump(by_alias=True, exclude_none=False) if isinstance(msg, WAMessage) else msg
    author_id = get_key_author(reaction.get("key"))
    reactions = [item for item in cast(list[dict[str, Any]], target.get("reactions", [])) if get_key_author(item.get("key")) != author_id]
    merged = dict(reaction)
    merged["text"] = merged.get("text") or ""
    reactions.append(merged)
    target["reactions"] = reactions
    if isinstance(msg, WAMessage):
        msg.reactions = reactions


def update_message_with_poll_update(msg: dict[str, Any] | WAMessage, update: dict[str, Any]) -> None:
    target = msg.model_dump(by_alias=True, exclude_none=False) if isinstance(msg, WAMessage) else msg
    author_id = get_key_author(update.get("pollUpdateMessageKey"))
    updates = [item for item in cast(list[dict[str, Any]], target.get("pollUpdates", [])) if get_key_author(item.get("pollUpdateMessageKey")) != author_id]
    vote = cast(dict[str, Any], update.get("vote") or {})
    if vote.get("selectedOptions"):
        updates.append(update)
    target["pollUpdates"] = updates
    if isinstance(msg, WAMessage):
        msg.poll_updates = updates


def update_message_with_event_response(msg: dict[str, Any] | WAMessage, update: dict[str, Any]) -> None:
    target = msg.model_dump(by_alias=True, exclude_none=False) if isinstance(msg, WAMessage) else msg
    author_id = get_key_author(update.get("eventResponseMessageKey"))
    responses = [
        item
        for item in cast(list[dict[str, Any]], target.get("eventResponses", []))
        if get_key_author(item.get("eventResponseMessageKey")) != author_id
    ]
    responses.append(update)
    target["eventResponses"] = responses
    if isinstance(msg, WAMessage):
        msg.event_responses = responses


def get_aggregate_votes_in_poll_message(
    msg: dict[str, Any] | WAMessage,
    me_id: str | None = None,
) -> list[dict[str, Any]]:
    target = msg.model_dump(by_alias=True, exclude_none=False) if isinstance(msg, WAMessage) else msg
    message = cast(dict[str, Any], target.get("message") or {})
    poll_updates = cast(list[dict[str, Any]], target.get("pollUpdates", []) or [])
    opts = (
        cast(dict[str, Any], message.get("pollCreationMessage") or {}).get("options")
        or cast(dict[str, Any], message.get("pollCreationMessageV2") or {}).get("options")
        or cast(dict[str, Any], message.get("pollCreationMessageV3") or {}).get("options")
        or []
    )
    vote_hash_map: dict[str, dict[str, Any]] = {}
    for opt in opts:
        option_name = cast(str, opt.get("optionName") or "")
        hash_key = hashlib.sha256(option_name.encode("utf-8")).hexdigest()
        vote_hash_map[hash_key] = {"name": option_name, "voters": []}

    for update in poll_updates:
        vote = cast(dict[str, Any], update.get("vote") or {})
        for option in vote.get("selectedOptions", []) or []:
            hash_key = option.hex() if isinstance(option, (bytes, bytearray)) else str(option)
            data = vote_hash_map.setdefault(hash_key, {"name": "Unknown", "voters": []})
            data["voters"].append(get_key_author(update.get("pollUpdateMessageKey"), me_id or "me"))
    return list(vote_hash_map.values())


def get_aggregate_responses_in_event_message(
    msg: dict[str, Any] | WAMessage,
    me_id: str | None = None,
) -> list[dict[str, Any]]:
    target = msg.model_dump(by_alias=True, exclude_none=False) if isinstance(msg, WAMessage) else msg
    event_responses = cast(list[dict[str, Any]], target.get("eventResponses", []) or [])
    response_types = ["GOING", "NOT_GOING", "MAYBE"]
    response_map: dict[str, dict[str, Any]] = {kind: {"response": kind, "responders": []} for kind in response_types}
    for update in event_responses:
        response_type = cast(dict[str, Any], update.get("response") or {}).get("eventResponse") or "UNKNOWN"
        if response_type in response_map:
            response_map[response_type]["responders"].append(get_key_author(update.get("eventResponseMessageKey"), me_id or "me"))
    return list(response_map.values())


def aggregate_message_keys_not_from_me(keys: list[WAMessageKey | dict[str, Any]]) -> list[dict[str, Any]]:
    key_map: dict[str, dict[str, Any]] = {}
    for key in keys:
        item = key.model_dump(by_alias=True, exclude_none=True) if isinstance(key, WAMessageKey) else key
        if item.get("fromMe"):
            continue
        remote_jid = item.get("remoteJid")
        participant = item.get("participant")
        uq_key = f"{remote_jid}:{participant or ''}"
        if uq_key not in key_map:
            key_map[uq_key] = {"jid": remote_jid, "participant": participant, "messageIds": []}
        key_map[uq_key]["messageIds"].append(item.get("id"))
    return list(key_map.values())


def _extract_error_status_code(error: Exception) -> int | None:
    for attr in ("status", "statusCode", "status_code"):
        value = getattr(error, attr, None)
        if isinstance(value, int):
            return value
    output = getattr(error, "output", None)
    if isinstance(output, dict):
        value = output.get("statusCode")
        if isinstance(value, int):
            return value
    message = str(error)
    match = re.search(r"\bstatus\s+(\d{3})\b", message)
    if match:
        return int(match.group(1))
    return None


async def download_media_message(
    message: WAMessage | dict[str, Any],
    output_type: Literal["buffer", "stream"],
    options: dict[str, Any],
    ctx: dict[str, Any] | None = None,
) -> bytes | AsyncIterator[bytes]:
    msg_data = message.model_dump(by_alias=True, exclude_none=False) if isinstance(message, WAMessage) else dict(message)

    async def _download(msg_obj: dict[str, Any]) -> bytes | AsyncIterator[bytes]:
        m_content = extract_message_content(cast(dict[str, Any], msg_obj.get("message")))
        if not m_content:
            raise ValueError("No message present")

        content_type = get_content_type(m_content)
        if not content_type:
            raise ValueError("No content type in message")

        media = m_content.get(content_type)
        if not isinstance(media, dict) or (media.get("url") is None and media.get("thumbnailDirectPath") is None):
            raise ValueError(f'"{content_type}" message is not a media message')

        media_type = content_type.replace("Message", "")
        download: dict[str, Any]
        if media.get("thumbnailDirectPath") is not None and media.get("url") is None:
            download = {
                "directPath": media.get("thumbnailDirectPath"),
                "mediaKey": media.get("mediaKey"),
            }
            media_type = "thumbnail-link"
        else:
            download = media

        stream = await download_content_from_message(download, media_type, options)
        if output_type == "buffer":
            chunks: list[bytes] = []
            async for chunk in stream:
                chunks.append(bytes(chunk))
            return b"".join(chunks)
        return stream

    try:
        return await _download(msg_data)
    except Exception as error:
        if ctx:
            status_code = _extract_error_status_code(cast(Exception, error))
            reupload_request = ctx.get("reuploadRequest")
            if status_code in _REUPLOAD_REQUIRED_STATUS and callable(reupload_request):
                logger = ctx.get("logger")
                if logger and hasattr(logger, "info") and callable(logger.info):
                    logger.info({"key": msg_data.get("key")}, "sending reupload media request...")
                updated = reupload_request(message)
                if asyncio.iscoroutine(updated):
                    updated = await updated
                updated_data = (
                    updated.model_dump(by_alias=True, exclude_none=False)
                    if isinstance(updated, WAMessage)
                    else (dict(updated) if isinstance(updated, dict) else msg_data)
                )
                return await _download(updated_data)
        raise


def assert_media_content(content: dict[str, Any] | None) -> dict[str, Any]:
    extracted = extract_message_content(content)
    media = None
    if extracted:
        media = (
            extracted.get("documentMessage")
            or extracted.get("imageMessage")
            or extracted.get("videoMessage")
            or extracted.get("audioMessage")
            or extracted.get("stickerMessage")
        )
    if not media:
        raise ValueError("given message is not a media message")
    return cast(dict[str, Any], media)


# camelCase aliases for parity
extractUrlFromText = extract_url_from_text
generateLinkPreviewIfRequired = generate_link_preview_if_required
prepareWAMessageMedia = prepare_wa_message_media
prepareDisappearingMessageSettingContent = prepare_disappearing_message_setting_content
generateForwardMessageContent = generate_forward_message_content
hasNonNullishProperty = has_non_nullish_property
generateWAMessageContent = generate_wa_message_content
generateWAMessageFromContent = generate_wa_message_from_content
generateWAMessage = generate_wa_message
getContentType = get_content_type
normalizeMessageContent = normalize_message_content
extractMessageContent = extract_message_content
updateMessageWithReceipt = update_message_with_receipt
updateMessageWithReaction = update_message_with_reaction
updateMessageWithPollUpdate = update_message_with_poll_update
updateMessageWithEventResponse = update_message_with_event_response
getAggregateVotesInPollMessage = get_aggregate_votes_in_poll_message
getAggregateResponsesInEventMessage = get_aggregate_responses_in_event_message
aggregateMessageKeysNotFromMe = aggregate_message_keys_not_from_me
assertMediaContent = assert_media_content
downloadMediaMessage = download_media_message


__all__ = [
    "extract_url_from_text",
    "generate_link_preview_if_required",
    "prepare_wa_message_media",
    "prepare_disappearing_message_setting_content",
    "generate_forward_message_content",
    "has_non_nullish_property",
    "generate_wa_message_content",
    "generate_wa_message_from_content",
    "generate_wa_message",
    "get_content_type",
    "normalize_message_content",
    "extract_message_content",
    "get_device",
    "update_message_with_receipt",
    "update_message_with_reaction",
    "update_message_with_poll_update",
    "update_message_with_event_response",
    "get_aggregate_votes_in_poll_message",
    "get_aggregate_responses_in_event_message",
    "aggregate_message_keys_not_from_me",
    "assert_media_content",
    "download_media_message",
]
