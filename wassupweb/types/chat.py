from __future__ import annotations

from typing import Any, Callable, Literal

from pydantic import BaseModel, ConfigDict, Field

from .auth import AccountSettings
from .business import QuickReplyAction
from .label import LabelActionBody
from .message import MinimalMessage, WAMessageKey

WAPrivacyValue = Literal["all", "contacts", "contact_blacklist", "none"]
WAPrivacyOnlineValue = Literal["all", "match_last_seen"]
WAPrivacyGroupAddValue = Literal["all", "contacts", "contact_blacklist"]
WAReadReceiptsValue = Literal["all", "none"]
WAPrivacyCallValue = Literal["all", "known"]
WAPrivacyMessagesValue = Literal["all", "contacts"]
WAPresence = Literal["unavailable", "available", "composing", "recording", "paused"]

ALL_WA_PATCH_NAMES = ["critical_block", "critical_unblock_low", "regular_high", "regular_low", "regular"]
WAPatchName = Literal["critical_block", "critical_unblock_low", "regular_high", "regular_low", "regular"]


class PresenceData(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    last_known_presence: WAPresence = Field(alias="lastKnownPresence")
    last_seen: int | None = Field(default=None, alias="lastSeen")


class BotListInfo(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    jid: str
    persona_id: str = Field(alias="personaId")


class ChatMutation(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="allow")
    sync_action: dict[str, Any] = Field(alias="syncAction")
    index: list[str]


class WAPatchCreate(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="allow")
    sync_action: dict[str, Any] = Field(alias="syncAction")
    index: list[str]
    type: WAPatchName
    api_version: int = Field(alias="apiVersion")
    operation: int


class Chat(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="allow")
    last_message_recv_timestamp: int | None = Field(default=None, alias="lastMessageRecvTimestamp")


class ChatUpdate(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="allow")
    conditional: Callable[[Any], bool | None] | None = None
    timestamp: int | None = None


LastMessageList = list[MinimalMessage] | dict[str, Any]
ChatModification = dict[str, Any]
InitialReceivedChatsState = dict[str, dict[str, int | None]]


class InitialAppStateSyncOptions(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    account_settings: AccountSettings = Field(alias="accountSettings")


class DeleteForMeAction(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    delete_media: bool = Field(alias="deleteMedia")
    key: WAMessageKey
    timestamp: int


class StarAction(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    messages: list[dict[str, Any]]
    star: bool


class QuickReplyModification(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    quick_reply: QuickReplyAction = Field(alias="quickReply")


class ChatModifyInput(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    mod: dict[str, Any]
    jid: str


class DisableLinkPreviewsPrivacyInput(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    is_previews_disabled: bool = Field(alias="isPreviewsDisabled")


class StarMessagesInput(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    jid: str
    messages: list[dict[str, Any]]
    star: bool


class ContactUpsertInput(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    jid: str
    contact: dict[str, Any]


class ContactRemoveInput(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    jid: str


class LabelUpsertInput(BaseModel):
    model_config = ConfigDict(populate_by_name=True, arbitrary_types_allowed=True)
    jid: str
    labels: LabelActionBody | dict[str, Any]


class ChatLabelInput(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    jid: str
    label_id: str = Field(alias="labelId")


class MessageLabelInput(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    jid: str
    message_id: str = Field(alias="messageId")
    label_id: str = Field(alias="labelId")


class QuickReplyUpsertInput(BaseModel):
    model_config = ConfigDict(populate_by_name=True, arbitrary_types_allowed=True)
    quick_reply: QuickReplyAction | dict[str, Any] = Field(alias="quickReply")


class QuickReplyRemoveInput(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    timestamp: str


class ArchiveChatInput(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    jid: str
    archive: bool
    last_messages: list[dict[str, Any]] | None = Field(default=None, alias="lastMessages")


class MuteChatInput(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    jid: str
    mute_seconds: int | None = Field(default=None, alias="muteSeconds")


class MarkReadInput(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    jid: str
    message_ids: list[str] = Field(alias="messageIds")
    read: bool = True


class FetchManyJidsInput(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    jids: list[str] = Field(default_factory=list)


class OnWhatsAppInput(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    phone_numbers: list[str] = Field(default_factory=list, alias="phoneNumbers")


class PnFromLidUSyncInput(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    jids: list[str] = Field(default_factory=list)


class BlockStatusInput(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    jid: str
    action: Literal["block", "unblock"]


class CleanDirtyBitsInput(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    type: Literal["account_sync", "groups"]
    from_timestamp: int | str | None = Field(default=None, alias="fromTimestamp")


class ProfilePictureUrlInput(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    jid: str
    picture_type: Literal["preview", "image"] = Field(default="preview", alias="pictureType")
    timeout_ms: int | None = Field(default=None, alias="timeoutMs")


class ProfilePictureUpdateInput(BaseModel):
    model_config = ConfigDict(populate_by_name=True, arbitrary_types_allowed=True)
    jid: str
    content: Any
    dimensions: Any | None = None


class ProfilePictureRemoveInput(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    jid: str


class ProfileStatusInput(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    status: str


class ProfileNameInput(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    name: str


class CallLinkCreateInput(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    media: Literal["audio", "video"]
    event: dict[str, int] | None = None
    timeout_ms: int | None = Field(default=None, alias="timeoutMs")


class PresenceUpdateInput(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    type: str
    to_jid: str | None = Field(default=None, alias="toJid")


class PresenceSubscribeInput(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    to_jid: str = Field(alias="toJid")
