from __future__ import annotations

from typing import Any

import pytest

from wassupweb.signal.lid_mapping import LIDMappingStore


class _Keys:
    def __init__(self, mapping: dict[str, str] | None = None) -> None:
        self.mapping = mapping or {}

    async def get(self, _key_type: str, ids: list[str]) -> dict[str, str]:
        return {item_id: self.mapping[item_id] for item_id in ids if item_id in self.mapping}

    async def set(self, _data: dict[str, dict[str, Any]]) -> None:
        return

    async def transaction(self, work: Any, _key: str) -> Any:
        return await work()


class _Logger:
    def warning(self, *_args: object, **_kwargs: object) -> None:
        return


@pytest.mark.asyncio
async def test_get_pn_for_lid_with_hosted_device_maps_back_to_pn_with_same_device() -> None:
    lid_with_hosted_device = "12345:99@lid"
    keys = _Keys({"12345_reverse": "54321"})
    store = LIDMappingStore(keys, _Logger(), None)

    result = await store.get_pn_for_lid(lid_with_hosted_device)

    assert result == "54321:99@s.whatsapp.net"


@pytest.mark.asyncio
async def test_get_pn_for_lid_returns_none_when_mapping_is_missing() -> None:
    store = LIDMappingStore(_Keys({}), _Logger(), None)

    result = await store.get_pn_for_lid("nonexistent@lid")

    assert result is None


@pytest.mark.asyncio
async def test_lid_mapping_store_exposes_baileys_camelcase_method_aliases() -> None:
    keys = _Keys({"54321": "12345", "12345_reverse": "54321"})
    store = LIDMappingStore(keys, _Logger(), None)

    assert callable(getattr(store, "storeLIDPNMappings", None))
    assert callable(getattr(store, "getLIDForPN", None))
    assert callable(getattr(store, "getLIDsForPNs", None))
    assert callable(getattr(store, "getPNForLID", None))
    assert callable(getattr(store, "getPNsForLIDs", None))

    assert await store.getLIDForPN("54321@s.whatsapp.net") == "12345@lid"
    assert await store.getPNForLID("12345:99@lid") == "54321:99@s.whatsapp.net"
