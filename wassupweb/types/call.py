from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

WACallUpdateType = Literal["offer", "ringing", "timeout", "reject", "accept", "terminate"]


class WACallEvent(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    chat_id: str = Field(alias="chatId")
    from_: str = Field(alias="from")
    caller_pn: str | None = Field(default=None, alias="callerPn")
    is_group: bool | None = Field(default=None, alias="isGroup")
    group_jid: str | None = Field(default=None, alias="groupJid")
    id: str
    date: datetime
    is_video: bool | None = Field(default=None, alias="isVideo")
    status: WACallUpdateType
    offline: bool
    latency_ms: int | None = Field(default=None, alias="latencyMs")
