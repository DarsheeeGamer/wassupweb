from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field

from ...types.usync import USyncQueryProtocol
from ...wabinary.generic_utils import assert_node_error_free
from ...wabinary.types import BinaryNode
from ..user import USyncUser


class StatusData(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    status: str | None = None
    set_at: datetime | None = Field(default=None, alias="setAt")


class StatusProtocol(USyncQueryProtocol):
    name = "status"

    def get_query_element(self) -> BinaryNode:
        return BinaryNode(tag="status", attrs={})

    def get_user_element(self, user: USyncUser) -> None:
        _ = user
        return None

    def parser(self, node: BinaryNode) -> StatusData | None:
        if node.tag != "status":
            return None
        assert_node_error_free(node)
        raw_status: str | None
        if isinstance(node.content, bytes):
            raw_status = node.content.decode("utf-8")
        elif isinstance(node.content, str):
            raw_status = node.content
        else:
            raw_status = None

        ts = int(node.attrs.get("t", "0"))
        set_at = datetime.fromtimestamp(ts, tz=timezone.utc) if ts else None

        if not raw_status:
            if int(node.attrs.get("code", "0")) == 401:
                raw_status = ""
            else:
                raw_status = None
        elif len(raw_status) == 0:
            raw_status = None

        return StatusData(status=raw_status, set_at=set_at)

    # camelCase aliases for Baileys parity
    getQueryElement = get_query_element
    getUserElement = get_user_element
