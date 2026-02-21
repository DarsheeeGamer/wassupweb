from __future__ import annotations

import asyncio
from typing import Any

import pytest

from wassupweb.socket.client import WASocketClient
from wassupweb.types.identity import JidKind
from wassupweb.types.socket import SocketConfig


class _Transport:
    is_open = False

    async def connect(self) -> None:
        return

    async def disconnect(self) -> None:
        return

    async def send(self, _payload: bytes) -> None:
        return

    async def recv(self) -> bytes:
        raise RuntimeError("transport is not connected")


class _Bus:
    def __init__(self) -> None:
        self.handlers: dict[str, list[Any]] = {}
        self.events: list[tuple[str, Any]] = []

    def on(self, event: str, handler: Any) -> None:
        self.handlers.setdefault(event, []).append(handler)

    def off(self, event: str, handler: Any) -> None:
        handlers = self.handlers.get(event) or []
        if handler in handlers:
            handlers.remove(handler)

    async def emit(self, event: str, payload: Any) -> None:
        self.events.append((event, payload))
        for handler in list(self.handlers.get(event, [])):
            result = handler(payload)
            if asyncio.iscoroutine(result):
                await result


@pytest.mark.asyncio
async def test_wait_for_timeout_cleans_listener() -> None:
    bus = _Bus()
    client = WASocketClient(config=SocketConfig(default_query_timeout_ms=1), transport=_Transport(), ev=bus)

    with pytest.raises(asyncio.TimeoutError):
        await client.wait_for("x", timeout_ms=1)

    assert bus.handlers.get("x", []) == []


@pytest.mark.asyncio
async def test_wait_for_success_cleans_listener() -> None:
    bus = _Bus()
    client = WASocketClient(config=SocketConfig(default_query_timeout_ms=50), transport=_Transport(), ev=bus)

    task = asyncio.create_task(client.wait_for("x", timeout_ms=100))
    await asyncio.sleep(0)
    await bus.emit("x", {"ok": True})

    assert await task == {"ok": True}
    assert bus.handlers.get("x", []) == []


@pytest.mark.asyncio
async def test_wait_for_supports_async_predicate() -> None:
    bus = _Bus()
    client = WASocketClient(config=SocketConfig(default_query_timeout_ms=50), transport=_Transport(), ev=bus)

    async def _predicate(payload: dict[str, Any]) -> bool:
        await asyncio.sleep(0)
        return payload.get("match") is True

    task = asyncio.create_task(client.wait_for("x", predicate=_predicate, timeout_ms=100))
    await asyncio.sleep(0)
    await bus.emit("x", {"match": False})
    await bus.emit("x", {"match": True})

    assert await task == {"match": True}
    assert bus.handlers.get("x", []) == []


@pytest.mark.asyncio
async def test_recv_loop_emits_close_when_transport_is_closed() -> None:
    bus = _Bus()
    client = WASocketClient(config=SocketConfig(), transport=_Transport(), ev=bus)

    await asyncio.wait_for(client._recv_loop(), timeout=0.2)

    event_names = [name for name, _ in bus.events]
    assert "error" in event_names
    assert "connection.update" in event_names


def test_client_identity_aliases_match_snake_case_helpers() -> None:
    bus = _Bus()
    client = WASocketClient(config=SocketConfig(), transport=_Transport(), ev=bus)

    via_snake = client.resolve_user("5511999999999@s.whatsapp.net")
    via_camel = client.resolveUser("5511999999999@s.whatsapp.net")
    assert via_camel == via_snake

    ref = client.resolveUserRef("5511999999999@s.whatsapp.net")
    assert ref.jid == "5511999999999@s.whatsapp.net"

    merged = client.linkIdentity("5511999999999@s.whatsapp.net", "991122@lid")
    assert merged["pnJid"] == "5511999999999@s.whatsapp.net"
    assert merged["lidJid"] == "991122@lid"

    assert client.resolveChatJid(merged["userId"], prefer=JidKind.LID) == "991122@lid"
    assert client.resolveChatJid(merged["userId"], prefer=JidKind.PN) == "5511999999999@s.whatsapp.net"

    identity = client.resolveMessageIdentity(
        {"key": {"remoteJid": "12345@g.us", "participant": "5511999999999@s.whatsapp.net"}}
    )
    assert identity.remote_user_id == "group:12345"
    assert identity.participant_user_id == "pn:5511999999999"
