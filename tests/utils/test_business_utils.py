from __future__ import annotations

import asyncio
from typing import Any

import pytest

import wassupweb.utils.business as business_mod
from wassupweb.types.product import ProductCreate
from wassupweb.wabinary import BinaryNode


def _node(tag: str, attrs: dict[str, Any] | None = None, content: Any = None) -> BinaryNode:
    return BinaryNode(tag=tag, attrs=attrs or {}, content=content)


def test_parse_catalog_collections_and_order_nodes() -> None:
    product = _node(
        "product",
        {"is_hidden": "true"},
        [
            _node("id", content=b"p1"),
            _node("name", content=b"Phone"),
            _node("retailer_id", content=b"ret-1"),
            _node("description", content=b"desc"),
            _node("price", content=b"100"),
            _node("currency", content=b"USD"),
            _node(
                "media",
                content=[
                    _node(
                        "image",
                        content=[
                            _node("request_image_url", content=b"https://r"),
                            _node("original_image_url", content=b"https://o"),
                        ],
                    )
                ],
            ),
            _node("status_info", content=[_node("status", content=b"APPROVED")]),
        ],
    )
    catalog_node = _node(
        "iq",
        content=[
            _node(
                "product_catalog",
                content=[
                    product,
                    _node("paging", content=[_node("after", content=b"cursor-1")]),
                ],
            )
        ],
    )
    parsed_catalog = business_mod.parse_catalog_node(catalog_node)
    assert parsed_catalog["nextPageCursor"] == "cursor-1"
    assert parsed_catalog["products"][0].id == "p1"
    assert parsed_catalog["products"][0].is_hidden is True

    collections_node = _node(
        "iq",
        content=[
            _node(
                "collections",
                content=[
                    _node(
                        "collection",
                        content=[
                            _node("id", content=b"c1"),
                            _node("name", content=b"Featured"),
                            product,
                            _node("status_info", content=[_node("status", content=b"active"), _node("can_appeal", content=b"true")]),
                        ],
                    )
                ],
            )
        ],
    )
    parsed_collections = business_mod.parse_collections_node(collections_node)
    assert parsed_collections["collections"][0].id == "c1"
    assert parsed_collections["collections"][0].status.can_appeal is True

    order_node = _node(
        "iq",
        content=[
            _node(
                "order",
                content=[
                    _node(
                        "product",
                        content=[
                            _node("id", content=b"p1"),
                            _node("name", content=b"Phone"),
                            _node("price", content=b"100"),
                            _node("currency", content=b"USD"),
                            _node("quantity", content=b"2"),
                            _node("image", content=[_node("url", content=b"https://img")]),
                        ],
                    ),
                    _node("price", content=[_node("total", content=b"200"), _node("currency", content=b"USD")]),
                ],
            )
        ],
    )
    parsed_order = business_mod.parse_order_details_node(order_node)
    assert parsed_order.price.total == 200
    assert parsed_order.products[0].quantity == 2


def test_to_product_node_maps_fields_and_requires_uploaded_urls() -> None:
    model = ProductCreate(
        name="Phone",
        retailerId="ret-1",
        description="desc",
        price=100,
        currency="USD",
        isHidden=True,
        originCountryCode=None,
        images=[{"url": "https://mmg.whatsapp.net/image1"}],
    )
    node = business_mod.to_product_node("p1", model)
    assert node.tag == "product"
    assert node.attrs["is_hidden"] == "true"
    assert node.attrs["compliance_category"] == "COUNTRY_ORIGIN_EXEMPT"

    with pytest.raises(ValueError, match="already be uploaded"):
        business_mod.to_product_node(
            None,
            ProductCreate(
                name="X",
                retailerId=None,
                description="d",
                price=1,
                currency="USD",
                isHidden=None,
                originCountryCode="US",
                images=[{"stream": b"raw"}],
            ),
        )


@pytest.mark.asyncio
async def test_uploading_necessary_images_parallelizes_and_converts_direct_path(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_stream(data: bytes):
        yield data

    async def _fake_get_stream(item: Any) -> dict[str, Any]:
        payload = (str(item.get("id", "x"))).encode("utf-8") if isinstance(item, dict) else b"x"
        return {"stream": _fake_stream(payload), "type": "buffer"}

    monkeypatch.setattr(business_mod, "get_stream", _fake_get_stream)

    in_flight = 0
    max_in_flight = 0
    calls: list[dict[str, Any]] = []

    async def _fake_upload(_path: str, opts: dict[str, Any]) -> dict[str, str]:
        nonlocal in_flight, max_in_flight
        in_flight += 1
        max_in_flight = max(max_in_flight, in_flight)
        calls.append(opts)
        await asyncio.sleep(0.05)
        in_flight -= 1
        return {"directPath": "/mms/product/test"}

    result = await business_mod.uploading_necessary_images(
        [{"id": "1"}, {"id": "2"}, {"url": "https://mmg.whatsapp.net/already"}],
        _fake_upload,
    )

    assert max_in_flight >= 2
    assert len(calls) == 2
    assert all(call["mediaType"] == "product-catalog-image" for call in calls)
    assert result[0]["url"].startswith("https://mmg.whatsapp.net")
    assert result[1]["url"].startswith("https://mmg.whatsapp.net")
    assert result[2]["url"] == "https://mmg.whatsapp.net/already"

