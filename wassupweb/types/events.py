from __future__ import annotations

from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from .auth import AuthenticationCreds
from .chat import Chat, ChatUpdate, PresenceData
from .contact import Contact
from .group_metadata import GroupMetadata, GroupParticipant, ParticipantAction, RequestJoinAction, RequestJoinMethod
from .label import Label
from .label_association import LabelAssociation
from .message import (
    MessageUpsertType,
    MessageUserReceiptUpdate,
    WAMessage,
    WAMessageKey,
    WAMessageUpdate,
)
from .state import ConnectionState


class MessagingHistorySet(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    chats: list[Chat]
    contacts: list[Contact]
    messages: list[WAMessage]
    lid_pn_mappings: list[dict[str, str]] | None = Field(default=None, alias="lidPnMappings")
    is_latest: bool | None = Field(default=None, alias="isLatest")
    progress: int | float | None = None
    sync_type: int | None = Field(default=None, alias="syncType")
    peer_data_request_session_id: str | None = Field(default=None, alias="peerDataRequestSessionId")


class PresenceUpdate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    id: str
    presences: dict[str, PresenceData]


class MessagesDelete(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    keys: list[WAMessageKey] | None = None
    jid: str | None = None
    all: bool | None = None


class MessageUpsertPayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    messages: list[WAMessage]
    type: MessageUpsertType
    request_id: str | None = Field(default=None, alias="requestId")


class MessageReactionPayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    key: WAMessageKey
    reaction: dict[str, Any]


class MessageMediaUpdate(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="allow")
    key: WAMessageKey
    media: dict[str, bytes] | None = None
    error: Any | None = None
    status_code: int | None = Field(default=None, alias="statusCode")


class GroupParticipantsUpdate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    id: str
    author: str
    author_pn: str | None = Field(default=None, alias="authorPn")
    participants: list[GroupParticipant]
    action: ParticipantAction


class GroupJoinRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    id: str
    author: str
    author_pn: str | None = Field(default=None, alias="authorPn")
    participant: str
    participant_pn: str | None = Field(default=None, alias="participantPn")
    action: RequestJoinAction
    method: RequestJoinMethod


class GroupMemberTagUpdate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    group_id: str = Field(alias="groupId")
    participant: str
    participant_alt: str | None = Field(default=None, alias="participantAlt")
    label: str
    message_timestamp: int | None = Field(default=None, alias="messageTimestamp")


class BlocklistSet(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    blocklist: list[str]


class BlocklistUpdate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    blocklist: list[str]
    type: str


class LabelAssociationUpdate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    association: LabelAssociation
    type: str


class NewsletterReactionEvent(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    id: str
    server_id: str
    reaction: dict[str, Any]


class NewsletterViewEvent(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    id: str
    server_id: str
    count: int


class NewsletterParticipantsUpdate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    id: str
    author: str
    user: str
    new_role: str
    action: str


class NewsletterSettingsUpdate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    id: str
    update: dict[str, Any]


class ChatLockEvent(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    id: str
    locked: bool


class LidMappingUpdate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    lid: str
    pn: str


class SettingUpdate(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="allow")
    setting: str
    value: Any


class HistorySetBuffer(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    chats: dict[str, Chat] = Field(default_factory=dict)
    contacts: dict[str, Contact] = Field(default_factory=dict)
    messages: dict[str, WAMessage] = Field(default_factory=dict)
    empty: bool = True
    is_latest: bool = Field(default=False, alias="isLatest")
    progress: int | float | None = None
    sync_type: int | None = Field(default=None, alias="syncType")
    peer_data_request_session_id: str | None = Field(default=None, alias="peerDataRequestSessionId")


class BufferedEventData(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    history_sets: HistorySetBuffer = Field(default_factory=HistorySetBuffer, alias="historySets")
    chat_upserts: dict[str, Chat] = Field(default_factory=dict, alias="chatUpserts")
    chat_updates: dict[str, ChatUpdate] = Field(default_factory=dict, alias="chatUpdates")
    chat_deletes: set[str] = Field(default_factory=set, alias="chatDeletes")
    contact_upserts: dict[str, Contact] = Field(default_factory=dict, alias="contactUpserts")
    contact_updates: dict[str, Contact] = Field(default_factory=dict, alias="contactUpdates")
    message_upserts: dict[str, dict[str, Any]] = Field(default_factory=dict, alias="messageUpserts")
    message_updates: dict[str, WAMessageUpdate] = Field(default_factory=dict, alias="messageUpdates")
    message_deletes: dict[str, WAMessageKey] = Field(default_factory=dict, alias="messageDeletes")
    message_reactions: dict[str, dict[str, Any]] = Field(default_factory=dict, alias="messageReactions")
    message_receipts: dict[str, dict[str, Any]] = Field(default_factory=dict, alias="messageReceipts")
    group_updates: dict[str, GroupMetadata] = Field(default_factory=dict, alias="groupUpdates")


class BaileysEventEmitter(Protocol):
    def on(self, event: str, listener: Any) -> None:
        ...

    def off(self, event: str, listener: Any) -> None:
        ...

    def remove_all_listeners(self, event: str) -> None:
        ...

    def emit(self, event: str, arg: Any) -> bool:
        ...


# Intentionally excludes call events based on user requirement.
BaileysEventMap = {
    "connection.update": ConnectionState,
    "creds.update": AuthenticationCreds,
    "messaging-history.set": MessagingHistorySet,
    "chats.upsert": list[Chat],
    "chats.update": list[ChatUpdate],
    "chats.delete": list[str],
    "presence.update": PresenceUpdate,
    "contacts.upsert": list[Contact],
    "contacts.update": list[Contact],
    "messages.delete": MessagesDelete,
    "messages.update": list[WAMessageUpdate],
    "messages.upsert": MessageUpsertPayload,
    "messages.reaction": list[MessageReactionPayload],
    "message-receipt.update": list[MessageUserReceiptUpdate],
    "messages.media-update": list[MessageMediaUpdate],
    "groups.upsert": list[GroupMetadata],
    "groups.update": list[GroupMetadata],
    "group-participants.update": GroupParticipantsUpdate,
    "group.join-request": GroupJoinRequest,
    "group.member-tag.update": GroupMemberTagUpdate,
    "blocklist.set": BlocklistSet,
    "blocklist.update": BlocklistUpdate,
    "labels.edit": Label,
    "labels.association": LabelAssociationUpdate,
    "newsletter.reaction": NewsletterReactionEvent,
    "newsletter.view": NewsletterViewEvent,
    "newsletter-participants.update": NewsletterParticipantsUpdate,
    "newsletter-settings.update": NewsletterSettingsUpdate,
    "chats.lock": ChatLockEvent,
    "settings.update": SettingUpdate,
    "lid-mapping.update": LidMappingUpdate,
}
