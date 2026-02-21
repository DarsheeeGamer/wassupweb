from __future__ import annotations

import asyncio
import inspect
from collections import defaultdict
from collections.abc import Awaitable, Callable
from typing import Any

from ..abc import EventBusABC, EventHandler


class EventBus(EventBusABC):
    def __init__(self) -> None:
        self._handlers: dict[str, list[EventHandler]] = defaultdict(list)
        self._lock = asyncio.Lock()
        self._is_buffering = False
        self._buffer_depth = 0
        self._buffered_events: list[tuple[str, Any]] = []

    def on(self, event: str, handler: EventHandler) -> None:
        self._handlers[event].append(handler)

    def off(self, event: str, handler: EventHandler) -> None:
        handlers = self._handlers.get(event)
        if not handlers:
            return
        try:
            handlers.remove(handler)
        except ValueError:
            return

    def remove_all_listeners(self, event: str) -> None:
        self._handlers.pop(event, None)

    async def emit(self, event: str, payload: Any) -> None:
        if self._is_buffering:
            self._buffered_events.append((event, payload))
            return
        await self._emit_now(event, payload)

    async def _emit_now(self, event: str, payload: Any) -> None:
        async with self._lock:
            handlers = list(self._handlers.get(event, []))

        for handler in handlers:
            result = handler(payload)
            if inspect.isawaitable(result):
                await result

    def buffer(self) -> None:
        self._buffer_depth += 1
        self._is_buffering = True

    def flush(self) -> bool:
        if self._buffer_depth > 0:
            self._buffer_depth -= 1
        if self._buffer_depth > 0:
            return False

        events = list(self._buffered_events)
        self._buffered_events.clear()
        self._is_buffering = False
        if not events:
            return False

        async def _run() -> None:
            for event, payload in events:
                await self._emit_now(event, payload)

        asyncio.create_task(_run())
        return True

    def create_buffered_function(self, work: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
        async def _wrapper(*args: Any, **kwargs: Any) -> Any:
            self.buffer()
            try:
                return await work(*args, **kwargs)
            finally:
                self.flush()

        return _wrapper

    # camelCase aliases for parity
    createBufferedFunction = create_buffered_function
    removeAllListeners = remove_all_listeners
