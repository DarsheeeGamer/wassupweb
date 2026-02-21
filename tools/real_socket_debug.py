from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from wassupweb import Config, make_wa_socket
from wassupweb.utils.generics import fetch_latest_wa_web_version
from wassupweb.utils.logger import get_logger


def _serialize(obj: Any) -> str:
    try:
        if hasattr(obj, "model_dump"):
            return json.dumps(obj.model_dump(by_alias=True, exclude_none=True), default=str)
        if hasattr(obj, "__dict__"):
            return json.dumps(obj.__dict__, default=str)
        return json.dumps(obj, default=str)
    except Exception:
        return str(obj)


async def _run(args: argparse.Namespace) -> None:
    py_logger = logging.getLogger("wassupweb-debug")
    py_logger.setLevel(logging.DEBUG)
    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    py_logger.handlers.clear()
    py_logger.addHandler(handler)

    logger = get_logger("wassupweb.debug").child({"tool": "real_socket_debug"})
    latest = fetch_latest_wa_web_version(timeout=10.0)
    version = latest.get("version") or (2, 3000, 0)
    py_logger.info("Using WA Web version=%s isLatest=%s", version, latest.get("isLatest"))
    client = make_wa_socket(
        Config(
            authFolder=args.session_dir,
            version=tuple(version),
            connectTimeoutMs=args.connect_timeout_ms,
            keepAliveIntervalMs=args.keep_alive_interval_ms,
            printQRInTerminal=False,
            strictNoiseCertValidation=args.strict_noise_cert_validation,
            logger=logger,
        )
    )

    open_event = asyncio.Event()
    close_event = asyncio.Event()

    async def on_connection_update(update: dict[str, Any]) -> None:
        py_logger.debug("[connection.update] %s", _serialize(update))
        if update.get("qr"):
            print("\n=== QR PAYLOAD ===")
            print(update["qr"])
            print("=== END QR PAYLOAD ===\n")
        if update.get("connection") == "open":
            open_event.set()
        if update.get("connection") == "close":
            close_event.set()

    async def on_error(error: Any) -> None:
        py_logger.error("[error] %s: %s", type(error).__name__, error)

    async def on_node_success(node: Any) -> None:
        py_logger.debug("[node:success] %s", _serialize(getattr(node, "attrs", {})))

    async def on_node_failure(node: Any) -> None:
        py_logger.error("[node:failure] %s", _serialize(getattr(node, "attrs", {})))

    async def on_node_stream_error(node: Any) -> None:
        py_logger.error("[node:stream:error] %s", _serialize(getattr(node, "attrs", {})))

    async def on_node_ib(node: Any) -> None:
        py_logger.debug("[node:ib] %s", _serialize(getattr(node, "attrs", {})))

    client.ev.on("connection.update", on_connection_update)
    client.ev.on("error", on_error)
    client.ev.on("node:success", on_node_success)
    client.ev.on("node:failure", on_node_failure)
    client.ev.on("node:stream:error", on_node_stream_error)
    client.ev.on("node:ib", on_node_ib)

    py_logger.info("Starting live socket connect with session_dir=%s", args.session_dir)
    await client.connect()

    if args.phone_number:
        code = await client.request_pairing_code(args.phone_number, args.custom_pairing_code)
        py_logger.info("Pairing code generated: %s", code)

    wait_tasks = [asyncio.create_task(open_event.wait()), asyncio.create_task(close_event.wait())]
    done, pending = await asyncio.wait(wait_tasks, timeout=args.wait_sec, return_when=asyncio.FIRST_COMPLETED)
    for task in pending:
        task.cancel()

    if not done:
        py_logger.error("Timeout waiting for open/close event (%ss).", args.wait_sec)
    elif wait_tasks[0] in done:
        py_logger.info("Connection opened.")
    else:
        py_logger.warning("Connection closed before opening.")

    if args.stay_open:
        py_logger.info("Staying open. Press Ctrl+C to stop.")
        while True:
            await asyncio.sleep(1)
    else:
        await client.disconnect()


def _parse() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Standalone real WhatsApp socket debug runner (no pytest)")
    p.add_argument("--session-dir", default="session", help="Auth folder (default: session)")
    p.add_argument("--phone-number", help="Optional phone-number pairing flow")
    p.add_argument("--custom-pairing-code", help="Optional fixed 8-char pairing code")
    p.add_argument("--strict-noise-cert-validation", action="store_true", help="Enable strict noise cert verification")
    p.add_argument("--connect-timeout-ms", type=int, default=20_000)
    p.add_argument("--keep-alive-interval-ms", type=int, default=30_000)
    p.add_argument("--wait-sec", type=int, default=180, help="Seconds to wait for open/close")
    p.add_argument("--stay-open", action="store_true", help="Keep process running after open/close result")
    return p.parse_args()


def main() -> None:
    args = _parse()
    try:
        asyncio.run(_run(args))
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
