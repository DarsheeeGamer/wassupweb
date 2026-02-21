from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .product import GetCatalogOptions, ProductCreate, ProductUpdate

DayOfWeekBusiness = Literal["sun", "mon", "tue", "wed", "thu", "fri", "sat"]


class BusinessHoursSpecific(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    day: DayOfWeekBusiness
    mode: Literal["specific_hours"]
    open_time_in_minutes: str = Field(alias="openTimeInMinutes")
    close_time_in_minutes: str = Field(alias="closeTimeInMinutes")


class BusinessHoursOpen(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    day: DayOfWeekBusiness
    mode: Literal["open_24h", "appointment_only"]


HoursDay = BusinessHoursSpecific | BusinessHoursOpen


class BusinessHoursConfig(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    timezone: str
    days: list[HoursDay]


class UpdateBusinessProfileProps(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    address: str | None = None
    websites: list[str] | None = None
    email: str | None = None
    description: str | None = None
    hours: BusinessHoursConfig | None = None


class QuickReplyAction(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="allow")
    timestamp: str | None = None


class BusinessProfileInput(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    jid: str


class BusinessProfileUpdateInput(BaseModel):
    model_config = ConfigDict(populate_by_name=True, arbitrary_types_allowed=True)
    profile: UpdateBusinessProfileProps | dict[str, Any]


class BusinessCoverPhotoUpdateInput(BaseModel):
    model_config = ConfigDict(populate_by_name=True, arbitrary_types_allowed=True)
    photo: Any


class BusinessCoverPhotoRemoveInput(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    photo_id: str = Field(alias="photoId")


class BusinessCatalogInput(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    options: GetCatalogOptions | dict[str, Any] | None = None


class BusinessCollectionsInput(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    jid: str | None = None
    limit: int = 51


class BusinessOrderDetailsInput(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    order_id: str = Field(alias="orderId")
    token_base64: str = Field(alias="tokenBase64")


class BusinessProductUpdateInput(BaseModel):
    model_config = ConfigDict(populate_by_name=True, arbitrary_types_allowed=True)
    product_id: str = Field(alias="productId")
    update: ProductUpdate | dict[str, Any]


class BusinessProductCreateInput(BaseModel):
    model_config = ConfigDict(populate_by_name=True, arbitrary_types_allowed=True)
    create: ProductCreate | dict[str, Any]


class BusinessProductDeleteInput(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    product_ids: list[str] = Field(alias="productIds")
