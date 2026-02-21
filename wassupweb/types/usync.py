from __future__ import annotations

from typing import Any, Protocol

from ..wabinary.types import BinaryNode


class USyncQueryProtocol(Protocol):
    name: str

    def get_query_element(self) -> BinaryNode:
        ...

    def get_user_element(self, user: Any) -> BinaryNode | None:
        ...

    def parser(self, data: BinaryNode) -> Any:
        ...
