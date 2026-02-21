from __future__ import annotations

from typing import Any

import pytest

import wassupweb.socket.business as business_mod
from wassupweb.socket.business import BusinessSocket
from wassupweb.types.business import BusinessHoursConfig, UpdateBusinessProfileProps
from wassupweb.wabinary import BinaryNode


class _BusinessHarness:
    def __init__(self) -> None:
        self.sent: list[BinaryNode] = []
        self.config = type("Cfg", (), {"options": {}})()

    async def query_node(self, node: BinaryNode) -> BinaryNode:
        self.sent.append(node)
        return BinaryNode(tag="iq", attrs={"type": "result"}, content=[])


@pytest.mark.asyncio
async def test_update_business_profile_builds_delta_nodes() -> None:
    obj = _BusinessHarness()
    profile = UpdateBusinessProfileProps(
        address="addr",
        email="a@example.com",
        description="desc",
        websites=["https://a.example", "https://b.example"],
        hours=BusinessHoursConfig.model_validate(
            {
                "timezone": "UTC",
                "days": [
                    {"day": "mon", "mode": "specific_hours", "openTimeInMinutes": "540", "closeTimeInMinutes": "1080"},
                    {"day": "sun", "mode": "open_24h"},
                ],
            }
        ),
    )

    await BusinessSocket.update_business_profile(obj, profile)  # type: ignore[arg-type]

    assert obj.sent
    node = obj.sent[0]
    assert node.attrs["xmlns"] == "w:biz"
    business_profile = node.content[0]
    assert business_profile.tag == "business_profile"
    assert business_profile.attrs["v"] == "3"
    assert business_profile.attrs["mutation_type"] == "delta"
    tags = [child.tag for child in business_profile.content]
    assert "address" in tags
    assert "email" in tags
    assert "description" in tags
    assert tags.count("website") == 2
    assert "business_hours" in tags

    business_hours = next(child for child in business_profile.content if child.tag == "business_hours")
    assert business_hours.attrs["timezone"] == "UTC"
    cfg_nodes = business_hours.content
    assert cfg_nodes[0].attrs["day_of_week"] == "mon"
    assert cfg_nodes[0].attrs["open_time"] == "540"
    assert cfg_nodes[1].attrs["mode"] == "open_24h"


@pytest.mark.asyncio
async def test_update_business_profile_accepts_dict_input() -> None:
    obj = _BusinessHarness()
    await BusinessSocket.update_business_profile(  # type: ignore[arg-type]
        obj,
        {"description": "hello world"},
    )

    assert obj.sent
    business_profile = obj.sent[0].content[0]
    tags = [child.tag for child in business_profile.content]
    assert tags == ["description"]


class _BusinessProfileHarness:
    def resolve_chat_jid(self, jid: str) -> str:
        return jid

    async def query_node(self, _node: BinaryNode, timeout_ms: int | None = None) -> BinaryNode:
        _ = timeout_ms
        return BinaryNode(
            tag="iq",
            attrs={"type": "result"},
            content=[
                BinaryNode(
                    tag="business_profile",
                    attrs={},
                    content=[
                        BinaryNode(
                            tag="profile",
                            attrs={"jid": "123@s.whatsapp.net"},
                            content=[
                                BinaryNode(tag="address", attrs={}, content=b"123 main"),
                                BinaryNode(tag="description", attrs={}, content=b"desc"),
                                BinaryNode(tag="website", attrs={}, content=b"https://example.com"),
                                BinaryNode(tag="email", attrs={}, content=b"a@example.com"),
                                BinaryNode(
                                    tag="categories",
                                    attrs={},
                                    content=[BinaryNode(tag="category", attrs={}, content=b"shopping")],
                                ),
                                BinaryNode(
                                    tag="business_hours",
                                    attrs={"timezone": "UTC"},
                                    content=[
                                        BinaryNode(
                                            tag="business_hours_config",
                                            attrs={"day_of_week": "mon", "mode": "open_24h"},
                                        )
                                    ],
                                ),
                            ],
                        )
                    ],
                )
            ],
        )


@pytest.mark.asyncio
async def test_get_business_profile_uses_parsed_chats_shape() -> None:
    obj = _BusinessProfileHarness()
    profile = await BusinessSocket.get_business_profile(obj, "123@s.whatsapp.net")  # type: ignore[arg-type]
    assert profile is not None
    assert profile.wid == "123@s.whatsapp.net"
    assert profile.website == ["https://example.com"]
    assert profile.business_hours.timezone == "UTC"


def test_wa_upload_to_server_prefers_socket_uploader_attribute() -> None:
    sentinel = lambda *_args, **_kwargs: None
    obj = _BusinessHarness()
    obj.wa_upload_to_server = sentinel  # type: ignore[attr-defined]
    assert BusinessSocket._wa_upload_to_server(obj) is sentinel  # type: ignore[arg-type]


def test_wa_upload_to_server_falls_back_to_config_options() -> None:
    sentinel = lambda *_args, **_kwargs: None
    obj = _BusinessHarness()
    obj.config.options = {"waUploadToServer": sentinel}
    assert BusinessSocket._wa_upload_to_server(obj) is sentinel  # type: ignore[arg-type]


def test_wa_upload_to_server_raises_when_missing() -> None:
    obj = _BusinessHarness()
    obj.config.options = {}
    with pytest.raises(RuntimeError, match="waUploadToServer upload function missing in config.options"):
        BusinessSocket._wa_upload_to_server(obj)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_get_catalog_builds_expected_node_with_default_me_jid_and_cursor(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Harness:
        def __init__(self) -> None:
            self.sent: list[BinaryNode] = []
            self.config = type("Cfg", (), {"auth": type("Auth", (), {"creds": type("Creds", (), {"me": {"id": "123@s.whatsapp.net"}})()})()})()

        def resolve_chat_jid(self, jid: str) -> str:
            return jid

        def _me_jid(self) -> str:
            return "123@s.whatsapp.net"

        async def query_node(self, node: BinaryNode) -> BinaryNode:
            self.sent.append(node)
            return BinaryNode(tag="iq", attrs={"type": "result"}, content=[])

    monkeypatch.setattr(business_mod, "parse_catalog_node", lambda _node: {"ok": True})
    obj = _Harness()
    out = await BusinessSocket.get_catalog(obj, {"cursor": "after-1", "limit": 7})  # type: ignore[arg-type]

    assert out == {"ok": True}
    sent = obj.sent[0]
    assert sent.attrs["xmlns"] == "w:biz:catalog"
    product_catalog = sent.content[0]
    assert product_catalog.attrs["jid"] == "123@s.whatsapp.net"
    assert product_catalog.attrs["allow_shop_source"] == "true"
    tags = [item.tag for item in product_catalog.content]
    assert tags == ["limit", "width", "height", "after"]
    assert product_catalog.content[0].content == b"7"
    assert product_catalog.content[3].content == b"after-1"


@pytest.mark.asyncio
async def test_get_collections_builds_expected_limits_and_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Harness:
        def __init__(self) -> None:
            self.sent: list[BinaryNode] = []
            self.config = type("Cfg", (), {"auth": type("Auth", (), {"creds": type("Creds", (), {"me": {"id": "123@s.whatsapp.net"}})()})()})()

        def resolve_chat_jid(self, jid: str) -> str:
            return jid

        def _me_jid(self) -> str:
            return "123@s.whatsapp.net"

        async def query_node(self, node: BinaryNode) -> BinaryNode:
            self.sent.append(node)
            return BinaryNode(tag="iq", attrs={"type": "result"}, content=[])

    monkeypatch.setattr(business_mod, "parse_collections_node", lambda _node: {"collections": []})
    obj = _Harness()
    out = await BusinessSocket.get_collections(obj)  # type: ignore[arg-type]

    assert out == {"collections": []}
    sent = obj.sent[0]
    assert sent.attrs["smax_id"] == "35"
    collections_node = sent.content[0]
    assert collections_node.attrs["biz_jid"] == "123@s.whatsapp.net"
    limits = {item.tag: item.content for item in collections_node.content}
    assert limits["collection_limit"] == b"51"
    assert limits["item_limit"] == b"51"
    assert limits["width"] == b"100"
    assert limits["height"] == b"100"


@pytest.mark.asyncio
async def test_product_delete_returns_deleted_count_from_response() -> None:
    class _Harness:
        async def query_node(self, _node: BinaryNode) -> BinaryNode:
            return BinaryNode(
                tag="iq",
                attrs={"type": "result"},
                content=[BinaryNode(tag="product_catalog_delete", attrs={"deleted_count": "2"})],
            )

    out = await BusinessSocket.product_delete(_Harness(), ["p1", "p2"])  # type: ignore[arg-type]
    assert out == {"deleted": 2}


@pytest.mark.asyncio
async def test_business_typed_profile_and_catalog_wrappers() -> None:
    class _Harness:
        def __init__(self) -> None:
            self.calls: list[tuple[str, Any]] = []

        async def get_business_profile(self, jid: str) -> dict[str, Any]:
            self.calls.append(("profile", jid))
            return {"jid": jid}

        async def update_business_profile(self, profile: dict[str, Any] | Any) -> BinaryNode:
            self.calls.append(("set_profile", profile))
            return BinaryNode(tag="iq", attrs={"type": "result"}, content=[])

        async def get_catalog(self, options: dict[str, Any] | Any | None = None) -> dict[str, Any]:
            self.calls.append(("catalog", options))
            return {"ok": True}

        async def get_collections(self, jid: str | None = None, limit: int = 51) -> dict[str, Any]:
            self.calls.append(("collections", (jid, limit)))
            return {"collections": []}

        async def get_order_details(self, order_id: str, token_base64: str) -> dict[str, Any]:
            self.calls.append(("order", (order_id, token_base64)))
            return {"id": order_id}

    obj = _Harness()
    profile = await BusinessSocket.fetch_business_profile(obj, {"jid": "1@s.whatsapp.net"})  # type: ignore[arg-type]
    await BusinessSocket.set_business_profile(obj, {"profile": {"description": "new"}})  # type: ignore[arg-type]
    catalog = await BusinessSocket.fetch_catalog(obj, {"options": {"limit": 7, "cursor": "c"}})  # type: ignore[arg-type]
    collections = await BusinessSocket.fetch_collections(obj, {"jid": "1@s.whatsapp.net", "limit": 20})  # type: ignore[arg-type]
    order = await BusinessSocket.fetch_order_details(  # type: ignore[arg-type]
        obj,
        {"orderId": "o-1", "tokenBase64": "tok"},
    )

    assert profile == {"jid": "1@s.whatsapp.net"}
    assert catalog == {"ok": True}
    assert collections == {"collections": []}
    assert order == {"id": "o-1"}
    assert ("profile", "1@s.whatsapp.net") in obj.calls
    assert ("collections", ("1@s.whatsapp.net", 20)) in obj.calls


@pytest.mark.asyncio
async def test_business_typed_product_and_cover_photo_wrappers() -> None:
    class _Harness:
        def __init__(self) -> None:
            self.calls: list[tuple[str, Any]] = []

        async def update_cover_photo(self, photo: Any) -> str:
            self.calls.append(("cover_update", photo))
            return "p-1"

        async def remove_cover_photo(self, photo_id: str) -> BinaryNode:
            self.calls.append(("cover_remove", photo_id))
            return BinaryNode(tag="iq", attrs={"type": "result"}, content=[])

        async def product_update(self, product_id: str, update: dict[str, Any] | Any) -> dict[str, Any]:
            self.calls.append(("product_update", (product_id, update)))
            return {"id": product_id}

        async def product_create(self, create: dict[str, Any] | Any) -> dict[str, Any]:
            self.calls.append(("product_create", create))
            return {"id": "new"}

        async def product_delete(self, product_ids: list[str]) -> dict[str, int]:
            self.calls.append(("product_delete", product_ids))
            return {"deleted": len(product_ids)}

    obj = _Harness()
    cover_id = await BusinessSocket.update_business_cover_photo(obj, {"photo": b"img"})  # type: ignore[arg-type]
    removed = await BusinessSocket.remove_business_cover_photo(obj, {"photoId": "p-1"})  # type: ignore[arg-type]
    updated = await BusinessSocket.update_product(  # type: ignore[arg-type]
        obj,
        {
            "productId": "prod-1",
            "update": {
                "name": "N",
                "description": "D",
                "price": 10,
                "currency": "USD",
                "images": [],
                "retailerId": "r1",
            },
        },
    )
    created = await BusinessSocket.create_product(  # type: ignore[arg-type]
        obj,
        {
            "create": {
                "name": "N",
                "description": "D",
                "price": 10,
                "currency": "USD",
                "images": [],
                "retailerId": "r1",
                "originCountryCode": "US",
            }
        },
    )
    deleted = await BusinessSocket.delete_products(  # type: ignore[arg-type]
        obj,
        {"productIds": ["a", "b"]},
    )

    assert cover_id == "p-1"
    assert isinstance(removed, BinaryNode)
    assert updated == {"id": "prod-1"}
    assert created == {"id": "new"}
    assert deleted == {"deleted": 2}
    assert ("cover_remove", "p-1") in obj.calls
    assert ("product_delete", ["a", "b"]) in obj.calls
