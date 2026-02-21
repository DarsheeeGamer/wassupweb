from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field

from ...types.usync import USyncQueryProtocol
from ...wabinary.generic_utils import assert_node_error_free
from ...wabinary.types import BinaryNode
from ..user import USyncUser


class DisappearingModeData(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    duration: int
    set_at: datetime | None = Field(default=None, alias="setAt")


class DisappearingModeProtocol(USyncQueryProtocol):
    name = "disappearing_mode"

    def get_query_element(self) -> BinaryNode:
        return BinaryNode(tag="disappearing_mode", attrs={})

    def get_user_element(self, user: USyncUser) -> None:
        _ = user
        return None

    def parser(self, node: BinaryNode) -> DisappearingModeData | None:
        if node.tag != "disappearing_mode":
            return None
        assert_node_error_free(node)
        duration = int(node.attrs.get("duration", "0"))
        ts = int(node.attrs.get("t", "0"))
        set_at = datetime.fromtimestamp(ts, tz=timezone.utc) if ts else None
        return DisappearingModeData(duration=duration, set_at=set_at)

    # camelCase aliases for Baileys parity
    getQueryElement = get_query_element
    getUserElement = get_user_element
