from __future__ import annotations

from typing import Any

from . import constants
from .jid_utils import FullJid, jid_decode
from .types import BinaryNode


def encode_binary_node(
    node: BinaryNode,
    opts: dict[str, Any] | None = None,
    buffer: list[int] | None = None,
) -> bytes:
    opts = opts or {
        "TAGS": constants.TAGS,
        "TOKEN_MAP": constants.TOKEN_MAP,
    }
    work = [0] if buffer is None else buffer
    encoded = _encode_binary_node_inner(node, opts, work)
    return bytes(encoded)


def _encode_binary_node_inner(
    node: BinaryNode,
    opts: dict[str, Any],
    buffer: list[int],
) -> list[int]:
    tags = opts["TAGS"]
    token_map = opts["TOKEN_MAP"]
    tag = node.tag
    attrs = node.attrs or {}
    content = node.content

    def push_byte(value: int) -> None:
        buffer.append(value & 0xFF)

    def push_int(value: int, width: int, little_endian: bool = False) -> None:
        for i in range(width):
            shift = i if little_endian else width - 1 - i
            buffer.append((value >> (shift * 8)) & 0xFF)

    def push_bytes(values: bytes | list[int]) -> None:
        for value in values:
            buffer.append(value)

    def push_int16(value: int) -> None:
        push_bytes([(value >> 8) & 0xFF, value & 0xFF])

    def push_int20(value: int) -> None:
        push_bytes([(value >> 16) & 0x0F, (value >> 8) & 0xFF, value & 0xFF])

    def write_byte_length(length: int) -> None:
        if length >= 4_294_967_296:
            raise ValueError(f"string too large to encode: {length}")
        if length >= 1 << 20:
            push_byte(tags["BINARY_32"])
            push_int(length, 4)
        elif length >= 256:
            push_byte(tags["BINARY_20"])
            push_int20(length)
        else:
            push_byte(tags["BINARY_8"])
            push_byte(length)

    def write_string_raw(value: str) -> None:
        raw = value.encode("utf-8")
        write_byte_length(len(raw))
        push_bytes(raw)

    def write_jid(decoded: FullJid) -> None:
        domain_type = decoded.get("domainType")
        device = decoded.get("device")
        user = decoded.get("user", "")
        server = decoded.get("server", "")

        if device is not None:
            push_byte(tags["AD_JID"])
            push_byte(domain_type or 0)
            push_byte(device or 0)
            write_string(user)
        else:
            push_byte(tags["JID_PAIR"])
            if user:
                write_string(user)
            else:
                push_byte(tags["LIST_EMPTY"])
            write_string(server)

    def pack_nibble(char: str) -> int:
        if char == "-":
            return 10
        if char == ".":
            return 11
        if char == "\x00":
            return 15
        if "0" <= char <= "9":
            return ord(char) - ord("0")
        raise ValueError(f'invalid byte for nibble "{char}"')

    def pack_hex(char: str) -> int:
        if "0" <= char <= "9":
            return ord(char) - ord("0")
        if "A" <= char <= "F":
            return 10 + ord(char) - ord("A")
        if "a" <= char <= "f":
            return 10 + ord(char) - ord("a")
        if char == "\x00":
            return 15
        raise ValueError(f'Invalid hex char "{char}"')

    def write_packed_bytes(value: str, packed_type: str) -> None:
        if len(value) > tags["PACKED_MAX"]:
            raise ValueError("Too many bytes to pack")
        push_byte(tags["NIBBLE_8"] if packed_type == "nibble" else tags["HEX_8"])
        rounded_length = (len(value) + 1) // 2
        if len(value) % 2 != 0:
            rounded_length |= 128
        push_byte(rounded_length)
        pack_fn = pack_nibble if packed_type == "nibble" else pack_hex

        def pair(v1: str, v2: str) -> int:
            return (pack_fn(v1) << 4) | pack_fn(v2)

        half = len(value) // 2
        for i in range(half):
            push_byte(pair(value[2 * i], value[2 * i + 1]))
        if len(value) % 2 != 0:
            push_byte(pair(value[-1], "\x00"))

    def is_nibble(value: str | None) -> bool:
        if not value or len(value) > tags["PACKED_MAX"]:
            return False
        for char in value:
            if not ("0" <= char <= "9" or char in "-."):
                return False
        return True

    def is_hex(value: str | None) -> bool:
        if not value or len(value) > tags["PACKED_MAX"]:
            return False
        for char in value:
            if not ("0" <= char <= "9" or "A" <= char <= "F"):
                return False
        return True

    def write_string(value: str | None) -> None:
        if value is None:
            push_byte(tags["LIST_EMPTY"])
            return
        if value == "":
            write_string_raw(value)
            return

        token_index = token_map.get(value)
        if token_index is not None:
            if "dict" in token_index:
                push_byte(tags["DICTIONARY_0"] + int(token_index["dict"]))
            push_byte(int(token_index["index"]))
        elif is_nibble(value):
            write_packed_bytes(value, "nibble")
        elif is_hex(value):
            write_packed_bytes(value, "hex")
        else:
            decoded_jid = jid_decode(value)
            if decoded_jid:
                write_jid(decoded_jid)
            else:
                write_string_raw(value)

    def write_list_start(list_size: int) -> None:
        if list_size == 0:
            push_byte(tags["LIST_EMPTY"])
        elif list_size < 256:
            push_bytes([tags["LIST_8"], list_size])
        else:
            push_byte(tags["LIST_16"])
            push_int16(list_size)

    if not tag:
        raise ValueError("Invalid node: tag cannot be undefined")

    valid_attributes = [key for key, value in attrs.items() if value is not None]
    write_list_start(2 * len(valid_attributes) + 1 + (1 if content is not None else 0))
    write_string(tag)

    for key in valid_attributes:
        value = attrs[key]
        if isinstance(value, str):
            write_string(key)
            write_string(value)

    if isinstance(content, str):
        write_string(content)
    elif isinstance(content, (bytes, bytearray)):
        content_bytes = bytes(content)
        write_byte_length(len(content_bytes))
        push_bytes(content_bytes)
    elif isinstance(content, list):
        valid_content = [item for item in content if item and getattr(item, "tag", None)]
        write_list_start(len(valid_content))
        for item in valid_content:
            _encode_binary_node_inner(item, opts, buffer)
    elif content is not None:
        raise ValueError(f'invalid children for header "{tag}": {content} ({type(content).__name__})')

    return buffer
