from __future__ import annotations

from ...types.usync import USyncQueryProtocol
from ...wabinary.types import BinaryNode
from ..user import USyncUser


class LIDProtocol(USyncQueryProtocol):
    name = "lid"

    def get_query_element(self) -> BinaryNode:
        return BinaryNode(tag="lid", attrs={})

    def get_user_element(self, user: USyncUser) -> BinaryNode | None:
        if user.lid:
            return BinaryNode(tag="lid", attrs={"jid": user.lid})
        return None

    def parser(self, node: BinaryNode) -> str | None:
        if node.tag == "lid":
            return node.attrs.get("val")
        return None

    # camelCase aliases for Baileys parity
    getQueryElement = get_query_element
    getUserElement = get_user_element
