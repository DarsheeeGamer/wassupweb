from __future__ import annotations

from typing import List

from pydantic import BaseModel, ConfigDict, Field

from ..types.usync import USyncQueryProtocol
from ..wabinary.generic_utils import get_binary_node_child
from ..wabinary.types import BinaryNode
from .protocols import (
    BotProfileProtocol,
    ContactProtocol,
    DeviceProtocol,
    DisappearingModeProtocol,
    LIDProtocol,
    StatusProtocol,
)
from .user import USyncUser


class USyncQueryResultItem(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    id: str
    data: dict[str, object] = Field(default_factory=dict)


class USyncQueryResult(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    list: List[USyncQueryResultItem] = Field(default_factory=lambda: [])
    side_list: List[USyncQueryResultItem] = Field(default_factory=lambda: [], alias="sideList")


class USyncQuery:
    def __init__(self) -> None:
        self.protocols: list[USyncQueryProtocol] = []
        self.users: list[USyncUser] = []
        self.context = "interactive"
        self.mode = "query"

    def with_mode(self, mode: str) -> "USyncQuery":
        self.mode = mode
        return self

    def with_context(self, context: str) -> "USyncQuery":
        self.context = context
        return self

    def with_user(self, user: USyncUser) -> "USyncQuery":
        self.users.append(user)
        return self

    def with_device_protocol(self) -> "USyncQuery":
        self.protocols.append(DeviceProtocol())
        return self

    def with_contact_protocol(self) -> "USyncQuery":
        self.protocols.append(ContactProtocol())
        return self

    def with_status_protocol(self) -> "USyncQuery":
        self.protocols.append(StatusProtocol())
        return self

    def with_disappearing_mode_protocol(self) -> "USyncQuery":
        self.protocols.append(DisappearingModeProtocol())
        return self

    def with_bot_profile_protocol(self) -> "USyncQuery":
        self.protocols.append(BotProfileProtocol())
        return self

    def with_lid_protocol(self) -> "USyncQuery":
        self.protocols.append(LIDProtocol())
        return self

    def parse_usync_query_result(self, result: BinaryNode | None) -> USyncQueryResult | None:
        if not result or result.attrs.get("type") != "result":
            return None

        protocol_map = {protocol.name: protocol.parser for protocol in self.protocols}
        query_result = USyncQueryResult()
        usync_node = get_binary_node_child(result, "usync")

        def _parse_items(list_node: BinaryNode | None) -> list[USyncQueryResultItem]:
            parsed_items: list[USyncQueryResultItem] = []
            if list_node and isinstance(list_node.content, list):
                for node in list_node.content:
                    item_id = node.attrs.get("jid")
                    if not item_id:
                        continue
                    data: dict[str, object] = {}
                    if isinstance(node.content, list):
                        for content in node.content:
                            parser = protocol_map.get(content.tag)
                            if not parser:
                                continue
                            parsed = parser(content)
                            if parsed is not None:
                                data[content.tag] = parsed
                    parsed_items.append(USyncQueryResultItem(id=item_id, data=data))
            return parsed_items

        query_result.list = _parse_items(get_binary_node_child(usync_node, "list") if usync_node else None)
        query_result.side_list = _parse_items(get_binary_node_child(usync_node, "side_list") if usync_node else None)

        return query_result

    # camelCase aliases for Baileys parity
    withMode = with_mode
    withContext = with_context
    withUser = with_user
    withDeviceProtocol = with_device_protocol
    withContactProtocol = with_contact_protocol
    withStatusProtocol = with_status_protocol
    withDisappearingModeProtocol = with_disappearing_mode_protocol
    withBotProfileProtocol = with_bot_profile_protocol
    withLIDProtocol = with_lid_protocol
    parseUSyncQueryResult = parse_usync_query_result
