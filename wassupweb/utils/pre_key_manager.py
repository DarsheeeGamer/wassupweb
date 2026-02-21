from __future__ import annotations

import asyncio
from typing import Any


class PreKeyManager:
    """
    Manages pre-key updates/deletions with per-key-type serialization.
    """

    def __init__(self, store: Any, logger: Any) -> None:
        self._store = store
        self._logger = logger
        self._locks: dict[str, asyncio.Lock] = {}

    def _get_lock(self, key_type: str) -> asyncio.Lock:
        lock = self._locks.get(key_type)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[key_type] = lock
        return lock

    async def process_operations(
        self,
        data: dict[str, dict[str, Any | None]],
        key_type: str,
        transaction_cache: dict[str, dict[str, Any | None]],
        mutations: dict[str, dict[str, Any | None]],
        is_in_transaction: bool,
    ) -> None:
        key_data = data.get(key_type)
        if not key_data:
            return

        lock = self._get_lock(key_type)
        async with lock:
            transaction_cache.setdefault(key_type, {})
            mutations.setdefault(key_type, {})

            deletions: list[str] = []
            updates: dict[str, Any] = {}

            for key_id, value in key_data.items():
                if value is None:
                    deletions.append(key_id)
                else:
                    updates[key_id] = value

            if updates:
                transaction_cache[key_type].update(updates)
                mutations[key_type].update(updates)

            if deletions:
                await self._process_deletions(
                    key_type,
                    deletions,
                    transaction_cache,
                    mutations,
                    is_in_transaction,
                )

    async def _process_deletions(
        self,
        key_type: str,
        ids: list[str],
        transaction_cache: dict[str, dict[str, Any | None]],
        mutations: dict[str, dict[str, Any | None]],
        is_in_transaction: bool,
    ) -> None:
        if is_in_transaction:
            for key_id in ids:
                if transaction_cache.get(key_type, {}).get(key_id) is not None:
                    transaction_cache[key_type][key_id] = None
                    mutations[key_type][key_id] = None
                else:
                    self._warn(f"Skipping deletion of non-existent {key_type} in transaction: {key_id}")
            return

        existing = await self._store.get(key_type, ids)
        for key_id in ids:
            if existing.get(key_id) is not None:
                transaction_cache[key_type][key_id] = None
                mutations[key_type][key_id] = None
            else:
                self._warn(f"Skipping deletion of non-existent {key_type}: {key_id}")

    async def validate_deletions(self, data: dict[str, dict[str, Any | None]], key_type: str) -> None:
        key_data = data.get(key_type)
        if not key_data:
            return

        lock = self._get_lock(key_type)
        async with lock:
            deletion_ids = [item_id for item_id, value in key_data.items() if value is None]
            if not deletion_ids:
                return

            existing = await self._store.get(key_type, deletion_ids)
            for key_id in deletion_ids:
                if existing.get(key_id) is None:
                    self._warn(f"Skipping deletion of non-existent {key_type}: {key_id}")
                    key_data.pop(key_id, None)

    def _warn(self, message: str) -> None:
        if hasattr(self._logger, "warn"):
            self._logger.warn(message)
        elif hasattr(self._logger, "warning"):
            self._logger.warning(message)


__all__ = ["PreKeyManager"]
