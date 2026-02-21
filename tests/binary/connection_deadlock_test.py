from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import pytest

from wassupweb.defaults import DEFAULT_CONNECTION_CONFIG
from wassupweb.socket.index import make_wa_socket
from wassupweb.types.socket import SocketConfig
from wassupweb.utils.event_bus import EventBus


@dataclass
class _DummyTransport:
    async def connect(self) -> None:
        return

    async def disconnect(self) -> None:
        return

    async def send(self, _payload: bytes) -> None:
        return

    async def recv(self) -> bytes:
        await asyncio.sleep(3600)
        return b""


@pytest.mark.asyncio
async def test_should_not_deadlock_when_history_sync_is_disabled() -> None:
    config_data = dict(DEFAULT_CONNECTION_CONFIG)
    config_data["should_sync_history_message"] = lambda _msg: False
    config = SocketConfig.model_validate(config_data)

    ev = EventBus()
    sock = make_wa_socket(config, transport=_DummyTransport(), event_bus=ev)

    calls: list[dict[str, Any]] = []

    async def _listener(payload: dict[str, Any]) -> None:
        calls.append(payload)

    sock.ev.on("messages.upsert", _listener)

    sock.ev.buffer()
    await sock.ev.emit("connection.update", {"receivedPendingNotifications": True})
    sock.ev.flush()

    regular_message = {
        "key": {"remoteJid": "1234567890@s.whatsapp.net", "fromMe": False, "id": "REGULAR_MSG_1"},
        "messageTimestamp": 1,
        "message": {"conversation": "Hello, world!"},
    }
    await sock.ev.emit("messages.upsert", {"messages": [regular_message], "type": "notify"})
    await asyncio.sleep(0.05)

    assert len(calls) == 1
    assert calls[0]["type"] == "notify"
    assert calls[0]["messages"][0]["key"] == regular_message["key"]
