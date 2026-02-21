from __future__ import annotations

from ...types.usync import USyncQueryProtocol
from ...wabinary.generic_utils import assert_node_error_free
from ...wabinary.types import BinaryNode
from ..user import USyncUser


class ContactProtocol(USyncQueryProtocol):
    name = "contact"

    def get_query_element(self) -> BinaryNode:
        return BinaryNode(tag="contact", attrs={})

    def get_user_element(self, user: USyncUser) -> BinaryNode:
        return BinaryNode(tag="contact", attrs={}, content=user.phone or "")

    def parser(self, node: BinaryNode) -> bool:
        if node.tag == "contact":
            assert_node_error_free(node)
            return node.attrs.get("type") == "in"
        return False

    # camelCase aliases for Baileys parity
    getQueryElement = get_query_element
    getUserElement = get_user_element
