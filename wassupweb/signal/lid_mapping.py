from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from ..wabinary import (
    is_hosted_pn_user,
    is_lid_user,
    is_pn_user,
    jid_decode,
    jid_normalized_user,
)


@dataclass
class _CacheEntry:
    value: str
    expires_at: float


class LIDMappingStore:
    def __init__(
        self,
        keys: Any,
        logger: Any,
        pn_to_lid_func: Callable[[list[str]], Awaitable[list[dict[str, str]] | None]] | None = None,
        *,
        ttl_ms: int = 3 * 24 * 60 * 60 * 1000,
    ) -> None:
        self.keys = keys
        self.logger = logger
        self.pn_to_lid_func = pn_to_lid_func
        self._ttl_ms = ttl_ms
        self._mapping_cache: dict[str, _CacheEntry] = {}
        self._inflight_lid_lookups: dict[str, asyncio.Future[list[dict[str, str]] | None]] = {}
        self._inflight_pn_lookups: dict[str, asyncio.Future[list[dict[str, str]] | None]] = {}
        self._lock = asyncio.Lock()

    def _cache_get(self, key: str) -> str | None:
        entry = self._mapping_cache.get(key)
        now = time.time() * 1000
        if not entry or entry.expires_at <= now:
            if entry:
                self._mapping_cache.pop(key, None)
            return None
        entry.expires_at = now + self._ttl_ms
        return entry.value

    def _cache_set(self, key: str, value: str) -> None:
        self._mapping_cache[key] = _CacheEntry(value=value, expires_at=(time.time() * 1000) + self._ttl_ms)

    async def store_lid_pn_mappings(self, pairs: list[dict[str, str]]) -> None:
        if not pairs:
            return
        validated_pairs: list[tuple[str, str]] = []
        for pair in pairs:
            lid = pair.get("lid")
            pn = pair.get("pn")
            if not lid or not pn:
                continue
            if not ((is_lid_user(lid) and is_pn_user(pn)) or (is_pn_user(lid) and is_lid_user(pn))):
                if self.logger:
                    self.logger.warning("Invalid LID-PN mapping", extra={"lid": lid, "pn": pn})
                continue
            lid_decoded = jid_decode(lid) or {}
            pn_decoded = jid_decode(pn) or {}
            lid_user = lid_decoded.get("user")
            pn_user = pn_decoded.get("user")
            if lid_user and pn_user:
                validated_pairs.append((pn_user, lid_user))
        if not validated_pairs:
            return

        cache_misses: set[str] = set()
        existing: dict[str, str] = {}
        for pn_user, _ in validated_pairs:
            cached = self._cache_get(f"pn:{pn_user}")
            if cached:
                existing[pn_user] = cached
            else:
                cache_misses.add(pn_user)

        if cache_misses:
            stored = await self.keys.get("lid-mapping", list(cache_misses))
            for pn_user in cache_misses:
                existing_lid = stored.get(pn_user)
                if existing_lid:
                    existing[pn_user] = existing_lid
                    self._cache_set(f"pn:{pn_user}", existing_lid)
                    self._cache_set(f"lid:{existing_lid}", pn_user)

        pair_map: dict[str, str] = {}
        for pn_user, lid_user in validated_pairs:
            if existing.get(pn_user) == lid_user:
                continue
            pair_map[pn_user] = lid_user
        if not pair_map:
            return

        batch_data: dict[str, str] = {}
        for pn_user, lid_user in pair_map.items():
            batch_data[pn_user] = lid_user
            batch_data[f"{lid_user}_reverse"] = pn_user

        async def _store() -> None:
            await self.keys.set({"lid-mapping": batch_data})

        if hasattr(self.keys, "transaction"):
            await self.keys.transaction(_store, "lid-mapping")
        else:
            await _store()

        for pn_user, lid_user in pair_map.items():
            self._cache_set(f"pn:{pn_user}", lid_user)
            self._cache_set(f"lid:{lid_user}", pn_user)

    async def get_lid_for_pn(self, pn: str) -> str | None:
        pairs = await self.get_lids_for_pns([pn])
        return pairs[0]["lid"] if pairs else None

    async def get_lids_for_pns(self, pns: list[str]) -> list[dict[str, str]] | None:
        if not pns:
            return None
        sorted_pns = sorted(set(pns))
        cache_key = ",".join(sorted_pns)
        async with self._lock:
            inflight = self._inflight_lid_lookups.get(cache_key)
            if inflight:
                return await inflight
            loop = asyncio.get_running_loop()
            future: asyncio.Future[list[dict[str, str]] | None] = loop.create_future()
            self._inflight_lid_lookups[cache_key] = future
        try:
            result = await self._get_lids_for_pns_impl(sorted_pns)
            future.set_result(result)
            return result
        except Exception as err:
            future.set_exception(err)
            raise
        finally:
            async with self._lock:
                self._inflight_lid_lookups.pop(cache_key, None)

    async def _get_lids_for_pns_impl(self, pns: list[str]) -> list[dict[str, str]] | None:
        usync_fetch: dict[str, list[int]] = {}
        successful_pairs: dict[str, dict[str, str]] = {}
        pending: list[tuple[str, str, dict[str, Any]]] = []

        def _add_resolved(pn: str, decoded: dict[str, Any], lid_user: str) -> None:
            pn_device = int(decoded.get("device") or 0)
            server = "hosted.lid" if decoded.get("server") == "hosted" else "lid"
            device_specific_lid = f"{lid_user}{f':{pn_device}' if pn_device else ''}@{server}"
            successful_pairs[pn] = {"lid": device_specific_lid, "pn": pn}

        for pn in pns:
            if not (is_pn_user(pn) or is_hosted_pn_user(pn)):
                continue
            decoded = jid_decode(pn)
            if not decoded:
                continue
            pn_user = decoded.get("user")
            if not pn_user:
                continue
            cached = self._cache_get(f"pn:{pn_user}")
            if cached:
                _add_resolved(pn, decoded, cached)
                continue
            pending.append((pn, pn_user, decoded))

        if pending:
            pn_users = sorted({pn_user for _, pn_user, _ in pending})
            stored = await self.keys.get("lid-mapping", pn_users)
            for pn_user in pn_users:
                lid_user = stored.get(pn_user)
                if lid_user:
                    self._cache_set(f"pn:{pn_user}", lid_user)
                    self._cache_set(f"lid:{lid_user}", pn_user)

            for pn, pn_user, decoded in pending:
                cached = self._cache_get(f"pn:{pn_user}")
                if cached:
                    _add_resolved(pn, decoded, cached)
                else:
                    device = int(decoded.get("device") or 0)
                    normalized_pn = jid_normalized_user(pn)
                    if is_hosted_pn_user(normalized_pn):
                        normalized_pn = f"{pn_user}@s.whatsapp.net"
                    usync_fetch.setdefault(normalized_pn, []).append(device)

        if usync_fetch and self.pn_to_lid_func:
            result = await self.pn_to_lid_func(list(usync_fetch.keys()))
            if result:
                await self.store_lid_pn_mappings(result)
                for pair in result:
                    pn_decoded = jid_decode(pair["pn"]) or {}
                    pn_user = pn_decoded.get("user")
                    lid_user = (jid_decode(pair["lid"]) or {}).get("user")
                    if not pn_user or not lid_user:
                        continue
                    for device in usync_fetch.get(pair["pn"], []):
                        lid_server = "hosted.lid" if device == 99 else "lid"
                        pn_server = "hosted" if device == 99 else "s.whatsapp.net"
                        device_lid = f"{lid_user}{f':{device}' if device else ''}@{lid_server}"
                        device_pn = f"{pn_user}{f':{device}' if device else ''}@{pn_server}"
                        successful_pairs[device_pn] = {"lid": device_lid, "pn": device_pn}

        return list(successful_pairs.values()) if successful_pairs else None

    async def get_pn_for_lid(self, lid: str) -> str | None:
        pairs = await self.get_pns_for_lids([lid])
        return pairs[0]["pn"] if pairs else None

    async def get_pns_for_lids(self, lids: list[str]) -> list[dict[str, str]] | None:
        if not lids:
            return None
        sorted_lids = sorted(set(lids))
        cache_key = ",".join(sorted_lids)
        async with self._lock:
            inflight = self._inflight_pn_lookups.get(cache_key)
            if inflight:
                return await inflight
            loop = asyncio.get_running_loop()
            future: asyncio.Future[list[dict[str, str]] | None] = loop.create_future()
            self._inflight_pn_lookups[cache_key] = future
        try:
            result = await self._get_pns_for_lids_impl(sorted_lids)
            future.set_result(result)
            return result
        except Exception as err:
            future.set_exception(err)
            raise
        finally:
            async with self._lock:
                self._inflight_pn_lookups.pop(cache_key, None)

    async def _get_pns_for_lids_impl(self, lids: list[str]) -> list[dict[str, str]] | None:
        successful_pairs: dict[str, dict[str, str]] = {}
        pending: list[tuple[str, str, dict[str, Any]]] = []

        def _add_resolved(lid: str, decoded: dict[str, Any], pn_user: str) -> None:
            lid_device = int(decoded.get("device") or 0)
            domain_type = decoded.get("domainType")
            pn_server = "hosted" if domain_type == 129 else "s.whatsapp.net"
            pn_jid = f"{pn_user}:{lid_device}@{pn_server}"
            successful_pairs[lid] = {"lid": lid, "pn": pn_jid}

        for lid in lids:
            if not is_lid_user(lid):
                continue
            decoded = jid_decode(lid)
            if not decoded:
                continue
            lid_user = decoded.get("user")
            if not lid_user:
                continue
            cached = self._cache_get(f"lid:{lid_user}")
            if cached:
                _add_resolved(lid, decoded, cached)
                continue
            pending.append((lid, lid_user, decoded))

        if pending:
            reverse_keys = sorted({f"{lid_user}_reverse" for _, lid_user, _ in pending})
            stored = await self.keys.get("lid-mapping", reverse_keys)
            for lid, lid_user, decoded in pending:
                pn_user = self._cache_get(f"lid:{lid_user}")
                if not pn_user:
                    pn_user = stored.get(f"{lid_user}_reverse")
                    if pn_user:
                        self._cache_set(f"lid:{lid_user}", pn_user)
                        self._cache_set(f"pn:{pn_user}", lid_user)
                if pn_user:
                    _add_resolved(lid, decoded, pn_user)

        return list(successful_pairs.values()) if successful_pairs else None


# class-level camelCase aliases for Baileys API parity
LIDMappingStore.storeLIDPNMappings = LIDMappingStore.store_lid_pn_mappings
LIDMappingStore.getLIDForPN = LIDMappingStore.get_lid_for_pn
LIDMappingStore.getLIDsForPNs = LIDMappingStore.get_lids_for_pns
LIDMappingStore.getPNForLID = LIDMappingStore.get_pn_for_lid
LIDMappingStore.getPNsForLIDs = LIDMappingStore.get_pns_for_lids

# camelCase aliases for parity
storeLIDPNMappings = LIDMappingStore.store_lid_pn_mappings
getLIDForPN = LIDMappingStore.get_lid_for_pn
getLIDsForPNs = LIDMappingStore.get_lids_for_pns
getPNForLID = LIDMappingStore.get_pn_for_lid
getPNsForLIDs = LIDMappingStore.get_pns_for_lids
