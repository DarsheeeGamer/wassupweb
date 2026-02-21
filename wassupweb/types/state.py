from __future__ import annotations

from datetime import datetime
from enum import IntEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .contact import Contact


class SyncState(IntEnum):
    Connecting = 0
    AwaitingInitialSync = 1
    Syncing = 2
    Online = 3


WAConnectionState = Literal["open", "connecting", "close"]


class LastDisconnect(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="allow")
    error: Any = None
    date: datetime


class LegacyConnectionState(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    phone_connected: bool = Field(alias="phoneConnected")
    user: Contact | None = None


class ConnectionState(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    connection: WAConnectionState
    last_disconnect: LastDisconnect | None = Field(default=None, alias="lastDisconnect")
    is_new_login: bool | None = Field(default=None, alias="isNewLogin")
    qr: str | None = None
    received_pending_notifications: bool | None = Field(default=None, alias="receivedPendingNotifications")
    legacy: LegacyConnectionState | None = None
    is_online: bool | None = Field(default=None, alias="isOnline")
