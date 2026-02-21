from __future__ import annotations

from typing import Any

from .types import BinaryNode


def get_binary_node_children(node: BinaryNode | None, child_tag: str) -> list[BinaryNode]:
    if not node or not isinstance(node.content, list):
        return []
    return [child for child in node.content if child.tag == child_tag]


def get_binary_node_child(node: BinaryNode | None, child_tag: str) -> BinaryNode | None:
    children = get_binary_node_children(node, child_tag)
    return children[0] if children else None


def get_all_binary_node_children(node: BinaryNode) -> list[BinaryNode]:
    return node.content if isinstance(node.content, list) else []


def get_binary_node_child_buffer(node: BinaryNode | None, child_tag: str) -> bytes | None:
    child = get_binary_node_child(node, child_tag)
    if child and isinstance(child.content, (bytes, bytearray)):
        return bytes(child.content)
    return None


def get_binary_node_child_string(node: BinaryNode | None, child_tag: str) -> str | None:
    child = get_binary_node_child(node, child_tag)
    if not child:
        return None
    if isinstance(child.content, str):
        return child.content
    if isinstance(child.content, (bytes, bytearray)):
        return bytes(child.content).decode("utf-8")
    return None


def get_binary_node_child_uint(node: BinaryNode, child_tag: str, length: int) -> int | None:
    buff = get_binary_node_child_buffer(node, child_tag)
    if buff is None:
        return None
    return _buffer_to_uint(buff, length)


def assert_node_error_free(node: BinaryNode) -> None:
    err_node = get_binary_node_child(node, "error")
    if err_node is not None:
        text = err_node.attrs.get("text", "Unknown error")
        code = err_node.attrs.get("code")
        raise RuntimeError(f"{text} (code={code})")


def reduce_binary_node_to_dictionary(node: BinaryNode, tag: str) -> dict[str, str]:
    nodes = get_binary_node_children(node, tag)
    result: dict[str, str] = {}
    for current in nodes:
        attrs = current.attrs
        if isinstance(attrs.get("name"), str):
            result[attrs["name"]] = attrs.get("value") or attrs.get("config_value") or ""
        else:
            key = attrs.get("config_code")
            if key:
                result[key] = attrs.get("value") or attrs.get("config_value") or ""
    return result


def _buffer_to_uint(data: bytes, length: int) -> int:
    value = 0
    for i in range(length):
        value = 256 * value + data[i]
    return value


def binary_node_to_string(node: BinaryNode | Any, indent: int = 0) -> str:
    if node is None:
        return ""

    tabs = "\t" * indent
    if isinstance(node, str):
        return tabs + node
    if isinstance(node, (bytes, bytearray)):
        return tabs + bytes(node).hex()
    if isinstance(node, list):
        return "\n".join(binary_node_to_string(item, indent + 1) for item in node)

    children = binary_node_to_string(node.content, indent + 1)
    attrs_text = " ".join(
        f"{k}='{v}'" for (k, v) in (node.attrs or {}).items() if v is not None
    )
    tag_start = f"<{node.tag} {attrs_text}".rstrip()
    if children:
        return f"{tabs}{tag_start}>\n{children}\n{tabs}</{node.tag}>"
    return f"{tabs}{tag_start}/>"
