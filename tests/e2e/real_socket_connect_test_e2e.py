from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

import pytest

from wassupweb import Config, make_wa_socket


@pytest.mark.asyncio
async def test_real_socket_connect_emits_live_updates() -> None:
    if os.getenv("WASSUPWEB_RUN_REAL_SOCKET") != "1":
        pytest.skip("Real socket e2e is disabled. Set WASSUPWEB_RUN_REAL_SOCKET=1 to enable.")

    root = Path(tempfile.mkdtemp(prefix="wassupweb-real-socket-"))
    updates: list[dict[str, Any]] = []
    errors: list[str] = []
    event = asyncio.Event()

    client = make_wa_socket(
        Config(
            authFolder=str(root),
            connectTimeoutMs=20_000,
            keepAliveIntervalMs=30_000,
        )
    )

    async def _on_connection_update(update: dict[str, Any]) -> None:
        updates.append(update)
        if update.get("qr") or update.get("connection") in {"open", "close"}:
            event.set()

    async def _on_error(error: Any) -> None:
        errors.append(f"{type(error).__name__}: {error}")
        event.set()

    client.ev.on("connection.update", _on_connection_update)
    client.ev.on("error", _on_error)

    try:
        await client.connect()
        await asyncio.wait_for(event.wait(), timeout=60)

        states = [u.get("connection") for u in updates if "connection" in u]
        saw_connecting = any(state == "connecting" for state in states)
        saw_qr = any(bool(u.get("qr")) for u in updates)
        saw_open = any(state == "open" for state in states)

        assert saw_connecting, f"expected connecting state, got updates={updates!r} errors={errors!r}"
        assert saw_qr or saw_open, f"expected qr or open state, got updates={updates!r} errors={errors!r}"
    finally:
        try:
            await client.disconnect()
        except Exception:
            pass
        shutil.rmtree(root, ignore_errors=True)

