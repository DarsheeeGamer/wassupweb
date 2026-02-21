from __future__ import annotations

from typing import Any

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class JidKind(StrEnum):
    PN = "pn"
    LID = "lid"
    GROUP = "group"
    NEWSLETTER = "newsletter"
    BROADCAST = "broadcast"
    BOT = "bot"
    UNKNOWN = "unknown"


class UserRef(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="allow")
    user_id: str = Field(alias="userId")
    jid: str | None = None
    pn_jid: str | None = Field(default=None, alias="pnJid")
    lid_jid: str | None = Field(default=None, alias="lidJid")
    kind: JidKind = JidKind.UNKNOWN
    user: str | None = None
    server: str | None = None
    device: int | None = None
    source: Literal["user_id", "jid", "phone", "unknown"] = "unknown"


class JidAlias(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    user_id: str = Field(alias="userId")
    jid: str
    kind: JidKind = JidKind.UNKNOWN


class IdentityResolveResult(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    ref: UserRef
    created: bool = False
    merged: bool = False


class MessageIdentityView(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    remote: UserRef | None = None
    participant: UserRef | None = None
    sender: UserRef | None = None
    remote_user_id: str | None = Field(default=None, alias="remoteUserId")
    participant_user_id: str | None = Field(default=None, alias="participantUserId")
    sender_user_id: str | None = Field(default=None, alias="senderUserId")


class SendMessageInput(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    to: str | UserRef | dict[str, Any]
    content: dict[str, Any]
    options: dict[str, Any] = Field(default_factory=dict)
    prefer: JidKind = JidKind.PN


class SendTextInput(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    to: str | UserRef | dict[str, Any]
    text: str
    options: dict[str, Any] = Field(default_factory=dict)
    prefer: JidKind = JidKind.PN
