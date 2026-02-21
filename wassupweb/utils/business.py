from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import os
import tempfile
import inspect
from typing import Any, TypeVar

from ..types.product import CatalogCollection, CatalogStatus, OrderDetails, OrderProduct, Product, ProductCreate, ProductUpdate
from ..wabinary import (
    BinaryNode,
    get_binary_node_child,
    get_binary_node_child_string,
    get_binary_node_children,
)
from .generics import generate_message_id_v2
from .messages_media import get_stream, get_url_from_direct_path

T = TypeVar("T", ProductCreate, ProductUpdate)


def parse_catalog_node(node: BinaryNode) -> dict[str, Any]:
    catalog_node = get_binary_node_child(node, "product_catalog")
    products = [parse_product_node(item) for item in get_binary_node_children(catalog_node, "product")]
    paging = get_binary_node_child(catalog_node, "paging")
    return {"products": products, "nextPageCursor": get_binary_node_child_string(paging, "after") if paging else None}


def parse_collections_node(node: BinaryNode) -> dict[str, list[CatalogCollection]]:
    collections_node = get_binary_node_child(node, "collections")
    collections: list[CatalogCollection] = []
    for collection_node in get_binary_node_children(collections_node, "collection"):
        item = CatalogCollection(
            id=get_binary_node_child_string(collection_node, "id") or "",
            name=get_binary_node_child_string(collection_node, "name") or "",
            products=[parse_product_node(prod) for prod in get_binary_node_children(collection_node, "product")],
            status=parse_status_info(collection_node),
        )
        collections.append(item)
    return {"collections": collections}


def parse_order_details_node(node: BinaryNode) -> OrderDetails:
    order_node = get_binary_node_child(node, "order")
    products: list[OrderProduct] = []
    for product_node in get_binary_node_children(order_node, "product"):
        image_node = get_binary_node_child(product_node, "image")
        products.append(
            OrderProduct(
                id=get_binary_node_child_string(product_node, "id") or "",
                name=get_binary_node_child_string(product_node, "name") or "",
                imageUrl=get_binary_node_child_string(image_node, "url") or "",
                price=int(get_binary_node_child_string(product_node, "price") or "0"),
                currency=get_binary_node_child_string(product_node, "currency") or "",
                quantity=int(get_binary_node_child_string(product_node, "quantity") or "0"),
            )
        )

    price_node = get_binary_node_child(order_node, "price")
    return OrderDetails(
        price={"total": int(get_binary_node_child_string(price_node, "total") or "0"), "currency": get_binary_node_child_string(price_node, "currency") or ""},
        products=products,
    )


def to_product_node(product_id: str | None, product: ProductCreate | ProductUpdate) -> BinaryNode:
    attrs: dict[str, str] = {}
    content: list[BinaryNode] = []
    product_data = product.model_dump(by_alias=True, exclude_none=False) if hasattr(product, "model_dump") else dict(product)

    if product_id is not None:
        content.append(BinaryNode(tag="id", attrs={}, content=product_id.encode("utf-8")))
    if product_data.get("name") is not None:
        content.append(BinaryNode(tag="name", attrs={}, content=str(product_data["name"]).encode("utf-8")))
    if product_data.get("description") is not None:
        content.append(BinaryNode(tag="description", attrs={}, content=str(product_data["description"]).encode("utf-8")))
    if product_data.get("retailerId") is not None:
        content.append(BinaryNode(tag="retailer_id", attrs={}, content=str(product_data["retailerId"]).encode("utf-8")))

    images = product_data.get("images") or []
    if images:
        image_nodes: list[BinaryNode] = []
        for image in images:
            if not isinstance(image, dict) or "url" not in image:
                raise ValueError("expected product image to already be uploaded")
            image_nodes.append(
                BinaryNode(
                    tag="image",
                    attrs={},
                    content=[BinaryNode(tag="url", attrs={}, content=str(image["url"]).encode("utf-8"))],
                )
            )
        content.append(BinaryNode(tag="media", attrs={}, content=image_nodes))

    if product_data.get("price") is not None:
        content.append(BinaryNode(tag="price", attrs={}, content=str(product_data["price"]).encode("utf-8")))
    if product_data.get("currency") is not None:
        content.append(BinaryNode(tag="currency", attrs={}, content=str(product_data["currency"]).encode("utf-8")))

    if "originCountryCode" in product_data:
        origin = product_data.get("originCountryCode")
        if origin is None:
            attrs["compliance_category"] = "COUNTRY_ORIGIN_EXEMPT"
        else:
            content.append(
                BinaryNode(
                    tag="compliance_info",
                    attrs={},
                    content=[
                        BinaryNode(tag="country_code_origin", attrs={}, content=str(origin).encode("utf-8")),
                    ],
                )
            )

    if product_data.get("isHidden") is not None:
        attrs["is_hidden"] = str(bool(product_data["isHidden"])).lower()

    return BinaryNode(tag="product", attrs=attrs, content=content)


def parse_product_node(product_node: BinaryNode) -> Product:
    is_hidden = product_node.attrs.get("is_hidden") == "true"
    media_node = get_binary_node_child(product_node, "media")
    status_info_node = get_binary_node_child(product_node, "status_info")
    return Product(
        id=get_binary_node_child_string(product_node, "id") or "",
        imageUrls=parse_image_urls(media_node),
        reviewStatus={"whatsapp": get_binary_node_child_string(status_info_node, "status") or ""},
        availability="in stock",
        name=get_binary_node_child_string(product_node, "name") or "",
        retailerId=get_binary_node_child_string(product_node, "retailer_id"),
        url=get_binary_node_child_string(product_node, "url"),
        description=get_binary_node_child_string(product_node, "description") or "",
        price=int(get_binary_node_child_string(product_node, "price") or "0"),
        currency=get_binary_node_child_string(product_node, "currency") or "",
        isHidden=is_hidden,
    )


async def uploading_necessary_images_of_product(
    product: T,
    wa_upload_to_server: Any,
    timeout_ms: int = 30_000,
) -> T:
    product_data = product.model_dump(by_alias=True, exclude_none=False) if hasattr(product, "model_dump") else dict(product)
    images = product_data.get("images")
    if images:
        product_data["images"] = await uploading_necessary_images(images, wa_upload_to_server, timeout_ms)
    if isinstance(product, ProductCreate):
        return ProductCreate.model_validate(product_data)  # type: ignore[return-value]
    if isinstance(product, ProductUpdate):
        return ProductUpdate.model_validate(product_data)  # type: ignore[return-value]
    return product  # pragma: no cover - generic fallback


async def uploading_necessary_images(
    images: list[Any],
    wa_upload_to_server: Any,
    timeout_ms: int = 30_000,
) -> list[dict[str, str]]:
    async def _upload_one(image: Any) -> dict[str, str]:
        if isinstance(image, dict) and "url" in image:
            url = str(image["url"])
            if ".whatsapp.net" in url:
                return {"url": url}

        stream_info = await get_stream(image)
        stream = stream_info["stream"]
        hasher = hashlib.sha256()
        file_path = os.path.join(tempfile.gettempdir(), f"img{generate_message_id_v2()}")
        try:
            with open(file_path, "wb") as out:
                async for block in stream:
                    hasher.update(block)
                    out.write(block)

            sha = base64.b64encode(hasher.digest()).decode("ascii")
            uploaded = wa_upload_to_server(
                file_path,
                {"mediaType": "product-catalog-image", "fileEncSha256B64": sha, "timeoutMs": timeout_ms},
            )
            if inspect.isawaitable(uploaded):
                uploaded = await uploaded

            direct_path = (
                uploaded.get("directPath")
                or uploaded.get("direct_path")
                or uploaded.get("url")
                or uploaded.get("mediaUrl")
            )
            if not direct_path:
                raise RuntimeError("upload did not return direct path")

            if str(direct_path).startswith("http"):
                return {"url": str(direct_path)}
            return {"url": get_url_from_direct_path(str(direct_path))}
        finally:
            with contextlib.suppress(Exception):
                os.unlink(file_path)
    if not images:
        return []
    return list(await asyncio.gather(*[_upload_one(image) for image in images]))


def parse_image_urls(media_node: BinaryNode | None) -> dict[str, str]:
    image_node = get_binary_node_child(media_node, "image")
    return {
        "requested": get_binary_node_child_string(image_node, "request_image_url") or "",
        "original": get_binary_node_child_string(image_node, "original_image_url") or "",
    }


def parse_status_info(node: BinaryNode) -> CatalogStatus:
    status_node = get_binary_node_child(node, "status_info")
    return CatalogStatus(status=get_binary_node_child_string(status_node, "status") or "", canAppeal=get_binary_node_child_string(status_node, "can_appeal") == "true")


# camelCase aliases for parity
parseCatalogNode = parse_catalog_node
parseCollectionsNode = parse_collections_node
parseOrderDetailsNode = parse_order_details_node
toProductNode = to_product_node
parseProductNode = parse_product_node
uploadingNecessaryImagesOfProduct = uploading_necessary_images_of_product
uploadingNecessaryImages = uploading_necessary_images


__all__ = [
    "parse_catalog_node",
    "parse_collections_node",
    "parse_order_details_node",
    "to_product_node",
    "parse_product_node",
    "uploading_necessary_images_of_product",
    "uploading_necessary_images",
]
