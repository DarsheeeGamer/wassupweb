from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .message import WAMediaUpload


class CatalogResult(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    data: dict[str, Any]


class ProductCreateResult(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    data: dict[str, Any]


class CatalogStatus(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    status: str
    can_appeal: bool = Field(alias="canAppeal")


ProductAvailability = Literal["in stock"]


class ProductBase(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    name: str
    retailer_id: str | None = Field(default=None, alias="retailerId")
    url: str | None = None
    description: str
    price: int
    currency: str
    is_hidden: bool | None = Field(default=None, alias="isHidden")


class ProductCreate(ProductBase):
    origin_country_code: str | None = Field(alias="originCountryCode")
    images: list[WAMediaUpload]


class ProductUpdate(ProductBase):
    images: list[WAMediaUpload]


class Product(ProductBase):
    id: str
    image_urls: dict[str, str] = Field(alias="imageUrls")
    review_status: dict[str, str] = Field(alias="reviewStatus")
    availability: ProductAvailability


class CatalogCollection(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    id: str
    name: str
    products: list[Product]
    status: CatalogStatus


class OrderPrice(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    currency: str
    total: int


class OrderProduct(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    id: str
    image_url: str = Field(alias="imageUrl")
    name: str
    quantity: int
    currency: str
    price: int


class OrderDetails(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    price: OrderPrice
    products: list[OrderProduct]


CatalogCursor = str


class GetCatalogOptions(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    cursor: CatalogCursor | None = None
    limit: int | None = None
    jid: str | None = None
