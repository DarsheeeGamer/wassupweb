from __future__ import annotations

from datetime import datetime
from enum import IntEnum, StrEnum
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field


class WAMessageAddressingMode(StrEnum):
    PN = "pn"
    LID = "lid"


class WAMessageStatus(IntEnum):
    ERROR = 0
    PENDING = 1
    SERVER_ACK = 2
    DELIVERY_ACK = 3
    READ = 4
    PLAYED = 5


# Numeric values can vary between WA proto revisions.
# We keep named constants stable for code paths that match against semantic events.
class WAMessageStubType(IntEnum):
    REVOKE = 1
    CIPHERTEXT = 2
    GROUP_CREATE = 20
    GROUP_CHANGE_SUBJECT = 21
    GROUP_CHANGE_ICON = 22
    GROUP_CHANGE_INVITE_LINK = 23
    GROUP_CHANGE_DESCRIPTION = 24
    GROUP_CHANGE_RESTRICT = 25
    GROUP_CHANGE_ANNOUNCE = 26
    GROUP_PARTICIPANT_ADD = 27
    GROUP_PARTICIPANT_REMOVE = 28
    GROUP_PARTICIPANT_PROMOTE = 29
    GROUP_PARTICIPANT_DEMOTE = 30
    GROUP_PARTICIPANT_INVITE = 31
    GROUP_PARTICIPANT_LEAVE = 32
    GROUP_PARTICIPANT_CHANGE_NUMBER = 33
    GROUP_PARTICIPANT_ADD_REQUEST_JOIN = 71
    GROUP_MEMBERSHIP_JOIN_APPROVAL_MODE = 145
    GROUP_MEMBER_ADD_MODE = 171
    GROUP_MEMBERSHIP_JOIN_APPROVAL_REQUEST_NON_ADMIN_ADD = 172
    CALL_MISSED_VOICE = 100
    CALL_MISSED_VIDEO = 101
    CALL_MISSED_GROUP_VOICE = 102
    CALL_MISSED_GROUP_VIDEO = 103
    BIZ_PRIVACY_MODE_TO_BSP = 120
    BIZ_PRIVACY_MODE_TO_FB = 121


class WAMessageKey(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="allow")
    remote_jid: str | None = Field(default=None, alias="remoteJid")
    from_me: bool | None = Field(default=None, alias="fromMe")
    id: str | None = None
    participant: str | None = None
    remote_jid_alt: str | None = Field(default=None, alias="remoteJidAlt")
    participant_alt: str | None = Field(default=None, alias="participantAlt")
    server_id: str | None = Field(default=None, alias="server_id")
    addressing_mode: str | None = Field(default=None, alias="addressingMode")
    is_view_once: bool | None = Field(default=None, alias="isViewOnce")


class WAMessage(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="allow")
    key: WAMessageKey
    message: dict[str, Any] | None = None
    message_timestamp: int | float | datetime | None = Field(default=None, alias="messageTimestamp")
    push_name: str | None = Field(default=None, alias="pushName")
    status: int | WAMessageStatus | None = None
    participant: str | None = None
    participant_alt: str | None = Field(default=None, alias="participantAlt")
    user_receipt: list[dict[str, Any]] | None = Field(default=None, alias="userReceipt")
    reactions: list[dict[str, Any]] | None = None
    poll_updates: list[dict[str, Any]] | None = Field(default=None, alias="pollUpdates")
    event_responses: list[dict[str, Any]] | None = Field(default=None, alias="eventResponses")
    message_stub_type: int | str | None = Field(default=None, alias="messageStubType")
    message_stub_parameters: list[Any] | None = Field(default=None, alias="messageStubParameters")
    category: str | None = None
    retry_count: int | None = Field(default=None, alias="retryCount")


WAMessageContent = dict[str, Any]
WAContactMessage = dict[str, Any]
WAContactsArrayMessage = dict[str, Any]
WATextMessage = dict[str, Any]
WAContextInfo = dict[str, Any]
WALocationMessage = dict[str, Any]
WAGenericMediaMessage = dict[str, Any]

WAMediaPayloadURL = dict[str, str]
WAMediaPayloadStream = dict[str, Any]
WAMediaUpload = bytes | str | Path | WAMediaPayloadStream | WAMediaPayloadURL
MessageType = str

MessageWithContextInfo = Literal[
    "imageMessage",
    "contactMessage",
    "locationMessage",
    "extendedTextMessage",
    "documentMessage",
    "audioMessage",
    "videoMessage",
    "call",
    "contactsArrayMessage",
    "liveLocationMessage",
    "templateMessage",
    "stickerMessage",
    "groupInviteMessage",
    "templateButtonReplyMessage",
    "productMessage",
    "listMessage",
    "orderMessage",
    "listResponseMessage",
    "buttonsMessage",
    "buttonsResponseMessage",
    "interactiveMessage",
    "interactiveResponseMessage",
    "pollCreationMessage",
    "requestPhoneNumberMessage",
    "messageHistoryBundle",
    "eventMessage",
    "newsletterAdminInviteMessage",
    "albumMessage",
    "stickerPackMessage",
    "pollResultSnapshotMessage",
    "messageHistoryNotice",
]

DownloadableMessage = dict[str, Any]
MessageReceiptType = Literal["read", "read-self", "hist_sync", "peer_msg", "sender", "inactive", "played"] | None


class MediaConnHost(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    hostname: str
    max_content_length_bytes: int = Field(alias="maxContentLengthBytes")


class MediaConnInfo(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    auth: str
    ttl: int
    hosts: list[MediaConnHost]
    fetch_date: datetime = Field(alias="fetchDate")


class WAUrlInfo(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="allow")
    canonical_url: str = Field(alias="canonical-url")
    matched_text: str = Field(alias="matched-text")
    title: str
    description: str | None = None
    jpeg_thumbnail: bytes | None = Field(default=None, alias="jpegThumbnail")
    high_quality_thumbnail: dict[str, Any] | None = Field(default=None, alias="highQualityThumbnail")
    original_thumbnail_url: str | None = Field(default=None, alias="originalThumbnailUrl")


class PollMessageOptions(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    name: str
    selectable_count: int | None = Field(default=None, alias="selectableCount")
    values: list[str]
    message_secret: bytes | None = Field(default=None, alias="messageSecret")
    to_announcement_group: bool | None = Field(default=None, alias="toAnnouncementGroup")


class EventMessageOptions(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    name: str
    description: str | None = None
    start_date: datetime = Field(alias="startDate")
    end_date: datetime | None = Field(default=None, alias="endDate")
    location: dict[str, Any] | None = None
    call: Literal["audio", "video"] | None = None
    is_cancelled: bool | None = Field(default=None, alias="isCancelled")
    is_schedule_call: bool | None = Field(default=None, alias="isScheduleCall")
    extra_guests_allowed: bool | None = Field(default=None, alias="extraGuestsAllowed")
    message_secret: bytes | None = Field(default=None, alias="messageSecret")


class ButtonReplyInfo(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    display_text: str = Field(alias="displayText")
    id: str
    index: int


class GroupInviteInfo(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    invite_code: str = Field(alias="inviteCode")
    invite_expiration: int = Field(alias="inviteExpiration")
    text: str
    jid: str
    subject: str


class WASendableProduct(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="allow")
    product_image: WAMediaUpload = Field(alias="productImage")


AnyMediaMessageContent = dict[str, Any]
AnyRegularMessageContent = dict[str, Any]
AnyMessageContent = dict[str, Any]
GroupMetadataParticipants = dict[str, Any]
MessageRelayOptions = dict[str, Any]
MiscMessageGenerationOptions = dict[str, Any]
MessageGenerationOptionsFromContent = dict[str, Any]


class WAMediaUploadFunction(Protocol):
    async def __call__(
        self,
        enc_file_path: str,
        opts: dict[str, Any],
    ) -> dict[str, Any]:
        ...


class MediaGenerationOptions(BaseModel):
    model_config = ConfigDict(populate_by_name=True, arbitrary_types_allowed=True)
    logger: Any = None
    media_type_override: str | None = Field(default=None, alias="mediaTypeOverride")
    upload: Any
    media_cache: Any = Field(default=None, alias="mediaCache")
    media_upload_timeout_ms: int | None = Field(default=None, alias="mediaUploadTimeoutMs")
    options: dict[str, Any] | None = None
    background_color: str | None = Field(default=None, alias="backgroundColor")
    font: int | None = None


MessageContentGenerationOptions = dict[str, Any]
MessageGenerationOptions = dict[str, Any]
MessageUpsertType = Literal["append", "notify"]
MessageUserReceipt = dict[str, Any]


class WAMessageUpdate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    update: dict[str, Any]
    key: WAMessageKey


WAMessageCursor = dict[str, WAMessageKey | None]


class MessageUserReceiptUpdate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    key: WAMessageKey
    receipt: MessageUserReceipt


class MediaDecryptionKeyInfo(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    iv: bytes
    cipher_key: bytes = Field(alias="cipherKey")
    mac_key: bytes | None = Field(default=None, alias="macKey")


class MinimalMessage(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    key: WAMessageKey
    message_timestamp: int | float | datetime | None = Field(default=None, alias="messageTimestamp")
