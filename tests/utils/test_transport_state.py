from __future__ import annotations

from types import SimpleNamespace

from wassupweb.socket.transport import WebSocketTransport


def test_transport_is_open_with_state_name() -> None:
    transport = WebSocketTransport("wss://example.test")
    transport._ws = SimpleNamespace(state=SimpleNamespace(name="OPEN"))
    assert transport.is_open is True


def test_transport_is_open_with_state_closed_name() -> None:
    transport = WebSocketTransport("wss://example.test")
    transport._ws = SimpleNamespace(state=SimpleNamespace(name="CLOSED"))
    assert transport.is_open is False

