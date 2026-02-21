from __future__ import annotations

from typing import Any

import pytest

from wassupweb.utils.auth_utils import add_transaction_capability, make_cacheable_signal_key_store


class _Store:
    def __init__(self) -> None:
        self.data: dict[str, dict[str, Any]] = {}
        self.get_calls: list[tuple[str, list[str]]] = []
        self.set_calls: list[dict[str, dict[str, Any | None]]] = []
        self.fail_set_attempts = 0

    async def get(self, key_type: str, ids: list[str]) -> dict[str, Any]:
        self.get_calls.append((key_type, list(ids)))
        source = self.data.get(key_type, {})
        return {item_id: source[item_id] for item_id in ids if item_id in source}

    async def set(self, data: dict[str, dict[str, Any | None]]) -> None:
        self.set_calls.append(data)
        if self.fail_set_attempts > 0:
            self.fail_set_attempts -= 1
            raise RuntimeError("commit failed")
        for key_type, values in data.items():
            target = self.data.setdefault(key_type, {})
            for item_id, value in values.items():
                if value is None:
                    target.pop(item_id, None)
                else:
                    target[item_id] = value

    async def clear(self) -> None:
        self.data.clear()


@pytest.mark.asyncio
async def test_make_cacheable_signal_key_store_caches_reads() -> None:
    store = _Store()
    store.data["session"] = {"a": {"k": 1}}
    cached = make_cacheable_signal_key_store(store, ttl_seconds=60)

    first = await cached.get("session", ["a"])
    second = await cached.get("session", ["a"])

    assert first == {"a": {"k": 1}}
    assert second == {"a": {"k": 1}}
    assert store.get_calls == [("session", ["a"])]


@pytest.mark.asyncio
async def test_add_transaction_capability_validates_pre_key_deletions_outside_tx() -> None:
    store = _Store()
    tx_store = add_transaction_capability(store, max_commit_retries=2, delay_between_tries_ms=1)

    await tx_store.set({"pre-key": {"missing": None, "pk1": {"key": 1}}, "session": {"s1": {"v": 9}}})

    assert store.data["pre-key"] == {"pk1": {"key": 1}}
    assert store.data["session"] == {"s1": {"v": 9}}
    # missing deletion should be filtered, only concrete pre-key insert is written
    assert any(call.get("pre-key") == {"pk1": {"key": 1}} for call in store.set_calls)


@pytest.mark.asyncio
async def test_transaction_get_uses_context_cache_for_repeated_reads() -> None:
    store = _Store()
    store.data["session"] = {"x": {"v": 1}}
    tx_store = add_transaction_capability(store, max_commit_retries=2, delay_between_tries_ms=1)

    async def _work() -> None:
        first = await tx_store.get("session", ["x"])
        second = await tx_store.get("session", ["x"])
        assert first == {"x": {"v": 1}}
        assert second == {"x": {"v": 1}}

    await tx_store.transaction(_work, "jid-1")
    assert store.get_calls == [("session", ["x"])]


@pytest.mark.asyncio
async def test_transaction_retries_commit_until_success() -> None:
    store = _Store()
    store.fail_set_attempts = 1
    tx_store = add_transaction_capability(store, max_commit_retries=3, delay_between_tries_ms=1)

    async def _work() -> str:
        await tx_store.set({"session": {"id1": {"v": 7}}})
        return "ok"

    out = await tx_store.transaction(_work, "jid-2")

    assert out == "ok"
    assert store.data["session"] == {"id1": {"v": 7}}
    assert len(store.set_calls) == 2


@pytest.mark.asyncio
async def test_transaction_pre_key_delete_after_cached_read_commits_mutation() -> None:
    store = _Store()
    store.data["pre-key"] = {"p1": {"k": 1}}
    tx_store = add_transaction_capability(store, max_commit_retries=2, delay_between_tries_ms=1)

    async def _work() -> None:
        loaded = await tx_store.get("pre-key", ["p1"])
        assert loaded == {"p1": {"k": 1}}
        await tx_store.set({"pre-key": {"p1": None}})

    await tx_store.transaction(_work, "jid-3")
    assert "p1" not in store.data.get("pre-key", {})

