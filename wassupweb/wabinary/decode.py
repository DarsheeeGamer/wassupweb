from __future__ import annotations

import zlib
from typing import Any

from . import constants
from .jid_utils import WAJIDDomains, jid_encode
from .types import BinaryNode


async def decompressing_if_required(buffer: bytes) -> bytes:
    if buffer[0] & 2:
        return zlib.decompress(buffer[1:])
    return buffer[1:]


def decode_decompressed_binary_node(
    buffer: bytes,
    opts: dict[str, Any] | None = None,
    index_ref: dict[str, int] | None = None,
) -> BinaryNode:
    opts = opts or {
        "DOUBLE_BYTE_TOKENS": constants.DOUBLE_BYTE_TOKENS,
        "SINGLE_BYTE_TOKENS": constants.SINGLE_BYTE_TOKENS,
        "TAGS": constants.TAGS,
    }
    index_ref = index_ref or {"index": 0}

    double_tokens = opts["DOUBLE_BYTE_TOKENS"]
    single_tokens = opts["SINGLE_BYTE_TOKENS"]
    tags = opts["TAGS"]

    def check_eos(length: int) -> None:
        if index_ref["index"] + length > len(buffer):
            raise ValueError("end of stream")

    def next_byte() -> int:
        value = buffer[index_ref["index"]]
        index_ref["index"] += 1
        return value

    def read_byte() -> int:
        check_eos(1)
        return next_byte()

    def read_bytes(n: int) -> bytes:
        check_eos(n)
        start = index_ref["index"]
        end = start + n
        value = buffer[start:end]
        index_ref["index"] = end
        return value

    def read_int(n: int, little_endian: bool = False) -> int:
        check_eos(n)
        value = 0
        for i in range(n):
            shift = i if little_endian else n - 1 - i
            value |= next_byte() << (shift * 8)
        return value

    def read_int20() -> int:
        check_eos(3)
        return ((next_byte() & 15) << 16) + (next_byte() << 8) + next_byte()

    def unpack_hex(value: int) -> int:
        if 0 <= value < 16:
            return ord("0") + value if value < 10 else ord("A") + value - 10
        raise ValueError(f"invalid hex: {value}")

    def unpack_nibble(value: int) -> int:
        if 0 <= value <= 9:
            return ord("0") + value
        if value == 10:
            return ord("-")
        if value == 11:
            return ord(".")
        if value == 15:
            return 0
        raise ValueError(f"invalid nibble: {value}")

    def unpack_byte(tag: int, value: int) -> int:
        if tag == tags["NIBBLE_8"]:
            return unpack_nibble(value)
        if tag == tags["HEX_8"]:
            return unpack_hex(value)
        raise ValueError(f"unknown tag: {tag}")

    def read_packed8(tag: int) -> str:
        start_byte = read_byte()
        value = ""
        for _ in range(start_byte & 127):
            cur_byte = read_byte()
            value += chr(unpack_byte(tag, (cur_byte & 0xF0) >> 4))
            value += chr(unpack_byte(tag, cur_byte & 0x0F))
        if (start_byte >> 7) != 0:
            value = value[:-1]
        return value

    def is_list_tag(tag: int) -> bool:
        return tag in {tags["LIST_EMPTY"], tags["LIST_8"], tags["LIST_16"]}

    def read_list_size(tag: int) -> int:
        if tag == tags["LIST_EMPTY"]:
            return 0
        if tag == tags["LIST_8"]:
            return read_byte()
        if tag == tags["LIST_16"]:
            return read_int(2)
        raise ValueError(f"invalid tag for list size: {tag}")

    def get_token_double(index1: int, index2: int) -> str:
        dictionary = double_tokens[index1] if index1 < len(double_tokens) else None
        if dictionary is None:
            raise ValueError(f"Invalid double token dict ({index1})")
        if index2 >= len(dictionary):
            raise ValueError(f"Invalid double token ({index2})")
        return dictionary[index2]

    def read_jid_pair() -> str:
        first = read_string(read_byte())
        second = read_string(read_byte())
        if second:
            return f"{first or ''}@{second}"
        raise ValueError(f"invalid jid pair: {first}, {second}")

    def read_ad_jid() -> str:
        domain_type = int(read_byte())
        device = read_byte()
        user = read_string(read_byte())
        server = "s.whatsapp.net"
        if domain_type == WAJIDDomains.LID:
            server = "lid"
        elif domain_type == WAJIDDomains.HOSTED:
            server = "hosted"
        elif domain_type == WAJIDDomains.HOSTED_LID:
            server = "hosted.lid"
        return jid_encode(user, server, device)

    def read_fb_jid() -> str:
        user = read_string(read_byte())
        device = read_int(2)
        server = read_string(read_byte())
        return f"{user}:{device}@{server}"

    def read_interop_jid() -> str:
        user = read_string(read_byte())
        device = read_int(2)
        integrator = read_int(2)
        server = "interop"
        before = index_ref["index"]
        try:
            server = read_string(read_byte())
        except Exception:
            index_ref["index"] = before
        return f"{integrator}-{user}:{device}@{server}"

    def read_string(tag: int) -> str:
        if 1 <= tag < len(single_tokens):
            return single_tokens[tag] or ""

        if tag in {
            tags["DICTIONARY_0"],
            tags["DICTIONARY_1"],
            tags["DICTIONARY_2"],
            tags["DICTIONARY_3"],
        }:
            return get_token_double(tag - tags["DICTIONARY_0"], read_byte())
        if tag == tags["LIST_EMPTY"]:
            return ""
        if tag == tags["BINARY_8"]:
            return read_bytes(read_byte()).decode("utf-8")
        if tag == tags["BINARY_20"]:
            return read_bytes(read_int20()).decode("utf-8")
        if tag == tags["BINARY_32"]:
            return read_bytes(read_int(4)).decode("utf-8")
        if tag == tags["JID_PAIR"]:
            return read_jid_pair()
        if tag == tags["FB_JID"]:
            return read_fb_jid()
        if tag == tags["INTEROP_JID"]:
            return read_interop_jid()
        if tag == tags["AD_JID"]:
            return read_ad_jid()
        if tag in {tags["HEX_8"], tags["NIBBLE_8"]}:
            return read_packed8(tag)
        raise ValueError(f"invalid string with tag: {tag}")

    def read_list(tag: int) -> list[BinaryNode]:
        items: list[BinaryNode] = []
        size = read_list_size(tag)
        for _ in range(size):
            items.append(decode_decompressed_binary_node(buffer, opts, index_ref))
        return items

    list_size = read_list_size(read_byte())
    header = read_string(read_byte())
    if not list_size or not header:
        raise ValueError("invalid node")

    attrs: dict[str, str] = {}
    data: list[BinaryNode] | bytes | str | None = None
    attributes_len = (list_size - 1) >> 1
    for _ in range(attributes_len):
        key = read_string(read_byte())
        value = read_string(read_byte())
        attrs[key] = value

    if list_size % 2 == 0:
        tag = read_byte()
        if is_list_tag(tag):
            data = read_list(tag)
        else:
            if tag == tags["BINARY_8"]:
                decoded: bytes | str = read_bytes(read_byte())
            elif tag == tags["BINARY_20"]:
                decoded = read_bytes(read_int20())
            elif tag == tags["BINARY_32"]:
                decoded = read_bytes(read_int(4))
            else:
                decoded = read_string(tag)
            data = decoded

    return BinaryNode(tag=header, attrs=attrs, content=data)


async def decode_binary_node(buff: bytes) -> BinaryNode:
    decomp = await decompressing_if_required(buff)
    return decode_decompressed_binary_node(decomp)
