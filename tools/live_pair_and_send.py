from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from wassupweb import Config, make_wa_socket
from wassupweb.types import SendTextInput
from wassupweb.utils.generics import fetch_latest_wa_web_version


async def _run(args: argparse.Namespace) -> None:
    latest = fetch_latest_wa_web_version(timeout=10.0)
    version = latest.get("version") or (2, 3000, 0)
    print(f"Using WA Web version={version} isLatest={latest.get('isLatest')}")
    client = make_wa_socket(
        Config(
            authFolder=args.session_dir,
            version=tuple(version),
            printQRInTerminal=False,
        )
    )

    done = asyncio.Event()

    async def _on_connection_update(update: dict[str, Any]) -> None:
        print(f"[connection.update] raw={update}")
        qr = update.get("qr")
        if qr:
            print("\n[connection.update] QR payload received. Scan this from WhatsApp Linked Devices:")
            print(qr)

        state = update.get("connection")
        if state:
            print(f"[connection.update] state={state}")
        if state == "open":
            done.set()

    async def _on_messages_upsert(payload: dict[str, Any]) -> None:
        print(f"[messages.upsert] type={payload.get('type')} count={len(payload.get('messages', []) or [])}")

    async def _on_error(error: Any) -> None:
        print(f"[error] {type(error).__name__}: {error}")

    async def _on_node_success(node: Any) -> None:
        print(f"[node:success] attrs={getattr(node, 'attrs', {})}")

    async def _on_node_failure(node: Any) -> None:
        print(f"[node:failure] attrs={getattr(node, 'attrs', {})}")

    async def _on_node_stream_error(node: Any) -> None:
        print(f"[node:stream:error] attrs={getattr(node, 'attrs', {})}")

    async def _on_node_ib(node: Any) -> None:
        print(f"[node:ib] attrs={getattr(node, 'attrs', {})}")

    client.ev.on("connection.update", _on_connection_update)
    client.ev.on("messages.upsert", _on_messages_upsert)
    client.ev.on("error", _on_error)
    client.ev.on("node:success", _on_node_success)
    client.ev.on("node:failure", _on_node_failure)
    client.ev.on("node:stream:error", _on_node_stream_error)
    client.ev.on("node:ib", _on_node_ib)

    print(f"Starting connect() with auth folder: {args.session_dir}")
    await client.connect()

    if args.phone_number:
        code = await client.request_pairing_code(args.phone_number, args.custom_pairing_code)
        print(f"\nPairing code: {code}")

    try:
        await asyncio.wait_for(done.wait(), timeout=args.open_timeout_sec)
    except asyncio.TimeoutError:
        raise RuntimeError(f"Timed out waiting for connection open after {args.open_timeout_sec}s")

    if args.to and args.text:
        result = await client.send_text(SendTextInput(to=args.to, text=args.text))
        msg_id = (((result or {}).get("key") or {}).get("id"))
        print(f"Sent message to {args.to}. message_id={msg_id}")

    print("Connected. Press Ctrl+C to stop.")
    while True:
        await asyncio.sleep(1)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Live WhatsApp account pair/connect/send smoke runner for wassupweb")
    parser.add_argument("--session-dir", default="session", help="Folder for persisted auth state (default: session)")
    parser.add_argument("--phone-number", help="Phone number (digits only) to request pairing code instead of QR")
    parser.add_argument("--custom-pairing-code", help="Optional 8-char custom pairing code")
    parser.add_argument("--to", help="Recipient JID/identity for test send, e.g. 15551234567@s.whatsapp.net")
    parser.add_argument("--text", help="Text body for test send")
    parser.add_argument("--open-timeout-sec", type=int, default=180, help="Timeout waiting for open connection")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    try:
        asyncio.run(_run(args))
    except KeyboardInterrupt:
        print("\nStopped by user.")


if __name__ == "__main__":
    main()
