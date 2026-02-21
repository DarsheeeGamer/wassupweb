from __future__ import annotations

from typing import Any

from wassupweb.types.auth import LTHashState, ProtocolAddress, SignalAuthState, SignalCreds, SignalIdentity


def test_signal_identity_accepts_nested_identifier() -> None:
    identity = SignalIdentity.model_validate(
        {
            "identifier": {"name": "12345@s.whatsapp.net", "deviceId": 7},
            "identifierKey": b"k",
        }
    )
    assert identity.identifier is not None
    assert identity.identifier == ProtocolAddress(name="12345@s.whatsapp.net", deviceId=7)
    assert identity.name == "12345@s.whatsapp.net"
    assert identity.device_id == 7


def test_signal_identity_builds_identifier_from_flat_shape() -> None:
    identity = SignalIdentity.model_validate(
        {
            "name": "67890@s.whatsapp.net",
            "deviceId": 2,
            "identifierKey": b"k2",
        }
    )
    assert identity.identifier is not None
    assert identity.identifier.name == "67890@s.whatsapp.net"
    assert identity.identifier.device_id == 2


def test_lt_hash_state_parses_index_value_map() -> None:
    state = LTHashState.model_validate(
        {
            "version": 1,
            "hash": b"h",
            "indexValueMap": {"abc": {"valueMac": b"v"}},
        }
    )
    assert state.index_value_map["abc"].value_mac == b"v"


def test_signal_auth_state_shape() -> None:
    class _Keys:
        async def get(self, key_type: str, ids: list[str]) -> dict[str, Any]:
            return {}

        async def set(self, data: dict[str, dict[str, Any | None]]) -> None:
            return None

        async def clear(self) -> None:
            return None

    state = SignalAuthState.model_validate(
        {
            "creds": {
                "signedIdentityKey": {"public": b"p", "private": b"q"},
                "signedPreKey": {
                    "keyPair": {"public": b"a", "private": b"b"},
                    "signature": b"s",
                    "keyId": 1,
                },
                "registrationId": 10,
            },
            "keys": _Keys(),
        }
    )
    assert isinstance(state.creds, SignalCreds)
    assert state.creds.registration_id == 10
