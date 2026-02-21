from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from ...types.usync import USyncQueryProtocol
from ...wabinary.generic_utils import assert_node_error_free, get_binary_node_child
from ...wabinary.types import BinaryNode
from ..user import USyncUser


class KeyIndexData(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    timestamp: int
    signed_key_index: bytes | None = Field(default=None, alias="signedKeyIndex")
    expected_timestamp: int | None = Field(default=None, alias="expectedTimestamp")


class DeviceListData(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    id: int
    key_index: int | None = Field(default=None, alias="keyIndex")
    is_hosted: bool | None = Field(default=None, alias="isHosted")


class ParsedDeviceInfo(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    device_list: list[DeviceListData] = Field(default_factory=list, alias="deviceList")
    key_index: KeyIndexData | None = Field(default=None, alias="keyIndex")


class DeviceProtocol(USyncQueryProtocol):
    name = "devices"

    def get_query_element(self) -> BinaryNode:
        return BinaryNode(tag="devices", attrs={"version": "2"})

    def get_user_element(self, user: USyncUser) -> BinaryNode | None:
        _ = user
        return None

    def parser(self, node: BinaryNode) -> ParsedDeviceInfo:
        device_list: list[DeviceListData] = []
        key_index: KeyIndexData | None = None
        if node.tag == "devices":
            assert_node_error_free(node)
            device_list_node = get_binary_node_child(node, "device-list")
            key_index_node = get_binary_node_child(node, "key-index-list")

            if device_list_node and isinstance(device_list_node.content, list):
                for item in device_list_node.content:
                    if item.tag != "device":
                        continue
                    attrs = item.attrs
                    device_list.append(
                        DeviceListData(
                            id=int(attrs.get("id", "0")),
                            key_index=int(attrs.get("key-index", "0")),
                            is_hosted=attrs.get("is_hosted") == "true",
                        )
                    )

            if key_index_node and key_index_node.tag == "key-index-list":
                attrs = key_index_node.attrs
                key_index = KeyIndexData(
                    timestamp=int(attrs.get("ts", "0")),
                    signed_key_index=key_index_node.content if isinstance(key_index_node.content, bytes) else None,
                    expected_timestamp=int(attrs["expected_ts"]) if "expected_ts" in attrs else None,
                )

        return ParsedDeviceInfo(device_list=device_list, key_index=key_index)

    # camelCase aliases for Baileys parity
    getQueryElement = get_query_element
    getUserElement = get_user_element
