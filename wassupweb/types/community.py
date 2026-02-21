from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class CommunityCreateInput(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    subject: str
    body: str


class CommunityCreateGroupInput(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    subject: str
    participants: list[str] = Field(default_factory=list)
    parent_community_jid: str = Field(alias="parentCommunityJid")


class CommunityLeaveInput(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    id: str


class CommunitySubjectUpdateInput(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    jid: str
    subject: str


class CommunityLinkGroupInput(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    group_jid: str = Field(alias="groupJid")
    parent_community_jid: str = Field(alias="parentCommunityJid")


class CommunityUnlinkGroupInput(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    group_jid: str = Field(alias="groupJid")
    parent_community_jid: str = Field(alias="parentCommunityJid")


class CommunityFetchLinkedGroupsInput(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    jid: str


class CommunityRequestParticipantsUpdateInput(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    jid: str
    participants: list[str] = Field(default_factory=list)
    action: Literal["approve", "reject"]


class CommunityParticipantsUpdateInput(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    jid: str
    participants: list[str] = Field(default_factory=list)
    action: Literal["add", "remove", "promote", "demote", "modify"]


class CommunityDescriptionUpdateInput(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    jid: str
    description: str | None = None


class CommunityInviteCodeInput(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    jid: str


class CommunityRevokeInviteInput(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    jid: str


class CommunityAcceptInviteInput(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    code: str


class CommunityRevokeInviteV4Input(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    community_jid: str = Field(alias="communityJid")
    invited_jid: str = Field(alias="invitedJid")


class CommunityAcceptInviteV4Input(BaseModel):
    model_config = ConfigDict(populate_by_name=True, arbitrary_types_allowed=True)
    key: str | dict[str, Any]
    invite_message: dict[str, Any] = Field(alias="inviteMessage")


class CommunityInviteInfoInput(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    code: str


class CommunityToggleEphemeralInput(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    jid: str
    ephemeral_expiration: int = Field(alias="ephemeralExpiration")


class CommunitySettingUpdateInput(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    jid: str
    setting: Literal["announcement", "not_announcement", "locked", "unlocked"]


class CommunityMemberAddModeInput(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    jid: str
    mode: Literal["admin_add", "all_member_add"]


class CommunityJoinApprovalModeInput(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    jid: str
    mode: Literal["on", "off"]
