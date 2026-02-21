from __future__ import annotations

import asyncio
import base64
import json
from pathlib import Path
from typing import Any, Callable

from ..types.auth import (
    AuthenticationCreds,
    AuthenticationState,
    SignalDataSet,
)
from .auth_utils import init_auth_creds
from pydantic import BaseModel

_FILE_LOCKS: dict[str, asyncio.Lock] = {}


def _file_lock(path: Path) -> asyncio.Lock:
    key = str(path.resolve())
    if key not in _FILE_LOCKS:
        _FILE_LOCKS[key] = asyncio.Lock()
    return _FILE_LOCKS[key]


def _fix_file_name(file_name: str) -> str:
    return file_name.replace("/", "__").replace(":", "-")


def _to_jsonable(value: Any) -> Any:
    if isinstance(value, (bytes, bytearray)):
        return {"__type__": "bytes", "data": base64.b64encode(bytes(value)).decode("ascii")}
    if isinstance(value, BaseModel):
        return {k: _to_jsonable(v) for k, v in value.model_dump(by_alias=False).items()}
    if isinstance(value, dict):
        return {k: _to_jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_to_jsonable(v) for v in value]
    return value


def _from_jsonable(value: Any) -> Any:
    if isinstance(value, dict) and value.get("__type__") == "bytes":
        return base64.b64decode(value["data"])
    if isinstance(value, dict):
        return {k: _from_jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_from_jsonable(v) for v in value]
    return value


def _decode_auth_creds(raw: dict[str, Any]) -> AuthenticationCreds:
    return AuthenticationCreds.model_validate(raw)


async def use_multi_file_auth_state(folder: str = "session") -> tuple[AuthenticationState, Callable[[], Any]]:
    root = Path(folder)
    root.mkdir(parents=True, exist_ok=True)
    if not root.is_dir():
        raise ValueError(f"Path exists and is not a directory: {folder}")

    async def write_data(data: Any, file_name: str) -> None:
        file_path = root / _fix_file_name(file_name)
        lock = _file_lock(file_path)
        async with lock:
            payload = json.dumps(_to_jsonable(data), separators=(",", ":"))
            await asyncio.to_thread(file_path.write_text, payload, "utf-8")

    async def read_data(file_name: str) -> Any | None:
        file_path = root / _fix_file_name(file_name)
        lock = _file_lock(file_path)
        async with lock:
            if not file_path.exists():
                return None
            text = await asyncio.to_thread(file_path.read_text, "utf-8")
            return _from_jsonable(json.loads(text))

    async def remove_data(file_name: str) -> None:
        file_path = root / _fix_file_name(file_name)
        lock = _file_lock(file_path)
        async with lock:
            if file_path.exists():
                await asyncio.to_thread(file_path.unlink)

    raw_creds = await read_data("creds.json")
    creds = _decode_auth_creds(raw_creds) if raw_creds else init_auth_creds()

    class MultiFileSignalStore:
        async def get(self, key_type: str, ids: list[str]) -> dict[str, Any]:
            data: dict[str, Any] = {}
            await asyncio.gather(
                *[_load_id(data, key_type, item_id) for item_id in ids],
                return_exceptions=False,
            )
            return data

        async def set(self, data: SignalDataSet) -> None:
            tasks: list[Any] = []
            for key_type, mapping in data.items():
                for item_id, value in mapping.items():
                    file_name = f"{key_type}-{item_id}.json"
                    tasks.append(write_data(value, file_name) if value is not None else remove_data(file_name))
            await asyncio.gather(*tasks, return_exceptions=False)

        async def clear(self) -> None:
            for path in root.glob("*.json"):
                await asyncio.to_thread(path.unlink)

    async def _load_id(data: dict[str, Any], key_type: str, item_id: str) -> None:
        data[item_id] = await read_data(f"{key_type}-{item_id}.json")

    async def save_creds() -> None:
        await write_data(creds, "creds.json")

    return AuthenticationState(creds=creds, keys=MultiFileSignalStore()), save_creds
