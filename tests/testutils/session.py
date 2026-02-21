from __future__ import annotations

import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from wassupweb.types.auth import AuthenticationState
from wassupweb.utils.use_multi_file_auth_state import use_multi_file_auth_state


@dataclass(slots=True)
class SessionBundle:
    state: AuthenticationState
    save_creds: Callable[[], Any]
    clear: Callable[[], Any]


async def make_session() -> SessionBundle:
    root = Path(tempfile.mkdtemp(prefix="wassupweb-test-session-"))
    state, save_creds = await use_multi_file_auth_state(str(root))

    async def clear() -> None:
        shutil.rmtree(root, ignore_errors=True)

    return SessionBundle(state=state, save_creds=save_creds, clear=clear)


class _MockWebSocket:
    def __init__(self) -> None:
        self.open = True
        self.closed = False

    async def send(self, _payload: bytes) -> None:
        return

    async def recv(self) -> bytes:
        return b""

    async def close(self) -> None:
        self.open = False
        self.closed = True


def mock_websocket(monkeypatch: Any) -> None:
    async def _fake_connect(*_args: Any, **_kwargs: Any) -> _MockWebSocket:
        return _MockWebSocket()

    monkeypatch.setattr("websockets.connect", _fake_connect)
