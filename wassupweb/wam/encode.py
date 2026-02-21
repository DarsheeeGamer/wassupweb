from __future__ import annotations

import struct
from typing import Any

from .binary_info import BinaryInfo
from .constants import (
    FLAG_BYTE,
    FLAG_EVENT,
    FLAG_EXTENDED,
    FLAG_FIELD,
    FLAG_GLOBAL,
    WEB_EVENTS,
    WEB_GLOBALS,
)

Value = int | float | str | None


def _header_bit_length(key: int) -> int:
    return 2 if key < 256 else 3


def encode_wam(binary_info: BinaryInfo) -> bytes:
    binary_info.buffer = []
    _encode_wam_header(binary_info)
    _encode_events(binary_info)
    return b"".join(binary_info.buffer)


def _encode_wam_header(binary_info: BinaryInfo) -> None:
    header = bytearray(8)
    header[0:3] = b"WAM"
    header[3] = binary_info.protocol_version
    header[4] = 1
    header[5:7] = binary_info.sequence.to_bytes(2, "big")
    header[7] = 0
    binary_info.buffer.append(bytes(header))


def _encode_global_attributes(binary_info: BinaryInfo, globals_data: dict[str, Value]) -> None:
    for key, raw_value in globals_data.items():
        match = next((item for item in WEB_GLOBALS if item.get("name") == key), None)
        if not match:
            continue
        value: Value = 1 if raw_value is True else 0 if raw_value is False else raw_value
        binary_info.buffer.append(_serialize_data(int(match["id"]), value, FLAG_GLOBAL))


def _encode_events(binary_info: BinaryInfo) -> None:
    for item in binary_info.events:
        if not item:
            continue
        name, payload = next(iter(item.items()))
        props = payload.get("props", {}) if isinstance(payload, dict) else {}
        globals_data = payload.get("globals", {}) if isinstance(payload, dict) else {}
        _encode_global_attributes(binary_info, globals_data)

        event = next((evt for evt in WEB_EVENTS if evt.get("name") == name), None)
        if not event:
            continue

        props_entries = list(props.items())
        extended = any(value is not None for _, value in props_entries)
        event_flag = FLAG_EVENT if extended else FLAG_EVENT | FLAG_EXTENDED
        weight = int(event.get("weight", 1))
        binary_info.buffer.append(_serialize_data(int(event["id"]), -weight, event_flag))

        event_props = event.get("props", {})
        for index, (key, raw_value) in enumerate(props_entries):
            meta = event_props.get(key)
            if not meta:
                continue
            field_id = int(meta[0])
            extended = index < len(props_entries) - 1
            value: Value = 1 if raw_value is True else 0 if raw_value is False else raw_value
            field_flag = FLAG_EVENT if extended else FLAG_FIELD | FLAG_EXTENDED
            binary_info.buffer.append(_serialize_data(field_id, value, field_flag))


def _serialize_data(key: int, value: Value, flag: int) -> bytes:
    header_size = _header_bit_length(key)
    if value is None:
        if flag == FLAG_GLOBAL:
            buff = bytearray(header_size)
            _serialize_header(buff, key, flag)
            return bytes(buff)
        raise ValueError("missing value for non-global WAM field")

    if isinstance(value, int) and not isinstance(value, bool):
        if value in (0, 1):
            buff = bytearray(header_size)
            _serialize_header(buff, key, flag | ((value + 1) << 4))
            return bytes(buff)
        if -128 <= value < 128:
            buff = bytearray(header_size + 1)
            offset = _serialize_header(buff, key, flag | (3 << 4))
            struct.pack_into("<b", buff, offset, value)
            return bytes(buff)
        if -32768 <= value < 32768:
            buff = bytearray(header_size + 2)
            offset = _serialize_header(buff, key, flag | (4 << 4))
            struct.pack_into("<h", buff, offset, value)
            return bytes(buff)
        if -2147483648 <= value < 2147483648:
            buff = bytearray(header_size + 4)
            offset = _serialize_header(buff, key, flag | (5 << 4))
            struct.pack_into("<i", buff, offset, value)
            return bytes(buff)
        buff = bytearray(header_size + 8)
        offset = _serialize_header(buff, key, flag | (7 << 4))
        struct.pack_into("<d", buff, offset, float(value))
        return bytes(buff)

    if isinstance(value, float):
        buff = bytearray(header_size + 8)
        offset = _serialize_header(buff, key, flag | (7 << 4))
        struct.pack_into("<d", buff, offset, value)
        return bytes(buff)

    if isinstance(value, str):
        utf8 = value.encode("utf-8")
        size = len(utf8)
        if size < 256:
            buff = bytearray(header_size + 1 + size)
            offset = _serialize_header(buff, key, flag | (8 << 4))
            buff[offset] = size
            offset += 1
        elif size < 65536:
            buff = bytearray(header_size + 2 + size)
            offset = _serialize_header(buff, key, flag | (9 << 4))
            struct.pack_into("<H", buff, offset, size)
            offset += 2
        else:
            buff = bytearray(header_size + 4 + size)
            offset = _serialize_header(buff, key, flag | (10 << 4))
            struct.pack_into("<I", buff, offset, size)
            offset += 4
        buff[offset : offset + size] = utf8
        return bytes(buff)

    raise TypeError(f"unsupported WAM value type: {type(value)!r}")


def _serialize_header(buffer: bytearray, key: int, flag: int) -> int:
    offset = 0
    if key < 256:
        buffer[offset] = flag
        offset += 1
        buffer[offset] = key
        offset += 1
    else:
        buffer[offset] = flag | FLAG_BYTE
        offset += 1
        struct.pack_into("<H", buffer, offset, key)
        offset += 2
    return offset
