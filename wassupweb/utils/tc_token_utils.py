from __future__ import annotations

from typing import Any

from ..wabinary.types import BinaryNode


async def build_tc_token_from_jid(
    *,
    auth_state: dict[str, Any],
    jid: str,
    base_content: list[BinaryNode] | None = None,
) -> list[BinaryNode] | None:
    base = list(base_content or [])
    try:
        key_store = (auth_state or {}).get("keys")
        if not key_store:
            return base or None
        tc_token_data = await key_store.get("tctoken", [jid])
        token_entry = (tc_token_data or {}).get(jid) or {}
        token = token_entry.get("token")
        if not token:
            return base or None
        base.append(BinaryNode(tag="tctoken", attrs={}, content=token))
        return base
    except Exception:
        return base or None


# camelCase alias
buildTcTokenFromJid = build_tc_token_from_jid
