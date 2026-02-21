from __future__ import annotations

import base64
import contextlib
import os
from typing import Any

from ..types.business import (
    BusinessCatalogInput,
    BusinessCollectionsInput,
    BusinessCoverPhotoRemoveInput,
    BusinessCoverPhotoUpdateInput,
    BusinessOrderDetailsInput,
    BusinessProductCreateInput,
    BusinessProductDeleteInput,
    BusinessProductUpdateInput,
    BusinessProfileInput,
    BusinessProfileUpdateInput,
    UpdateBusinessProfileProps,
)
from ..types.product import GetCatalogOptions, ProductCreate, ProductUpdate
from ..utils.business import (
    parse_catalog_node,
    parse_collections_node,
    parse_order_details_node,
    parse_product_node,
    to_product_node,
    uploading_necessary_images_of_product,
)
from ..utils.messages_media import get_raw_media_upload_data
from ..wabinary import BinaryNode
from .chats import ChatsSocket
from .newsletter import NewsletterSocket

S_WHATSAPP_NET = "s.whatsapp.net"


class BusinessSocket(NewsletterSocket):
    def _me_jid(self) -> str | None:
        auth = getattr(self.config, "auth", None)
        if not auth:
            return None
        me = getattr(auth.creds, "me", None) or {}
        return me.get("id")

    async def get_business_profile(self, jid: str) -> Any:
        return await ChatsSocket.get_business_profile(self, jid)  # parity: same parsed profile shape as chats socket

    async def update_business_profile(self, profile: UpdateBusinessProfileProps | dict[str, Any]) -> BinaryNode:
        model = profile if isinstance(profile, UpdateBusinessProfileProps) else UpdateBusinessProfileProps.model_validate(profile)
        payload = model.model_dump(by_alias=True, exclude_none=True)
        nodes: list[BinaryNode] = []

        for key in ("address", "email", "description"):
            value = payload.get(key)
            if value is not None:
                nodes.append(BinaryNode(tag=key, attrs={}, content=str(value)))

        websites = payload.get("websites")
        if isinstance(websites, list):
            for website in websites:
                nodes.append(BinaryNode(tag="website", attrs={}, content=str(website)))

        hours = payload.get("hours")
        if isinstance(hours, dict):
            day_nodes: list[BinaryNode] = []
            for day in hours.get("days") or []:
                if not isinstance(day, dict):
                    continue
                attrs = {
                    "day_of_week": str(day.get("day")),
                    "mode": str(day.get("mode")),
                }
                if day.get("mode") == "specific_hours":
                    if day.get("openTimeInMinutes") is not None:
                        attrs["open_time"] = str(day.get("openTimeInMinutes"))
                    if day.get("closeTimeInMinutes") is not None:
                        attrs["close_time"] = str(day.get("closeTimeInMinutes"))
                day_nodes.append(BinaryNode(tag="business_hours_config", attrs=attrs))

            nodes.append(
                BinaryNode(
                    tag="business_hours",
                    attrs={"timezone": str(hours.get("timezone") or "")},
                    content=day_nodes,
                )
            )

        node = BinaryNode(
            tag="iq",
            attrs={"to": "s.whatsapp.net", "type": "set", "xmlns": "w:biz"},
            content=[BinaryNode(tag="business_profile", attrs={"v": "3", "mutation_type": "delta"}, content=nodes)],
        )
        return await self.query_node(node)

    def _wa_upload_to_server(self) -> Any:
        uploader = getattr(self, "wa_upload_to_server", None)
        if callable(uploader):
            return uploader
        options = self.config.options if isinstance(self.config.options, dict) else {}
        uploader = options.get("waUploadToServer") or options.get("upload")
        if not uploader:
            raise RuntimeError("waUploadToServer upload function missing in config.options")
        return uploader

    async def update_cover_photo(self, photo: Any) -> str:
        uploader = self._wa_upload_to_server()
        raw = await get_raw_media_upload_data(photo, "biz-cover-photo", self._logger)
        file_sha_b64 = base64.b64encode(raw["fileSha256"]).decode("ascii")
        file_path = raw["filePath"]
        try:
            uploaded = await uploader(
                file_path,
                {"fileEncSha256B64": file_sha_b64, "mediaType": "biz-cover-photo"},
            )
        finally:
            with contextlib.suppress(Exception):
                os.unlink(file_path)

        meta_hmac = uploaded.get("meta_hmac")
        fbid = uploaded.get("fbid")
        ts = uploaded.get("ts")

        node = BinaryNode(
            tag="iq",
            attrs={"to": S_WHATSAPP_NET, "type": "set", "xmlns": "w:biz"},
            content=[
                BinaryNode(
                    tag="business_profile",
                    attrs={"v": "3", "mutation_type": "delta"},
                    content=[
                        BinaryNode(
                            tag="cover_photo",
                            attrs={
                                "id": str(fbid),
                                "op": "update",
                                "token": str(meta_hmac or ""),
                                "ts": str(ts or ""),
                            },
                        )
                    ],
                )
            ],
        )
        await self.query_node(node)
        return str(fbid)

    async def remove_cover_photo(self, photo_id: str) -> BinaryNode:
        node = BinaryNode(
            tag="iq",
            attrs={"to": S_WHATSAPP_NET, "type": "set", "xmlns": "w:biz"},
            content=[
                BinaryNode(
                    tag="business_profile",
                    attrs={"v": "3", "mutation_type": "delta"},
                    content=[BinaryNode(tag="cover_photo", attrs={"op": "delete", "id": photo_id})],
                )
            ],
        )
        return await self.query_node(node)

    async def get_catalog(self, options: GetCatalogOptions | dict[str, Any] | None = None) -> dict[str, Any]:
        opts = options if isinstance(options, GetCatalogOptions) else GetCatalogOptions.model_validate(options or {})
        jid = self.resolve_chat_jid(opts.jid or self._me_jid() or "")

        query_nodes = [
            BinaryNode(tag="limit", attrs={}, content=str(opts.limit or 10).encode("utf-8")),
            BinaryNode(tag="width", attrs={}, content=b"100"),
            BinaryNode(tag="height", attrs={}, content=b"100"),
        ]
        if opts.cursor:
            query_nodes.append(BinaryNode(tag="after", attrs={}, content=str(opts.cursor).encode("utf-8")))

        result = await self.query_node(
            BinaryNode(
                tag="iq",
                attrs={"to": S_WHATSAPP_NET, "type": "get", "xmlns": "w:biz:catalog"},
                content=[
                    BinaryNode(
                        tag="product_catalog",
                        attrs={"jid": jid, "allow_shop_source": "true"},
                        content=query_nodes,
                    )
                ],
            )
        )
        return parse_catalog_node(result)

    async def get_collections(self, jid: str | None = None, limit: int = 51) -> dict[str, Any]:
        resolved = self.resolve_chat_jid(jid or self._me_jid() or "")
        result = await self.query_node(
            BinaryNode(
                tag="iq",
                attrs={"to": S_WHATSAPP_NET, "type": "get", "xmlns": "w:biz:catalog", "smax_id": "35"},
                content=[
                    BinaryNode(
                        tag="collections",
                        attrs={"biz_jid": resolved},
                        content=[
                            BinaryNode(tag="collection_limit", attrs={}, content=str(limit).encode("utf-8")),
                            BinaryNode(tag="item_limit", attrs={}, content=str(limit).encode("utf-8")),
                            BinaryNode(tag="width", attrs={}, content=b"100"),
                            BinaryNode(tag="height", attrs={}, content=b"100"),
                        ],
                    )
                ],
            )
        )
        return parse_collections_node(result)

    async def get_order_details(self, order_id: str, token_base64: str) -> Any:
        result = await self.query_node(
            BinaryNode(
                tag="iq",
                attrs={"to": S_WHATSAPP_NET, "type": "get", "xmlns": "fb:thrift_iq", "smax_id": "5"},
                content=[
                    BinaryNode(
                        tag="order",
                        attrs={"op": "get", "id": order_id},
                        content=[
                            BinaryNode(
                                tag="image_dimensions",
                                attrs={},
                                content=[
                                    BinaryNode(tag="width", attrs={}, content=b"100"),
                                    BinaryNode(tag="height", attrs={}, content=b"100"),
                                ],
                            ),
                            BinaryNode(tag="token", attrs={}, content=token_base64.encode("utf-8")),
                        ],
                    )
                ],
            )
        )
        return parse_order_details_node(result)

    async def product_update(self, product_id: str, update: ProductUpdate | dict[str, Any]) -> Any:
        update_model = update if isinstance(update, ProductUpdate) else ProductUpdate.model_validate(update)
        uploader = self._wa_upload_to_server()
        update_model = await uploading_necessary_images_of_product(update_model, uploader)
        edit_node = to_product_node(product_id, update_model)

        result = await self.query_node(
            BinaryNode(
                tag="iq",
                attrs={"to": S_WHATSAPP_NET, "type": "set", "xmlns": "w:biz:catalog"},
                content=[
                    BinaryNode(
                        tag="product_catalog_edit",
                        attrs={"v": "1"},
                        content=[
                            edit_node,
                            BinaryNode(tag="width", attrs={}, content=b"100"),
                            BinaryNode(tag="height", attrs={}, content=b"100"),
                        ],
                    )
                ],
            )
        )
        product_catalog_edit_node = next((item for item in (result.content or []) if isinstance(item, BinaryNode) and item.tag == "product_catalog_edit"), None)
        product_node = None
        if isinstance(product_catalog_edit_node, BinaryNode):
            product_node = next((item for item in (product_catalog_edit_node.content or []) if isinstance(item, BinaryNode) and item.tag == "product"), None)
        return parse_product_node(product_node) if isinstance(product_node, BinaryNode) else None

    async def product_create(self, create: ProductCreate | dict[str, Any]) -> Any:
        create_model = create if isinstance(create, ProductCreate) else ProductCreate.model_validate(create)
        if create_model.is_hidden is None:
            create_model.is_hidden = False

        uploader = self._wa_upload_to_server()
        create_model = await uploading_necessary_images_of_product(create_model, uploader)
        create_node = to_product_node(None, create_model)

        result = await self.query_node(
            BinaryNode(
                tag="iq",
                attrs={"to": S_WHATSAPP_NET, "type": "set", "xmlns": "w:biz:catalog"},
                content=[
                    BinaryNode(
                        tag="product_catalog_add",
                        attrs={"v": "1"},
                        content=[
                            create_node,
                            BinaryNode(tag="width", attrs={}, content=b"100"),
                            BinaryNode(tag="height", attrs={}, content=b"100"),
                        ],
                    )
                ],
            )
        )
        product_catalog_add_node = next((item for item in (result.content or []) if isinstance(item, BinaryNode) and item.tag == "product_catalog_add"), None)
        product_node = None
        if isinstance(product_catalog_add_node, BinaryNode):
            product_node = next((item for item in (product_catalog_add_node.content or []) if isinstance(item, BinaryNode) and item.tag == "product"), None)
        return parse_product_node(product_node) if isinstance(product_node, BinaryNode) else None

    async def product_delete(self, product_ids: list[str]) -> dict[str, int]:
        result = await self.query_node(
            BinaryNode(
                tag="iq",
                attrs={"to": S_WHATSAPP_NET, "type": "set", "xmlns": "w:biz:catalog"},
                content=[
                    BinaryNode(
                        tag="product_catalog_delete",
                        attrs={"v": "1"},
                        content=[
                            BinaryNode(
                                tag="product",
                                attrs={},
                                content=[BinaryNode(tag="id", attrs={}, content=str(item).encode("utf-8"))],
                            )
                            for item in product_ids
                        ],
                    )
                ],
            )
        )
        deleted_count = 0
        product_catalog_del_node = next((item for item in (result.content or []) if isinstance(item, BinaryNode) and item.tag == "product_catalog_delete"), None)
        if isinstance(product_catalog_del_node, BinaryNode):
            deleted_count = int(product_catalog_del_node.attrs.get("deleted_count") or 0)
        return {"deleted": deleted_count}

    # typed convenience interfaces
    async def fetch_business_profile(self, request: BusinessProfileInput | dict[str, Any]) -> Any:
        payload = request if isinstance(request, BusinessProfileInput) else BusinessProfileInput.model_validate(request)
        return await self.get_business_profile(payload.jid)

    async def set_business_profile(self, request: BusinessProfileUpdateInput | dict[str, Any]) -> BinaryNode:
        payload = (
            request if isinstance(request, BusinessProfileUpdateInput) else BusinessProfileUpdateInput.model_validate(request)
        )
        return await self.update_business_profile(payload.profile)

    async def update_business_cover_photo(self, request: BusinessCoverPhotoUpdateInput | dict[str, Any]) -> str:
        payload = (
            request
            if isinstance(request, BusinessCoverPhotoUpdateInput)
            else BusinessCoverPhotoUpdateInput.model_validate(request)
        )
        return await self.update_cover_photo(payload.photo)

    async def remove_business_cover_photo(self, request: BusinessCoverPhotoRemoveInput | dict[str, Any]) -> BinaryNode:
        payload = (
            request
            if isinstance(request, BusinessCoverPhotoRemoveInput)
            else BusinessCoverPhotoRemoveInput.model_validate(request)
        )
        return await self.remove_cover_photo(payload.photo_id)

    async def fetch_catalog(self, request: BusinessCatalogInput | dict[str, Any] | None = None) -> dict[str, Any]:
        payload = (
            request
            if isinstance(request, BusinessCatalogInput)
            else BusinessCatalogInput.model_validate(request or {})
        )
        return await self.get_catalog(payload.options)

    async def fetch_collections(self, request: BusinessCollectionsInput | dict[str, Any] | None = None) -> dict[str, Any]:
        payload = (
            request
            if isinstance(request, BusinessCollectionsInput)
            else BusinessCollectionsInput.model_validate(request or {})
        )
        return await self.get_collections(payload.jid, payload.limit)

    async def fetch_order_details(self, request: BusinessOrderDetailsInput | dict[str, Any]) -> Any:
        payload = (
            request
            if isinstance(request, BusinessOrderDetailsInput)
            else BusinessOrderDetailsInput.model_validate(request)
        )
        return await self.get_order_details(payload.order_id, payload.token_base64)

    async def update_product(self, request: BusinessProductUpdateInput | dict[str, Any]) -> Any:
        payload = (
            request
            if isinstance(request, BusinessProductUpdateInput)
            else BusinessProductUpdateInput.model_validate(request)
        )
        return await self.product_update(payload.product_id, payload.update)

    async def create_product(self, request: BusinessProductCreateInput | dict[str, Any]) -> Any:
        payload = (
            request
            if isinstance(request, BusinessProductCreateInput)
            else BusinessProductCreateInput.model_validate(request)
        )
        return await self.product_create(payload.create)

    async def delete_products(self, request: BusinessProductDeleteInput | dict[str, Any]) -> dict[str, int]:
        payload = (
            request
            if isinstance(request, BusinessProductDeleteInput)
            else BusinessProductDeleteInput.model_validate(request)
        )
        return await self.product_delete(payload.product_ids)

    # camelCase aliases for Baileys parity
    updateBussinesProfile = update_business_profile
    updateCoverPhoto = update_cover_photo
    removeCoverPhoto = remove_cover_photo
    getCatalog = get_catalog
    getCollections = get_collections
    getOrderDetails = get_order_details
    productUpdate = product_update
    productCreate = product_create
    productDelete = product_delete
    fetchBusinessProfile = fetch_business_profile
    setBusinessProfile = set_business_profile
    updateBusinessCoverPhoto = update_business_cover_photo
    removeBusinessCoverPhoto = remove_business_cover_photo
    fetchCatalog = fetch_catalog
    fetchCollections = fetch_collections
    fetchOrderDetails = fetch_order_details
    updateProduct = update_product
    createProduct = create_product
    deleteProducts = delete_products
