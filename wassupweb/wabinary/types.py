from __future__ import annotations

from dataclasses import dataclass
from typing import Any


BinaryNodeContent = list["BinaryNode"] | str | bytes | None


@dataclass(slots=True)
class BinaryNode:
    tag: str
    attrs: dict[str, str]
    content: BinaryNodeContent = None


@dataclass(slots=True)
class BinaryNodeCodingOptions:
    TAGS: dict[str, int]
    DOUBLE_BYTE_TOKENS: list[list[str]]
    SINGLE_BYTE_TOKENS: list[str]
    TOKEN_MAP: dict[str, dict[str, int]]


BinaryNodeAttributes = dict[str, str]
BinaryNodeData = BinaryNodeContent
