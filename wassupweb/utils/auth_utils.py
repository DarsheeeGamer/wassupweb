from __future__ import annotations

import asyncio
import base64
import json
import secrets
from contextvars import ContextVar
from typing import Any, Awaitable, Callable

from pydantic import BaseModel

from ..defaults import DEFAULT_CACHE_TTLS
from ..types.auth import AuthenticationCreds, AuthenticationState, SignalDataSet, SignalKeyStore
from .crypto import Curve, generate_registration_id, signed_key_pair
from .pre_key_manager import PreKeyManager


def _json_default(value: Any) -> Any:
    if isinstance(value, (bytes, bytearray)):
        return {"__type__": "bytes", "data": base64.b64encode(bytes(value)).decode("ascii")}
    if isinstance(value, BaseModel):
        return value.model_dump(by_alias=False)
    raise TypeError(f"Object is not JSON serializable: {type(value)!r}")


class InMemorySignalKeyStore(SignalKeyStore):
    def __init__(self) -> None:
        self._data: dict[str, dict[str, Any]] = {}
        self._lock = asyncio.Lock()

    async def get(self, key_type: str, ids: list[str]) -> dict[str, Any]:
        async with self._lock:
            source = self._data.get(key_type, {})
            return {item_id: source[item_id] for item_id in ids if item_id in source}

    async def set(self, data: SignalDataSet) -> None:
        async with self._lock:
            for key_type, values in data.items():
                if key_type not in self._data:
                    self._data[key_type] = {}
                for item_id, value in values.items():
                    if value is None:
                        self._data[key_type].pop(item_id, None)
                    else:
                        self._data[key_type][item_id] = value

    async def clear(self) -> None:
        async with self._lock:
            self._data.clear()


def make_cacheable_signal_key_store(
    store: SignalKeyStore,
    ttl_seconds: int = DEFAULT_CACHE_TTLS["signal_store"],
    logger: Any = None,
) -> SignalKeyStore:
    cache: dict[str, tuple[Any, float]] = {}
    lock = asyncio.Lock()

    async def _prune() -> None:
        now = asyncio.get_running_loop().time()
        expired = [k for k, (_, exp) in cache.items() if exp <= now]
        for key in expired:
            cache.pop(key, None)

    class CacheableStore(SignalKeyStore):
        async def get(self, key_type: str, ids: list[str]) -> dict[str, Any]:
            async with lock:
                await _prune()
                result: dict[str, Any] = {}
                missing: list[str] = []
                for item_id in ids:
                    key = f"{key_type}.{item_id}"
                    if key in cache:
                        result[item_id] = cache[key][0]
                    else:
                        missing.append(item_id)

            if missing:
                if logger and hasattr(logger, "trace"):
                    logger.trace({"items": len(missing)}, "loading from store")
                fetched = await store.get(key_type, missing)
                async with lock:
                    expiry = asyncio.get_running_loop().time() + ttl_seconds
                    for item_id, value in fetched.items():
                        cache[f"{key_type}.{item_id}"] = (value, expiry)
                        result[item_id] = value

            return result

        async def set(self, data: SignalDataSet) -> None:
            async with lock:
                expiry = asyncio.get_running_loop().time() + ttl_seconds
                keys = 0
                for key_type, values in data.items():
                    for item_id, value in values.items():
                        cache[f"{key_type}.{item_id}"] = (value, expiry)
                        keys += 1
                if logger and hasattr(logger, "trace"):
                    logger.trace({"keys": keys}, "updated cache")
            await store.set(data)

        async def clear(self) -> None:
            async with lock:
                cache.clear()
            await store.clear()

    return CacheableStore()


def add_transaction_capability(
    store: SignalKeyStore,
    max_commit_retries: int,
    delay_between_tries_ms: int,
    logger: Any = None,
) -> SignalKeyStore:
    tx_context: ContextVar[dict[str, Any] | None] = ContextVar("tx_context", default=None)
    locks: dict[str, asyncio.Lock] = {}
    write_locks: dict[str, asyncio.Lock] = {}
    pre_key_manager = PreKeyManager(store, logger)

    def _lock_for(key: str) -> asyncio.Lock:
        if key not in locks:
            locks[key] = asyncio.Lock()
        return locks[key]

    def _write_lock_for(key_type: str) -> asyncio.Lock:
        if key_type not in write_locks:
            write_locks[key_type] = asyncio.Lock()
        return write_locks[key_type]

    def _log(level: str, payload: Any, message: str | None = None) -> None:
        if logger is None:
            return
        fn = getattr(logger, level, None)
        if not callable(fn):
            return
        if message is None:
            fn(payload)
        else:
            fn(payload, message)

    class TransactionStore(SignalKeyStore):
        async def get(self, key_type: str, ids: list[str]) -> dict[str, Any]:
            ctx = tx_context.get()
            if not ctx:
                return await store.get(key_type, ids)
            cached = ctx.setdefault("cache", {}).setdefault(key_type, {})
            missing = [item_id for item_id in ids if item_id not in cached]
            if missing:
                ctx["db_queries"] = int(ctx.get("db_queries") or 0) + 1
                _log("trace", {"type": key_type, "count": len(missing)}, "fetching missing keys in transaction")
                async with _lock_for(f"read:{key_type}"):
                    fetched = await store.get(key_type, missing)
                cached.update(fetched)
            return {item_id: cached[item_id] for item_id in ids if item_id in cached}

        async def set(self, data: SignalDataSet) -> None:
            ctx = tx_context.get()
            if not ctx:
                normalized: SignalDataSet = {}
                for key_type, values in data.items():
                    copy_values: dict[str, Any | None] = dict(values)
                    if key_type == "pre-key":
                        await pre_key_manager.validate_deletions({"pre-key": copy_values}, "pre-key")
                    if copy_values:
                        normalized[key_type] = copy_values

                if not normalized:
                    return

                async def _set_one(key_type: str, values: dict[str, Any | None]) -> None:
                    async with _write_lock_for(key_type):
                        await store.set({key_type: values})

                await asyncio.gather(*[_set_one(key_type, values) for key_type, values in normalized.items()])
                return
            mutations = ctx.setdefault("mutations", {})
            cache = ctx.setdefault("cache", {})
            _log("trace", {"types": list(data.keys())}, "caching in transaction")
            for key_type, values in data.items():
                cache.setdefault(key_type, {})
                mutations.setdefault(key_type, {})
                if key_type == "pre-key":
                    await pre_key_manager.process_operations(
                        {key_type: dict(values)},
                        key_type,
                        cache,
                        mutations,
                        True,
                    )
                else:
                    cache[key_type].update(values)
                    mutations[key_type].update(values)

        async def clear(self) -> None:
            await store.clear()

        def is_in_transaction(self) -> bool:
            return tx_context.get() is not None

        async def transaction(self, exec_fn: Callable[[], Awaitable[Any]], key: str) -> Any:
            if self.is_in_transaction():
                _log("trace", "reusing existing transaction context")
                return await exec_fn()

            lock = _lock_for(key)
            async with lock:
                token = tx_context.set({"cache": {}, "mutations": {}, "db_queries": 0})
                _log("trace", "entering transaction")
                try:
                    result = await exec_fn()
                    ctx = tx_context.get() or {"mutations": {}}
                    mutations = ctx.get("mutations") or {}
                    if not mutations:
                        _log("trace", "no mutations in transaction")
                        return result
                    for attempt in range(max_commit_retries):
                        try:
                            await store.set(mutations)
                            _log("trace", {"mutationCount": len(mutations)}, "committed transaction")
                            break
                        except Exception as error:
                            if attempt == max_commit_retries - 1:
                                raise
                            retries_left = max_commit_retries - attempt - 1
                            _log("warn", f"failed to commit mutations, retries left={retries_left}")
                            await asyncio.sleep(delay_between_tries_ms / 1000.0)
                    _log("trace", {"dbQueries": int(ctx.get('db_queries') or 0)}, "transaction completed")
                    return result
                except Exception as error:
                    _log("error", {"error": str(error)}, "transaction failed, rolling back")
                    raise
                finally:
                    tx_context.reset(token)

    return TransactionStore()


def init_auth_creds() -> AuthenticationCreds:
    identity_key = Curve.generate_key_pair()
    return AuthenticationCreds(
        noise_key=Curve.generate_key_pair(),
        pairing_ephemeral_key_pair=Curve.generate_key_pair(),
        signed_identity_key=identity_key,
        signed_pre_key=signed_key_pair(identity_key, 1),
        registration_id=generate_registration_id(),
        adv_secret_key=base64.b64encode(secrets.token_bytes(32)).decode("ascii"),
    )


def auth_state_to_json(state: AuthenticationState) -> str:
    return json.dumps(state.model_dump(by_alias=False), default=_json_default)
