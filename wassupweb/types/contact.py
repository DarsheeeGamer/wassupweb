from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class Contact(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    id: str
    lid: str | None = None
    phone_number: str | None = Field(default=None, alias="phoneNumber")
    name: str | None = None
    notify: str | None = None
    verified_name: str | None = Field(default=None, alias="verifiedName")
    img_url: str | None = Field(default=None, alias="imgUrl")
    status: str | None = None
