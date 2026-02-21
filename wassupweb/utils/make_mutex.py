from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

T = TypeVar("T")


class Mutex:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()

    async def mutex(self, code: Callable[[], Awaitable[T] | T]) -> T:
        async with self._lock:
            result = code()
            if asyncio.iscoroutine(result):
                return await result
            return result


class KeyedMutex:
    def __init__(self) -> None:
        self._locks: dict[str, asyncio.Lock] = {}
        self._ref_count: defaultdict[str, int] = defaultdict(int)
        self._guard = asyncio.Lock()

    async def _get_lock(self, key: str) -> asyncio.Lock:
        async with self._guard:
            if key not in self._locks:
                self._locks[key] = asyncio.Lock()
            self._ref_count[key] += 1
            return self._locks[key]

    async def _release_lock(self, key: str) -> None:
        async with self._guard:
            self._ref_count[key] -= 1
            if self._ref_count[key] <= 0:
                self._ref_count.pop(key, None)
                self._locks.pop(key, None)

    async def mutex(self, key: str, task: Callable[[], Awaitable[T] | T]) -> T:
        lock = await self._get_lock(key)
        try:
            async with lock:
                result = task()
                if asyncio.iscoroutine(result):
                    return await result
                return result
        finally:
            await self._release_lock(key)


def make_mutex() -> Mutex:
    return Mutex()


def make_keyed_mutex() -> KeyedMutex:
    return KeyedMutex()


# camelCase aliases
makeMutex = make_mutex
makeKeyedMutex = make_keyed_mutex
