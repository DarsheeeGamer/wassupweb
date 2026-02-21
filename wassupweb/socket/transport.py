from __future__ import annotations

import websockets
from typing import Any

from ..abc import TransportABC


class WebSocketTransport(TransportABC):
    def __init__(self, url: str, open_timeout: float | None = 20.0, **kwargs: object) -> None:
        self._url = url
        self._open_timeout = open_timeout
        self._kwargs = kwargs
        self._ws: Any | None = None

    async def connect(self) -> None:
        self._ws = await websockets.connect(self._url, open_timeout=self._open_timeout, **self._kwargs)

    @property
    def is_open(self) -> bool:
        ws = self._ws
        if ws is None:
            return False
        state = getattr(ws, "state", None)
        if state is not None:
            state_name = getattr(state, "name", None)
            if isinstance(state_name, str):
                return state_name.upper() == "OPEN"
            try:
                return int(state) == 1
            except Exception:
                return False
        open_state = getattr(ws, "open", None)
        if open_state is not None:
            return bool(open_state)
        closed_state = getattr(ws, "closed", None)
        if isinstance(closed_state, bool):
            return not closed_state
        return True

    async def disconnect(self) -> None:
        if self._ws:
            await self._ws.close()
        self._ws = None

    async def send(self, payload: bytes) -> None:
        if not self._ws:
            raise RuntimeError("transport is not connected")
        await self._ws.send(payload)

    async def recv(self) -> bytes:
        if not self._ws:
            raise RuntimeError("transport is not connected")
        data = await self._ws.recv()
        if isinstance(data, str):
            return data.encode("utf-8")
        return data
