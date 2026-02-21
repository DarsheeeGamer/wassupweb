from __future__ import annotations

import time
from typing import Any, Literal, TypedDict

from ..wabinary import are_jids_same_user, get_binary_node_child, jid_decode
from .generics import is_string_null_or_empty


class IdentityChangeResult(TypedDict, total=False):
    action: Literal[
        "no_identity_node",
        "invalid_notification",
        "skipped_companion_device",
        "skipped_self_primary",
        "debounced",
        "skipped_offline",
        "skipped_no_session",
        "session_refreshed",
        "session_refresh_failed",
    ]
    device: int
    error: Any


class TTLBoolCache:
    def __init__(self, ttl_ms: int = 15_000) -> None:
        self._ttl_ms = ttl_ms
        self._data: dict[str, float] = {}

    def get(self, key: str) -> bool:
        now = time.time() * 1000
        exp = self._data.get(key)
        if exp is None:
            return False
        if exp <= now:
            self._data.pop(key, None)
            return False
        return True

    def set(self, key: str, value: bool) -> None:
        if value:
            self._data[key] = (time.time() * 1000) + self._ttl_ms
        else:
            self._data.pop(key, None)


async def handle_identity_change(node: Any, ctx: dict[str, Any]) -> IdentityChangeResult:
    from_jid = getattr(node, "attrs", {}).get("from")
    if not from_jid:
        return {"action": "invalid_notification"}

    identity_node = get_binary_node_child(node, "identity")
    if not identity_node:
        return {"action": "no_identity_node"}

    logger = ctx.get("logger")
    if logger:
        logger.info("identity changed", extra={"jid": from_jid})

    decoded = jid_decode(from_jid) or {}
    device = int(decoded.get("device") or 0)
    if device and device != 0:
        if logger:
            logger.debug("ignoring identity change from companion device", extra={"jid": from_jid, "device": device})
        return {"action": "skipped_companion_device", "device": device}

    me_id = ctx.get("meId")
    me_lid = ctx.get("meLid")
    is_self_primary = bool(me_id and (are_jids_same_user(from_jid, me_id) or (me_lid and are_jids_same_user(from_jid, me_lid))))
    if is_self_primary:
        if logger:
            logger.info("self primary identity changed", extra={"jid": from_jid})
        return {"action": "skipped_self_primary"}

    debounce_cache = ctx.get("debounceCache")
    if debounce_cache and debounce_cache.get(from_jid):
        if logger:
            logger.debug("skipping identity assert (debounced)", extra={"jid": from_jid})
        return {"action": "debounced"}

    if debounce_cache:
        debounce_cache.set(from_jid, True)

    validate_session = ctx["validateSession"]
    assert_sessions = ctx["assertSessions"]
    offline_notification = not is_string_null_or_empty(getattr(node, "attrs", {}).get("offline"))
    has_existing_session = await validate_session(from_jid)
    if not has_existing_session.get("exists"):
        if logger:
            logger.debug("no old session, skipping session refresh", extra={"jid": from_jid})
        return {"action": "skipped_no_session"}

    if logger:
        logger.debug("old session exists, will refresh session", extra={"jid": from_jid})
    if offline_notification:
        if logger:
            logger.debug("skipping session refresh during offline processing", extra={"jid": from_jid})
        return {"action": "skipped_offline"}

    try:
        await assert_sessions([from_jid], True)
        return {"action": "session_refreshed"}
    except Exception as error:  # pragma: no cover - runtime handling path
        if logger:
            logger.warning("failed to assert sessions after identity change", extra={"jid": from_jid, "error": str(error)})
        return {"action": "session_refresh_failed", "error": error}


# camelCase aliases
handleIdentityChange = handle_identity_change
