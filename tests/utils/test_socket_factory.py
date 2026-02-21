from __future__ import annotations

from wassupweb.socket.communities import CommunitiesSocket
from wassupweb.socket.factory import make_wa_socket
from wassupweb.types.socket import SocketConfig


def test_factory_returns_full_wa_socket_stack() -> None:
    sock = make_wa_socket(SocketConfig())
    assert isinstance(sock, CommunitiesSocket)
    assert hasattr(sock.ev, "buffer")
