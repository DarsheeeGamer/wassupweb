from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class XWAPaths(StrEnum):
    xwa2_newsletter_create = "xwa2_newsletter_create"
    xwa2_newsletter_subscribers = "xwa2_newsletter_subscribers"
    xwa2_newsletter_view = "xwa2_newsletter_view"
    xwa2_newsletter_metadata = "xwa2_newsletter"
    xwa2_newsletter_admin_count = "xwa2_newsletter_admin"
    xwa2_newsletter_mute_v2 = "xwa2_newsletter_mute_v2"
    xwa2_newsletter_unmute_v2 = "xwa2_newsletter_unmute_v2"
    xwa2_newsletter_follow = "xwa2_newsletter_follow"
    xwa2_newsletter_unfollow = "xwa2_newsletter_unfollow"
    xwa2_newsletter_change_owner = "xwa2_newsletter_change_owner"
    xwa2_newsletter_demote = "xwa2_newsletter_demote"
    xwa2_newsletter_delete_v2 = "xwa2_newsletter_delete_v2"


class QueryIds(StrEnum):
    CREATE = "8823471724422422"
    UPDATE_METADATA = "24250201037901610"
    METADATA = "6563316087068696"
    SUBSCRIBERS = "9783111038412085"
    FOLLOW = "7871414976211147"
    UNFOLLOW = "7238632346214362"
    MUTE = "29766401636284406"
    UNMUTE = "9864994326891137"
    ADMIN_COUNT = "7130823597031706"
    CHANGE_OWNER = "7341777602580933"
    DEMOTE = "6551828931592903"
    DELETE = "30062808666639665"


class NewsletterUpdate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    name: str | None = None
    description: str | None = None
    picture: str | None = None


NewsletterViewRole = Literal["ADMIN", "GUEST", "OWNER", "SUBSCRIBER"]


class NewsletterCreateTextMeta(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    id: str
    text: str
    update_time: str = Field(alias="update_time")


class NewsletterCreateImageMeta(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    direct_path: str = Field(alias="direct_path")
    id: str
    type: str


class NewsletterCreateThreadMetadata(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    creation_time: str = Field(alias="creation_time")
    description: NewsletterCreateTextMeta
    handle: str | None
    invite: str
    name: NewsletterCreateTextMeta
    picture: NewsletterCreateImageMeta
    preview: NewsletterCreateImageMeta
    subscribers_count: str = Field(alias="subscribers_count")
    verification: Literal["VERIFIED", "UNVERIFIED"]


class NewsletterCreateViewerMetadata(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    mute: Literal["ON", "OFF"]
    role: NewsletterViewRole


class NewsletterCreateResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    id: str
    state: dict[str, str]
    thread_metadata: NewsletterCreateThreadMetadata = Field(alias="thread_metadata")
    viewer_metadata: NewsletterCreateViewerMetadata = Field(alias="viewer_metadata")


class NewsletterReactionCode(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    code: str
    count: int


class NewsletterPicture(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    url: str | None = None
    direct_path: str | None = Field(default=None, alias="directPath")
    media_key: str | None = Field(default=None, alias="mediaKey")
    id: str | None = None


class NewsletterMetadata(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    id: str
    owner: str | None = None
    name: str
    description: str | None = None
    invite: str | None = None
    creation_time: int | None = None
    subscribers: int | None = None
    picture: NewsletterPicture | None = None
    verification: Literal["VERIFIED", "UNVERIFIED"] | None = None
    reaction_codes: list[NewsletterReactionCode] | None = None
    mute_state: Literal["ON", "OFF"] | None = None
    thread_metadata: dict[str, object] | None = None


class NewsletterCreateInput(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    name: str
    description: str | None = None


class NewsletterUpdateInput(BaseModel):
    model_config = ConfigDict(populate_by_name=True, arbitrary_types_allowed=True)
    jid: str
    updates: NewsletterUpdate | dict[str, Any]


class NewsletterJidInput(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    jid: str


class NewsletterMetadataInput(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    type: str
    key: str


class NewsletterNameUpdateInput(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    jid: str
    name: str


class NewsletterDescriptionUpdateInput(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    jid: str
    description: str


class NewsletterPictureUpdateInput(BaseModel):
    model_config = ConfigDict(populate_by_name=True, arbitrary_types_allowed=True)
    jid: str
    content: Any


class NewsletterReactInput(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    jid: str
    server_id: str = Field(alias="serverId")
    reaction: str | None = None


class NewsletterFetchMessagesInput(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    jid: str
    count: int
    since: int | None = None
    after: int | None = None


class NewsletterChangeOwnerInput(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    jid: str
    new_owner_jid: str = Field(alias="newOwnerJid")


class NewsletterDemoteInput(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    jid: str
    user_jid: str = Field(alias="userJid")
