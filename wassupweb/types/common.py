from __future__ import annotations

from enum import IntEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class DisconnectReason(IntEnum):
    connectionClosed = 428
    connectionLost = 408
    connectionReplaced = 440
    timedOut = 408
    loggedOut = 401
    badSession = 500
    restartRequired = 515
    multideviceMismatch = 411
    forbidden = 403
    unavailableService = 503


class WAInitResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    ref: str
    ttl: int
    status: Literal[200]


class WABusinessHoursConfig(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    day_of_week: str
    mode: str
    open_time: int | None = None
    close_time: int | None = None


class WABusinessHours(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    timezone: str | None = None
    config: list[WABusinessHoursConfig] | None = None
    business_config: list[WABusinessHoursConfig] | None = None


class WABusinessProfile(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    description: str
    email: str | None = None
    business_hours: WABusinessHours = Field(alias="business_hours")
    website: list[str] = Field(default_factory=list)
    category: str | None = None
    wid: str | None = None
    address: str | None = None


class CurveKeyPair(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    private: bytes
    public: bytes
