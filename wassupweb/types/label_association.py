from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class LabelAssociationType(StrEnum):
    Chat = "label_jid"
    Message = "label_message"


LabelAssociationTypes = Literal["label_jid", "label_message"]


class ChatLabelAssociation(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    type: Literal[LabelAssociationType.Chat] = LabelAssociationType.Chat
    chat_id: str = Field(alias="chatId")
    label_id: str = Field(alias="labelId")


class MessageLabelAssociation(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    type: Literal[LabelAssociationType.Message] = LabelAssociationType.Message
    chat_id: str = Field(alias="chatId")
    message_id: str = Field(alias="messageId")
    label_id: str = Field(alias="labelId")


LabelAssociation = ChatLabelAssociation | MessageLabelAssociation


class ChatLabelAssociationActionBody(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    label_id: str = Field(alias="labelId")


class MessageLabelAssociationActionBody(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    label_id: str = Field(alias="labelId")
    message_id: str = Field(alias="messageId")
