from __future__ import annotations

import shutil
import uuid
from pathlib import Path
from types import MethodType, SimpleNamespace
from typing import Any

import pytest

import wassupweb.socket.socket as core_socket_mod
from wassupweb.socket.socket import CoreSocket
from wassupweb.utils.use_multi_file_auth_state import use_multi_file_auth_state


class _Logger:
    def warning(self, *_args: object, **_kwargs: object) -> None:
        return


@pytest.mark.asyncio
async def test_use_multi_file_auth_state_defaults_to_session_folder(monkeypatch: pytest.MonkeyPatch) -> None:
    root = Path(".test_tmp_session_default") / str(uuid.uuid4())
    root.mkdir(parents=True, exist_ok=True)
    root_abs = root.resolve()
    try:
        monkeypatch.chdir(root)
        state, save = await use_multi_file_auth_state()
        assert state is not None
        await save()
        assert (root_abs / "session" / "creds.json").exists()
    finally:
        shutil.rmtree(root_abs, ignore_errors=True)


@pytest.mark.asyncio
async def test_core_socket_ensure_auth_state_uses_session_folder_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    async def _fake_loader(folder: str = "session") -> tuple[Any, Any]:
        captured["folder"] = folder
        captured["saved"] = 0

        async def _save() -> None:
            captured["saved"] += 1

        auth = SimpleNamespace(creds=SimpleNamespace(model_fields={}, me=None))
        return auth, _save

    monkeypatch.setattr(core_socket_mod, "use_multi_file_auth_state", _fake_loader)

    obj = SimpleNamespace(config=SimpleNamespace(auth=None), _logger=_Logger(), _save_auth_state=None)
    await CoreSocket._ensure_auth_state(obj)  # type: ignore[arg-type]

    assert captured["folder"] == "session"
    assert obj.config.auth is not None
    assert callable(obj._save_auth_state)


@pytest.mark.asyncio
async def test_handle_creds_update_persists_when_default_auth_store_is_active() -> None:
    from wassupweb.utils.auth_utils import init_auth_creds

    called = {"saved": 0}

    async def _save() -> None:
        called["saved"] += 1

    creds = init_auth_creds()
    obj = SimpleNamespace(
        config=SimpleNamespace(auth=SimpleNamespace(creds=creds)),
        _logger=_Logger(),
        _save_auth_state=_save,
    )
    obj.send_node = MethodType(lambda _self, _node: None, obj)

    await CoreSocket._handle_creds_update(obj, {"lastPropHash": "v1"})  # type: ignore[arg-type]

    assert creds.last_prop_hash == "v1"
    assert called["saved"] == 1
