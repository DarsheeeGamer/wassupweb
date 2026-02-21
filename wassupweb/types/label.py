from __future__ import annotations

from enum import IntEnum

from pydantic import BaseModel, ConfigDict, Field


class Label(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    id: str
    name: str
    color: int
    deleted: bool
    predefined_id: str | None = Field(default=None, alias="predefinedId")


class LabelActionBody(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    id: str
    name: str | None = None
    color: int | None = None
    deleted: bool | None = None
    predefined_id: int | None = Field(default=None, alias="predefinedId")


class LabelColor(IntEnum):
    Color1 = 0
    Color2 = 1
    Color3 = 2
    Color4 = 3
    Color5 = 4
    Color6 = 5
    Color7 = 6
    Color8 = 7
    Color9 = 8
    Color10 = 9
    Color11 = 10
    Color12 = 11
    Color13 = 12
    Color14 = 13
    Color15 = 14
    Color16 = 15
    Color17 = 16
    Color18 = 17
    Color19 = 18
    Color20 = 19
