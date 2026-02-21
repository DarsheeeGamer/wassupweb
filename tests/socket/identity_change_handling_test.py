from __future__ import annotations

from typing import Any

import pytest

from wassupweb.utils.identity_change_handler import TTLBoolCache, handle_identity_change
from wassupweb.wabinary import BinaryNode


class _Logger:
    def info(self, *_args: object, **_kwargs: object) -> None:
        return

    def debug(self, *_args: object, **_kwargs: object) -> None:
        return

    def warning(self, *_args: object, **_kwargs: object) -> None:
        return


class _Fns:
    def __init__(self) -> None:
        self.validate_calls: list[str] = []
        self.assert_calls: list[tuple[list[str], bool]] = []
        self._validate_result: dict[str, Any] = {"exists": True}
        self._assert_error: Exception | None = None
        self._validate_error: Exception | None = None

    async def validate_session(self, jid: str) -> dict[str, Any]:
        self.validate_calls.append(jid)
        if self._validate_error:
            raise self._validate_error
        return dict(self._validate_result)

    async def assert_sessions(self, jids: list[str], force: bool = False) -> bool:
        self.assert_calls.append((jids, force))
        if self._assert_error:
            raise self._assert_error
        return True


def _identity_change_node(from_jid: str | None, offline: str | None = None, include_identity: bool = True) -> BinaryNode:
    attrs: dict[str, str] = {"type": "encrypt"}
    if from_jid is not None:
        attrs["from"] = from_jid
    if offline is not None:
        attrs["offline"] = offline

    content: list[BinaryNode] = []
    if include_identity:
        content = [BinaryNode(tag="identity", attrs={}, content=b"test-identity-key")]

    return BinaryNode(tag="notification", attrs=attrs, content=content)


def _ctx(fns: _Fns, *, me_id: str = "myuser@s.whatsapp.net", me_lid: str | None = "mylid@lid", cache: TTLBoolCache | None = None) -> dict[str, Any]:
    return {
        "meId": me_id,
        "meLid": me_lid,
        "validateSession": fns.validate_session,
        "assertSessions": fns.assert_sessions,
        "debounceCache": cache or TTLBoolCache(ttl_ms=5_000),
        "logger": _Logger(),
    }


@pytest.mark.asyncio
async def test_should_skip_companion_devices() -> None:
    fns = _Fns()
    result = await handle_identity_change(_identity_change_node("user:5@s.whatsapp.net"), _ctx(fns))
    assert result["action"] == "skipped_companion_device"
    assert not fns.validate_calls


@pytest.mark.asyncio
async def test_should_process_primary_device() -> None:
    fns = _Fns()
    result = await handle_identity_change(_identity_change_node("user@s.whatsapp.net"), _ctx(fns))
    assert result["action"] == "session_refreshed"


@pytest.mark.asyncio
async def test_should_skip_self_primary_by_pn_or_lid() -> None:
    fns = _Fns()
    result1 = await handle_identity_change(_identity_change_node("myuser@s.whatsapp.net"), _ctx(fns))
    result2 = await handle_identity_change(_identity_change_node("mylid@lid"), _ctx(fns))
    assert result1["action"] == "skipped_self_primary"
    assert result2["action"] == "skipped_self_primary"
    assert not fns.validate_calls


@pytest.mark.asyncio
async def test_should_skip_when_no_existing_session() -> None:
    fns = _Fns()
    fns._validate_result = {"exists": False}
    result = await handle_identity_change(_identity_change_node("user@s.whatsapp.net"), _ctx(fns))
    assert result["action"] == "skipped_no_session"
    assert not fns.assert_calls


@pytest.mark.asyncio
async def test_should_skip_during_offline_processing() -> None:
    fns = _Fns()
    result = await handle_identity_change(_identity_change_node("user@s.whatsapp.net", offline="0"), _ctx(fns))
    assert result["action"] == "skipped_offline"
    assert not fns.assert_calls


@pytest.mark.asyncio
async def test_should_refresh_session_when_online_with_existing_session() -> None:
    fns = _Fns()
    result = await handle_identity_change(_identity_change_node("user@s.whatsapp.net"), _ctx(fns))
    assert result["action"] == "session_refreshed"
    assert fns.assert_calls == [(["user@s.whatsapp.net"], True)]


@pytest.mark.asyncio
async def test_should_debounce_multiple_identity_changes_for_same_jid() -> None:
    fns = _Fns()
    cache = TTLBoolCache(ttl_ms=60_000)
    ctx = _ctx(fns, cache=cache)
    node = _identity_change_node("user@s.whatsapp.net")

    result1 = await handle_identity_change(node, ctx)
    result2 = await handle_identity_change(node, ctx)

    assert result1["action"] == "session_refreshed"
    assert result2["action"] == "debounced"
    assert len(fns.assert_calls) == 1


@pytest.mark.asyncio
async def test_should_allow_different_jids_independently() -> None:
    fns = _Fns()
    cache = TTLBoolCache(ttl_ms=60_000)
    ctx = _ctx(fns, cache=cache)

    result1 = await handle_identity_change(_identity_change_node("user1@s.whatsapp.net"), ctx)
    result2 = await handle_identity_change(_identity_change_node("user2@s.whatsapp.net"), ctx)

    assert result1["action"] == "session_refreshed"
    assert result2["action"] == "session_refreshed"
    assert len(fns.assert_calls) == 2


@pytest.mark.asyncio
async def test_should_handle_assert_sessions_failure_gracefully() -> None:
    fns = _Fns()
    err = RuntimeError("Session assertion failed")
    fns._assert_error = err

    result = await handle_identity_change(_identity_change_node("user@s.whatsapp.net"), _ctx(fns))

    assert result["action"] == "session_refresh_failed"
    assert result["error"] is err


@pytest.mark.asyncio
async def test_should_propagate_validate_session_errors() -> None:
    fns = _Fns()
    fns._validate_error = RuntimeError("Database error")

    with pytest.raises(RuntimeError, match="Database error"):
        await handle_identity_change(_identity_change_node("user@s.whatsapp.net"), _ctx(fns))


@pytest.mark.asyncio
async def test_should_return_invalid_notification_when_from_missing() -> None:
    fns = _Fns()
    result = await handle_identity_change(_identity_change_node(None), _ctx(fns))
    assert result["action"] == "invalid_notification"


@pytest.mark.asyncio
async def test_should_return_no_identity_node_when_identity_missing() -> None:
    fns = _Fns()
    result = await handle_identity_change(_identity_change_node("user@s.whatsapp.net", include_identity=False), _ctx(fns))
    assert result["action"] == "no_identity_node"


@pytest.mark.asyncio
async def test_should_handle_lid_jids_correctly() -> None:
    fns = _Fns()
    result = await handle_identity_change(_identity_change_node("12345@lid"), _ctx(fns))
    assert result["action"] == "session_refreshed"
    assert fns.validate_calls == ["12345@lid"]
