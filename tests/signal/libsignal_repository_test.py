from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from wassupweb.signal.libsignal import (
    jid_to_signal_protocol_address,
    jid_to_signal_sender_key_name,
    jidToSignalProtocolAddress,
    jidToSignalSenderKeyName,
    make_libsignal_repository,
)


class _Keys:
    def __init__(self) -> None:
        self.store: dict[str, dict[str, Any]] = {
            "device-list": {},
            "session": {},
            "sender-key": {},
            "identity-key": {},
            "pre-key": {},
            "lid-mapping": {},
        }

    async def get(self, key_type: str, ids: list[str]) -> dict[str, Any]:
        bucket = self.store.setdefault(key_type, {})
        return {item: bucket.get(item) for item in ids}

    async def set(self, updates: dict[str, dict[str, Any]]) -> None:
        for key_type, values in updates.items():
            bucket = self.store.setdefault(key_type, {})
            for item_key, item_value in values.items():
                if item_value is None:
                    bucket.pop(item_key, None)
                else:
                    bucket[item_key] = item_value

    async def transaction(self, work: Any, _id: str | None = None) -> Any:
        return await work()


def _make_auth() -> Any:
    keys = _Keys()
    creds = SimpleNamespace()
    return SimpleNamespace(keys=keys, creds=creds)


def test_jid_helper_camelcase_aliases() -> None:
    snake_addr = jid_to_signal_protocol_address("12345@s.whatsapp.net")
    camel_addr = jidToSignalProtocolAddress("12345@s.whatsapp.net")
    assert str(camel_addr) == str(snake_addr)

    snake_name = jid_to_signal_sender_key_name("group@g.us", "12345@s.whatsapp.net")
    camel_name = jidToSignalSenderKeyName("group@g.us", "12345@s.whatsapp.net")
    assert str(camel_name) == str(snake_name)


@pytest.mark.asyncio
async def test_repository_exposes_camelcase_aliases_and_lid_mapping_alias() -> None:
    auth = _make_auth()
    repo = make_libsignal_repository(auth, logger=None)

    assert repo.lidMapping is repo.lid_mapping
    assert repo.jidToSignalProtocolAddress("12345@s.whatsapp.net") == repo.jid_to_signal_protocol_address(
        "12345@s.whatsapp.net"
    )
    result = await repo.migrateSession("12345@lid", "99999:99@hosted.lid")
    assert result == {"migrated": 0, "skipped": 0, "total": 1}


@pytest.mark.asyncio
async def test_migrate_session_uses_signal_address_session_lookup_for_hosted_devices() -> None:
    auth = _make_auth()
    keys = auth.keys
    keys.store["device-list"]["12345"] = ["0", "99"]

    hosted_from_addr = str(jid_to_signal_protocol_address("12345:99@hosted"))
    keys.store["session"]["12345.0"] = b"primary"
    keys.store["session"]["12345.99"] = b"discovery"
    keys.store["session"][hosted_from_addr] = b"hosted"

    repo = make_libsignal_repository(auth, logger=None)
    result = await repo.migrate_session("12345@s.whatsapp.net", "99999@hosted.lid")

    assert result["total"] == 2
    assert result["migrated"] == 2
    assert result["skipped"] == 0
