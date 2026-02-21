from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .contact import Contact

WAMessageAddressingMode = Literal["pn", "lid"]
ParticipantAction = Literal["add", "remove", "promote", "demote", "modify"]
RequestJoinAction = Literal["created", "revoked", "rejected"]
RequestJoinMethod = Literal["invite_link", "linked_group_join", "non_admin_add"] | None


class GroupParticipant(Contact):
    model_config = ConfigDict(populate_by_name=True)
    is_admin: bool | None = Field(default=None, alias="isAdmin")
    is_super_admin: bool | None = Field(default=None, alias="isSuperAdmin")
    admin: Literal["admin", "superadmin"] | None = None


class GroupMetadata(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    id: str
    notify: str | None = None
    addressing_mode: WAMessageAddressingMode | None = Field(default=None, alias="addressingMode")
    owner: str | None = None
    owner_pn: str | None = Field(default=None, alias="ownerPn")
    owner_country_code: str | None = None
    subject: str
    subject_owner: str | None = Field(default=None, alias="subjectOwner")
    subject_owner_pn: str | None = Field(default=None, alias="subjectOwnerPn")
    subject_time: int | None = Field(default=None, alias="subjectTime")
    creation: int | None = None
    desc: str | None = None
    desc_owner: str | None = Field(default=None, alias="descOwner")
    desc_owner_pn: str | None = Field(default=None, alias="descOwnerPn")
    desc_id: str | None = Field(default=None, alias="descId")
    desc_time: int | None = Field(default=None, alias="descTime")
    linked_parent: str | None = Field(default=None, alias="linkedParent")
    restrict: bool | None = None
    announce: bool | None = None
    member_add_mode: bool | None = Field(default=None, alias="memberAddMode")
    join_approval_mode: bool | None = Field(default=None, alias="joinApprovalMode")
    is_community: bool | None = Field(default=None, alias="isCommunity")
    is_community_announce: bool | None = Field(default=None, alias="isCommunityAnnounce")
    size: int | None = None
    participants: list[GroupParticipant] = Field(default_factory=list)
    ephemeral_duration: int | None = Field(default=None, alias="ephemeralDuration")
    invite_code: str | None = Field(default=None, alias="inviteCode")
    author: str | None = None
    author_pn: str | None = Field(default=None, alias="authorPn")


class WAGroupCreateResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    status: int
    gid: str | None = None
    participants: list[dict[str, dict[str, object]]] | None = None


class GroupModificationResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    status: int
    participants: dict[str, dict[str, object]] | None = None


class GroupCreateInput(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    subject: str
    participants: list[str] = Field(default_factory=list)


class GroupParticipantsUpdateInput(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    jid: str
    participants: list[str] = Field(default_factory=list)
    action: ParticipantAction


class GroupRequestParticipantsUpdateInput(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    jid: str
    participants: list[str] = Field(default_factory=list)
    action: Literal["approve", "reject"]


class GroupSubjectUpdateInput(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    jid: str
    subject: str


class GroupDescriptionUpdateInput(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    jid: str
    description: str | None = None


class GroupSettingUpdateInput(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    jid: str
    setting: Literal["announcement", "not_announcement", "locked", "unlocked"]


class GroupMemberAddModeInput(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    jid: str
    mode: Literal["admin_add", "all_member_add"]


class GroupJoinApprovalModeInput(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    jid: str
    mode: Literal["on", "off"]


class GroupToggleEphemeralInput(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    jid: str
    ephemeral_expiration: int = Field(alias="ephemeralExpiration")
