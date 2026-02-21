from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Awaitable, Callable, TypeVar

from ..types.common import DisconnectReason
from ..types.message import WAMessageKey, WAMessageStatus
from ..wabinary import get_all_binary_node_children, jid_decode
from ..wabinary.types import BinaryNode
from .crypto import sha256

T = TypeVar("T")
DEFAULT_BAILEYS_VERSION = (2, 3000, 1033105955)

_UNEXPECTED_SERVER_CODE_TEXT = "Unexpected server response: "
_CROCKFORD_CHARACTERS = "123456789ABCDEFGHJKLMNPQRSTVWXYZ"


class BufferJSON:
    @staticmethod
    def replacer(_key: Any, value: Any) -> Any:
        if isinstance(value, (bytes, bytearray)):
            return {"type": "Buffer", "data": base64.b64encode(bytes(value)).decode("ascii")}
        if isinstance(value, dict):
            return {k: BufferJSON.replacer(k, v) for k, v in value.items()}
        if isinstance(value, list):
            return [BufferJSON.replacer(None, item) for item in value]
        return value

    @staticmethod
    def reviver(_key: Any, value: Any) -> Any:
        if isinstance(value, dict) and value.get("type") == "Buffer" and isinstance(value.get("data"), str):
            return base64.b64decode(value["data"])
        if isinstance(value, dict):
            if value and all(str(k).isdigit() for k in value.keys()):
                ordered: list[Any] | None = None
                try:
                    ordered = [value[str(index)] for index in sorted(int(k) for k in value.keys())]
                except Exception:
                    ordered = None
                if ordered is not None and all(isinstance(item, int) and 0 <= item <= 255 for item in ordered):
                    return bytes(ordered)
            return {k: BufferJSON.reviver(k, v) for k, v in value.items()}
        if isinstance(value, list):
            return [BufferJSON.reviver(None, item) for item in value]
        return value


def get_key_author(key: WAMessageKey | dict[str, Any] | None, me_id: str = "me") -> str:
    if key is None:
        return ""
    if isinstance(key, WAMessageKey):
        data = key.model_dump(by_alias=True, exclude_none=True)
    else:
        data = key
    if data.get("fromMe"):
        return me_id
    return (
        data.get("participantAlt")
        or data.get("remoteJidAlt")
        or data.get("participant")
        or data.get("remoteJid")
        or ""
    )


def is_string_null_or_empty(value: str | None) -> bool:
    return value is None or value == ""


def write_random_pad_max16(msg: bytes | bytearray) -> bytes:
    pad_byte = os.urandom(1)[0]
    pad_len = (pad_byte & 0x0F) + 1
    return bytes(msg) + bytes([pad_len] * pad_len)


def unpad_random_max16(data: bytes | bytearray) -> bytes:
    buff = bytes(data)
    if not buff:
        raise ValueError("unpad_random_max16 received empty bytes")
    pad_len = buff[-1]
    if pad_len > len(buff):
        raise ValueError(f"invalid pad length {pad_len} for {len(buff)} bytes")
    return buff[:-pad_len]


def generate_participant_hash_v2(participants: list[str]) -> str:
    sorted_participants = sorted(participants)
    hash_b64 = base64.b64encode(sha256("".join(sorted_participants).encode("utf-8"))).decode("ascii")
    return f"2:{hash_b64[:6]}"


def encode_wa_message(message: Any) -> bytes:
    payload = json.dumps(message, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return write_random_pad_max16(payload)


def generate_registration_id() -> int:
    return int.from_bytes(os.urandom(2), "big") & 0x3FFF


def encode_big_endian(value: int, width: int = 4) -> bytes:
    return int(value).to_bytes(width, "big", signed=False)


def to_number(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(float(value))
        except ValueError:
            return 0
    if hasattr(value, "toNumber") and callable(value.toNumber):  # pragma: no cover - JS interop shape
        return int(value.toNumber())
    if hasattr(value, "low"):
        return int(value.low)
    return 0


def unix_timestamp_seconds(date: datetime | None = None) -> int:
    when = date or datetime.now(UTC)
    return int(when.timestamp())


@dataclass
class DebouncedTimeout:
    interval_ms: int = 1000
    task: Callable[[], None] | None = None
    _handle: asyncio.TimerHandle | None = None

    def start(self, new_interval_ms: int | None = None, new_task: Callable[[], None] | None = None) -> None:
        if new_task is not None:
            self.task = new_task
        if new_interval_ms is not None:
            self.interval_ms = new_interval_ms
        if self._handle:
            self._handle.cancel()
        loop = asyncio.get_running_loop()
        self._handle = loop.call_later(self.interval_ms / 1000.0, self._run)

    def _run(self) -> None:
        self._handle = None
        if self.task:
            self.task()

    def cancel(self) -> None:
        if self._handle:
            self._handle.cancel()
            self._handle = None

    def set_task(self, new_task: Callable[[], None]) -> None:
        self.task = new_task

    def set_interval(self, new_interval: int) -> None:
        self.interval_ms = new_interval


def debounced_timeout(interval_ms: int = 1000, task: Callable[[], None] | None = None) -> DebouncedTimeout:
    return DebouncedTimeout(interval_ms=interval_ms, task=task)


def delay(ms: int) -> Awaitable[None]:
    return delay_cancellable(ms).delay


@dataclass
class CancellableDelay:
    delay: Awaitable[None]
    cancel: Callable[[], None]


def delay_cancellable(ms: int) -> CancellableDelay:
    loop = asyncio.get_running_loop()
    future: asyncio.Future[None] = loop.create_future()

    def _resolve() -> None:
        if not future.done():
            future.set_result(None)

    handle = loop.call_later(ms / 1000.0, _resolve)

    def _cancel() -> None:
        handle.cancel()
        if not future.done():
            future.set_exception(RuntimeError("Cancelled"))

    return CancellableDelay(delay=future, cancel=_cancel)


async def promise_timeout(
    ms: int | None,
    promise: Callable[[Callable[[T], None], Callable[[Exception], None]], None],
) -> T:
    if not ms:
        loop = asyncio.get_running_loop()
        future: asyncio.Future[T] = loop.create_future()
        promise(future.set_result, future.set_exception)
        return await future

    loop = asyncio.get_running_loop()
    future: asyncio.Future[T] = loop.create_future()

    def _timeout() -> None:
        if not future.done():
            future.set_exception(RuntimeError(f"Timed Out ({DisconnectReason.timedOut})"))

    timeout_handle = loop.call_later(ms / 1000.0, _timeout)
    try:
        promise(future.set_result, future.set_exception)
        return await future
    finally:
        timeout_handle.cancel()


def generate_message_id_v2(user_id: str | None = None) -> str:
    data = bytearray(8 + 20 + 16)
    now_sec = int(time.time())
    data[0:8] = now_sec.to_bytes(8, "big", signed=False)

    if user_id:
        jid = jid_decode(user_id)
        user = jid.get("user") if jid else None
        if user:
            encoded = f"{user}@c.us".encode("utf-8")
            data[8 : 8 + min(len(encoded), 20)] = encoded[:20]

    random_tail = os.urandom(16)
    data[28:44] = random_tail
    digest = hashlib.sha256(bytes(data)).hexdigest().upper()
    return "3EB0" + digest[:18]


def generate_message_id() -> str:
    return "3EB0" + os.urandom(18).hex().upper()


def bind_wait_for_event(ev: Any, event: str) -> Callable[[Callable[[Any], Awaitable[bool | None]], int | None], Awaitable[None]]:
    async def _wait(check: Callable[[Any], Awaitable[bool | None]], timeout_ms: int | None = None) -> None:
        loop = asyncio.get_running_loop()
        future: asyncio.Future[None] = loop.create_future()

        async def _listener(update: Any) -> None:
            if await check(update):
                if not future.done():
                    future.set_result(None)

        async def _close_listener(state: dict[str, Any]) -> None:
            if state.get("connection") == "close" and not future.done():
                future.set_exception(RuntimeError("Connection Closed"))

        ev.on("connection.update", _close_listener)
        ev.on(event, _listener)
        try:
            if timeout_ms:
                await asyncio.wait_for(future, timeout_ms / 1000.0)
            else:
                await future
        finally:
            ev.off(event, _listener)
            ev.off("connection.update", _close_listener)

    return _wait


def bind_wait_for_connection_update(ev: Any) -> Callable[[Callable[[Any], Awaitable[bool | None]], int | None], Awaitable[None]]:
    return bind_wait_for_event(ev, "connection.update")


def fetch_latest_baileys_version(timeout: float = 10.0) -> dict[str, Any]:
    url = "https://raw.githubusercontent.com/WhiskeySockets/Baileys/master/src/Defaults/index.ts"
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - trusted static URL
            text = resp.read().decode("utf-8", errors="replace")
        match = re.search(r"const version = \[(\d+),\s*(\d+),\s*(\d+)\]", text)
        if not match:
            raise ValueError("Could not parse version from Defaults/index.ts")
        version = (int(match.group(1)), int(match.group(2)), int(match.group(3)))
        return {"version": version, "isLatest": True}
    except Exception as error:  # pragma: no cover - network dependent
        return {"version": DEFAULT_BAILEYS_VERSION, "isLatest": False, "error": error}


def fetch_latest_wa_web_version(timeout: float = 10.0) -> dict[str, Any]:
    url = "https://web.whatsapp.com/sw.js"
    default_headers = {
        "sec-fetch-site": "none",
        "user-agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        ),
    }
    try:
        req = urllib.request.Request(url, headers=default_headers, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - WA public endpoint
            data = resp.read().decode("utf-8", errors="replace")
        match = re.search(r'\\?"client_revision\\?":\s*(\d+)', data)
        if not match:
            return {
                "version": DEFAULT_BAILEYS_VERSION,
                "isLatest": False,
                "error": {"message": "Could not find client revision in fetched content"},
            }
        return {"version": (2, 3000, int(match.group(1))), "isLatest": True}
    except Exception as error:  # pragma: no cover - network dependent
        return {"version": DEFAULT_BAILEYS_VERSION, "isLatest": False, "error": error}


def generate_md_tag_prefix() -> str:
    four = os.urandom(4)
    first = int.from_bytes(four[:2], "big")
    second = int.from_bytes(four[2:], "big")
    return f"{first}.{second}-"


_STATUS_MAP: dict[str, int] = {
    "sender": int(WAMessageStatus.SERVER_ACK),
    "played": int(WAMessageStatus.PLAYED),
    "read": int(WAMessageStatus.READ),
    "read-self": int(WAMessageStatus.READ),
}


def get_status_from_receipt_type(receipt_type: str | None) -> int:
    if receipt_type is None:
        return int(WAMessageStatus.DELIVERY_ACK)
    return _STATUS_MAP.get(receipt_type, int(WAMessageStatus.DELIVERY_ACK))


_CODE_MAP = {"conflict": int(DisconnectReason.connectionReplaced)}


def get_error_code_from_stream_error(node: BinaryNode) -> dict[str, Any]:
    children = get_all_binary_node_children(node)
    reason = children[0].tag if children else "unknown"
    status_code = int(node.attrs.get("code") or _CODE_MAP.get(reason, int(DisconnectReason.badSession)))
    if status_code == int(DisconnectReason.restartRequired):
        reason = "restart required"
    return {"reason": reason, "statusCode": status_code}


def get_code_from_ws_error(error: Exception) -> int:
    message = str(error or "")
    status_code = 500
    if _UNEXPECTED_SERVER_CODE_TEXT in message:
        raw = message[message.find(_UNEXPECTED_SERVER_CODE_TEXT) + len(_UNEXPECTED_SERVER_CODE_TEXT) :]
        try:
            code = int(raw.strip())
        except ValueError:
            code = 0
        if code >= 400:
            status_code = code
    elif "timed out" in message.lower() or getattr(error, "code", "").startswith("E"):
        status_code = 408
    return status_code


def is_wa_business_platform(platform: str) -> bool:
    return platform in {"smbi", "smba"}


def trim_undefined(obj: dict[str, Any]) -> dict[str, Any]:
    keys = [key for key, value in obj.items() if value is None]
    for key in keys:
        del obj[key]
    return obj


def bytes_to_crockford(buffer: bytes | bytearray) -> str:
    value = 0
    bit_count = 0
    out: list[str] = []
    for element in bytes(buffer):
        value = (value << 8) | (element & 0xFF)
        bit_count += 8
        while bit_count >= 5:
            out.append(_CROCKFORD_CHARACTERS[(value >> (bit_count - 5)) & 31])
            bit_count -= 5
    if bit_count > 0:
        out.append(_CROCKFORD_CHARACTERS[(value << (5 - bit_count)) & 31])
    return "".join(out)


def encode_newsletter_message(message: Any) -> bytes:
    return json.dumps(message, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


# camelCase aliases for closer Baileys parity
getKeyAuthor = get_key_author
isStringNullOrEmpty = is_string_null_or_empty
writeRandomPadMax16 = write_random_pad_max16
unpadRandomMax16 = unpad_random_max16
generateParticipantHashV2 = generate_participant_hash_v2
encodeWAMessage = encode_wa_message
generateRegistrationId = generate_registration_id
encodeBigEndian = encode_big_endian
toNumber = to_number
unixTimestampSeconds = unix_timestamp_seconds
debouncedTimeout = debounced_timeout
delayCancellable = delay_cancellable
promiseTimeout = promise_timeout
generateMessageIDV2 = generate_message_id_v2
generateMessageID = generate_message_id
bindWaitForEvent = bind_wait_for_event
bindWaitForConnectionUpdate = bind_wait_for_connection_update
fetchLatestBaileysVersion = fetch_latest_baileys_version
fetchLatestWaWebVersion = fetch_latest_wa_web_version
generateMdTagPrefix = generate_md_tag_prefix
getStatusFromReceiptType = get_status_from_receipt_type
getErrorCodeFromStreamError = get_error_code_from_stream_error
getCodeFromWSError = get_code_from_ws_error
isWABusinessPlatform = is_wa_business_platform
bytesToCrockford = bytes_to_crockford
encodeNewsletterMessage = encode_newsletter_message


__all__ = [
    "BufferJSON",
    "DebouncedTimeout",
    "CancellableDelay",
    "get_key_author",
    "is_string_null_or_empty",
    "write_random_pad_max16",
    "unpad_random_max16",
    "generate_participant_hash_v2",
    "encode_wa_message",
    "generate_registration_id",
    "encode_big_endian",
    "to_number",
    "unix_timestamp_seconds",
    "debounced_timeout",
    "delay",
    "delay_cancellable",
    "promise_timeout",
    "generate_message_id_v2",
    "generate_message_id",
    "bind_wait_for_event",
    "bind_wait_for_connection_update",
    "fetch_latest_baileys_version",
    "fetch_latest_wa_web_version",
    "generate_md_tag_prefix",
    "get_status_from_receipt_type",
    "get_error_code_from_stream_error",
    "get_code_from_ws_error",
    "is_wa_business_platform",
    "trim_undefined",
    "bytes_to_crockford",
    "encode_newsletter_message",
]
