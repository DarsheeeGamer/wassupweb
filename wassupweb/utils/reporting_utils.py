from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from typing import Any

from ..wabinary.types import BinaryNode
from .crypto import hkdf


@dataclass(slots=True)
class ReportingField:
    f: int
    m: bool = False
    s: list["ReportingField"] | None = None


CompiledReportingField = dict[str, Any]


_REPORTING_FIELDS: list[ReportingField] = [
    ReportingField(f=1),
    ReportingField(
        f=3,
        s=[
            ReportingField(f=2),
            ReportingField(f=3),
            ReportingField(f=8),
            ReportingField(f=11),
            ReportingField(f=17, s=[ReportingField(f=21), ReportingField(f=22)]),
            ReportingField(f=25),
        ],
    ),
    ReportingField(f=4, s=[ReportingField(f=1), ReportingField(f=16), ReportingField(f=17, s=[ReportingField(f=21), ReportingField(f=22)])]),
    ReportingField(f=5, s=[ReportingField(f=3), ReportingField(f=4), ReportingField(f=5), ReportingField(f=16), ReportingField(f=17, s=[ReportingField(f=21), ReportingField(f=22)])]),
    ReportingField(f=6, s=[ReportingField(f=1), ReportingField(f=17, s=[ReportingField(f=21), ReportingField(f=22)]), ReportingField(f=30)]),
    ReportingField(f=7, s=[ReportingField(f=2), ReportingField(f=7), ReportingField(f=10), ReportingField(f=17, s=[ReportingField(f=21), ReportingField(f=22)]), ReportingField(f=20)]),
    ReportingField(f=8, s=[ReportingField(f=2), ReportingField(f=7), ReportingField(f=9), ReportingField(f=17, s=[ReportingField(f=21), ReportingField(f=22)]), ReportingField(f=21)]),
    ReportingField(f=9, s=[ReportingField(f=2), ReportingField(f=6), ReportingField(f=7), ReportingField(f=13), ReportingField(f=17, s=[ReportingField(f=21), ReportingField(f=22)]), ReportingField(f=20)]),
    ReportingField(f=12, s=[ReportingField(f=1), ReportingField(f=2), ReportingField(f=14, m=True), ReportingField(f=15)]),
    ReportingField(f=18, s=[ReportingField(f=6), ReportingField(f=16), ReportingField(f=17, s=[ReportingField(f=21), ReportingField(f=22)])]),
    ReportingField(f=26, s=[ReportingField(f=4), ReportingField(f=5), ReportingField(f=8), ReportingField(f=13), ReportingField(f=17, s=[ReportingField(f=21), ReportingField(f=22)])]),
    ReportingField(f=28, s=[ReportingField(f=1), ReportingField(f=2), ReportingField(f=4), ReportingField(f=5), ReportingField(f=6), ReportingField(f=7, s=[ReportingField(f=21), ReportingField(f=22)])]),
    ReportingField(f=37, s=[ReportingField(f=1, m=True)]),
    ReportingField(f=49, s=[ReportingField(f=2), ReportingField(f=3, s=[ReportingField(f=1), ReportingField(f=2)]), ReportingField(f=5, s=[ReportingField(f=21), ReportingField(f=22)]), ReportingField(f=8, s=[ReportingField(f=1), ReportingField(f=2)])]),
    ReportingField(f=53, s=[ReportingField(f=1, m=True)]),
    ReportingField(f=55, s=[ReportingField(f=1, m=True)]),
    ReportingField(f=58, s=[ReportingField(f=1, m=True)]),
    ReportingField(f=59, s=[ReportingField(f=1, m=True)]),
    ReportingField(f=60, s=[ReportingField(f=2), ReportingField(f=3, s=[ReportingField(f=1), ReportingField(f=2)]), ReportingField(f=5, s=[ReportingField(f=21), ReportingField(f=22)]), ReportingField(f=8, s=[ReportingField(f=1), ReportingField(f=2)])]),
    ReportingField(f=64, s=[ReportingField(f=2), ReportingField(f=3, s=[ReportingField(f=1), ReportingField(f=2)]), ReportingField(f=5, s=[ReportingField(f=21), ReportingField(f=22)]), ReportingField(f=8, s=[ReportingField(f=1), ReportingField(f=2)])]),
    ReportingField(f=66, s=[ReportingField(f=2), ReportingField(f=6), ReportingField(f=7), ReportingField(f=13), ReportingField(f=17, s=[ReportingField(f=21), ReportingField(f=22)]), ReportingField(f=20)]),
    ReportingField(f=74, s=[ReportingField(f=1, m=True)]),
    ReportingField(f=87, s=[ReportingField(f=1, m=True)]),
    ReportingField(f=88, s=[ReportingField(f=1), ReportingField(f=2, s=[ReportingField(f=1)]), ReportingField(f=3, s=[ReportingField(f=21), ReportingField(f=22)])]),
    ReportingField(f=92, s=[ReportingField(f=1, m=True)]),
    ReportingField(f=93, s=[ReportingField(f=1, m=True)]),
    ReportingField(f=94, s=[ReportingField(f=1, m=True)]),
]


def _compile_reporting_fields(fields: list[ReportingField]) -> dict[int, CompiledReportingField]:
    mapping: dict[int, CompiledReportingField] = {}
    for field in fields:
        mapping[field.f] = {
            "m": field.m,
            "children": _compile_reporting_fields(field.s or []),
        }
    return mapping


_COMPILED_REPORTING_FIELDS = _compile_reporting_fields(_REPORTING_FIELDS)
_EMPTY_MAP: dict[int, CompiledReportingField] = {}

ENC_SECRET_REPORT_TOKEN = "Report Token"

WIRE_VARINT = 0
WIRE_FIXED64 = 1
WIRE_BYTES = 2
WIRE_FIXED32 = 5


def should_include_reporting_token(message: dict[str, Any]) -> bool:
    return not message.get("reactionMessage") and not message.get("encReactionMessage") and not message.get("encEventResponseMessage") and not message.get("pollUpdateMessage")


def _generate_msg_secret_key(
    modification_type: str,
    orig_msg_id: str,
    orig_msg_sender: str,
    modification_sender: str,
    orig_msg_secret: bytes,
) -> bytes:
    use_case_secret = (
        orig_msg_id.encode("utf-8")
        + orig_msg_sender.encode("utf-8")
        + modification_sender.encode("utf-8")
        + modification_type.encode("utf-8")
    )
    return hkdf(orig_msg_secret, 32, info=use_case_secret.decode("latin1"))


def _decode_varint(buffer: bytes, offset: int) -> tuple[int, int, bool]:
    value = 0
    read = 0
    shift = 0
    while offset + read < len(buffer):
        current = buffer[offset + read]
        value |= (current & 0x7F) << shift
        read += 1
        if (current & 0x80) == 0:
            return value, read, True
        shift += 7
        if shift > 35:
            return 0, 0, False
    return 0, 0, False


def _encode_varint(value: int) -> bytes:
    parts: list[int] = []
    remaining = value & 0xFFFFFFFF
    while remaining > 0x7F:
        parts.append((remaining & 0x7F) | 0x80)
        remaining >>= 7
    parts.append(remaining)
    return bytes(parts)


def _extract_reporting_token_content(data: bytes, cfg: dict[int, CompiledReportingField]) -> bytes | None:
    out: list[tuple[int, bytes]] = []
    idx = 0
    while idx < len(data):
        tag_value, tag_bytes, ok = _decode_varint(data, idx)
        if not ok:
            return None
        field_num = tag_value >> 3
        wire_type = tag_value & 0x7
        field_start = idx
        idx += tag_bytes
        field_cfg = cfg.get(field_num)

        def _push_slice(end: int) -> bool:
            nonlocal idx
            if end > len(data):
                return False
            out.append((field_num, data[field_start:end]))
            idx = end
            return True

        def _skip(end: int) -> bool:
            nonlocal idx
            if end > len(data):
                return False
            idx = end
            return True

        if wire_type == WIRE_VARINT:
            _, varint_bytes, ok2 = _decode_varint(data, idx)
            if not ok2:
                return None
            end = idx + varint_bytes
            if not field_cfg:
                if not _skip(end):
                    return None
                continue
            if not _push_slice(end):
                return None
            continue

        if wire_type == WIRE_FIXED64:
            end = idx + 8
            if not field_cfg:
                if not _skip(end):
                    return None
                continue
            if not _push_slice(end):
                return None
            continue

        if wire_type == WIRE_FIXED32:
            end = idx + 4
            if not field_cfg:
                if not _skip(end):
                    return None
                continue
            if not _push_slice(end):
                return None
            continue

        if wire_type == WIRE_BYTES:
            length, length_bytes, ok2 = _decode_varint(data, idx)
            if not ok2:
                return None
            val_start = idx + length_bytes
            val_end = val_start + length
            if val_end > len(data):
                return None
            if not field_cfg:
                idx = val_end
                continue

            if field_cfg.get("m") or field_cfg.get("children"):
                sub = _extract_reporting_token_content(data[val_start:val_end], field_cfg.get("children") or _EMPTY_MAP)
                if sub is None:
                    return None
                if len(sub) > 0:
                    new_tag = _encode_varint(tag_value)
                    new_len = _encode_varint(len(sub))
                    out.append((field_num, new_tag + new_len + sub))
                idx = val_end
                continue

            out.append((field_num, data[field_start:val_end]))
            idx = val_end
            continue

        return None

    if not out:
        return b""
    out.sort(key=lambda item: item[0])
    return b"".join(item[1] for item in out)


async def get_message_reporting_token(
    msg_protobuf: bytes,
    message: dict[str, Any],
    key: dict[str, Any],
) -> BinaryNode | None:
    msg_secret = ((message.get("messageContextInfo") or {}).get("messageSecret")) if isinstance(message, dict) else None
    if not msg_secret or not key.get("id"):
        return None

    msg_id = key["id"]
    from_jid = key.get("remoteJid") if key.get("fromMe") else (key.get("participant") or key.get("remoteJid"))
    to_jid = (key.get("participant") or key.get("remoteJid")) if key.get("fromMe") else key.get("remoteJid")
    if not from_jid or not to_jid:
        return None

    reporting_secret = _generate_msg_secret_key(
        ENC_SECRET_REPORT_TOKEN,
        msg_id,
        from_jid,
        to_jid,
        bytes(msg_secret),
    )

    content = _extract_reporting_token_content(msg_protobuf, _COMPILED_REPORTING_FIELDS)
    if not content:
        return None

    reporting_token = hmac.new(reporting_secret, content, digestmod=hashlib.sha256).digest()[:16]
    return BinaryNode(
        tag="reporting",
        attrs={},
        content=[
            BinaryNode(
                tag="reporting_token",
                attrs={"v": "2"},
                content=reporting_token,
            )
        ],
    )


# camelCase aliases
shouldIncludeReportingToken = should_include_reporting_token
getMessageReportingToken = get_message_reporting_token
