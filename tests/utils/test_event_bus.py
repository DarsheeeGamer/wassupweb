from __future__ import annotations

import asyncio

import pytest

from wassupweb.utils.event_bus import EventBus


@pytest.mark.asyncio
async def test_event_bus_buffer_and_flush() -> None:
    ev = EventBus()
    seen: list[int] = []

    async def _handler(payload: int) -> None:
        seen.append(payload)

    ev.on("x", _handler)
    ev.buffer()
    await ev.emit("x", 1)
    await ev.emit("x", 2)
    assert seen == []
    assert ev.flush() is True
    await asyncio.sleep(0)
    assert seen == [1, 2]


@pytest.mark.asyncio
async def test_event_bus_create_buffered_function() -> None:
    ev = EventBus()
    seen: list[int] = []

    async def _handler(payload: int) -> None:
        seen.append(payload)

    ev.on("x", _handler)

    async def _work() -> int:
        await ev.emit("x", 5)
        await ev.emit("x", 6)
        assert seen == []
        return 42

    wrapped = ev.create_buffered_function(_work)
    result = await wrapped()
    assert result == 42
    await asyncio.sleep(0)
    assert seen == [5, 6]
