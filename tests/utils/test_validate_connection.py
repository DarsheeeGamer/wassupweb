from __future__ import annotations

import base64
from typing import Any

import pytest

import wassupweb.utils.validate_connection as vc
from wassupweb.wabinary import BinaryNode


def test_generate_login_node_has_expected_payload_fields() -> None:
    payload = vc.generate_login_node(
        "123456789:7@s.whatsapp.net",
        {"version": (2, 3000, 1017531287), "countryCode": "US"},
    )
    assert payload["passive"] is True
    assert payload["pull"] is True
    assert payload["username"] == 123456789
    assert payload["device"] == 7
    assert payload["webInfo"]["webSubPlatform"] == "WEB_BROWSER"


def test_generate_registration_node_encodes_pairing_material() -> None:
    signal_creds = {
        "registrationId": 1234,
        "signedIdentityKey": {"public": b"pub-id"},
        "signedPreKey": {"keyId": 5, "keyPair": {"public": b"pre-pub"}, "signature": b"sig"},
    }
    payload = vc.generate_registration_node(
        signal_creds,
        {"version": (2, 3000, 1017531287), "browser": ("Windows", "Desktop", ""), "syncFullHistory": True},
    )
    dp = payload["devicePairingData"]
    assert payload["passive"] is False
    assert payload["pull"] is False
    assert isinstance(dp["buildHash"], bytes)
    assert isinstance(dp["deviceProps"], bytes)
    assert dp["eKeytype"] == bytes([5])
    assert dp["eIdent"] == b"pub-id"
    assert dp["eSkeyVal"] == b"pre-pub"
    assert dp["eSkeySig"] == b"sig"


@pytest.mark.asyncio
async def test_configure_successful_pairing_accepts_base64_secret_and_builds_reply(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    details = b"acc-details"
    hmac_value = b"\x01\x02\x03"
    key_bytes = b"\x11" * 32
    sig_bytes = b"\x22" * 64

    monkeypatch.setattr(vc, "hmac_sign", lambda msg, key: hmac_value)
    monkeypatch.setattr(vc.Curve, "verify", lambda key, msg, sig: True)
    monkeypatch.setattr(vc.Curve, "sign", lambda private, msg: b"device-signature")

    class _Identity:
        def model_dump(self, by_alias: bool = True) -> dict[str, Any]:
            _ = by_alias
            return {"identifierKey": "id-key", "name": "abc", "deviceId": 1}

    monkeypatch.setattr(vc, "create_signal_identity", lambda lid, key: _Identity())

    stanza = BinaryNode(
        tag="iq",
        attrs={"id": "msg-1"},
        content=[
            BinaryNode(
                tag="pair-success",
                attrs={},
                content=[
                    BinaryNode(
                        tag="device-identity",
                        attrs={
                            "hmac": base64.b64encode(hmac_value).decode("ascii"),
                            "account_signature_key": base64.b64encode(key_bytes).decode("ascii"),
                            "account_signature": base64.b64encode(sig_bytes).decode("ascii"),
                            "key-index": "7",
                        },
                        content=details,
                    ),
                    BinaryNode(tag="platform", attrs={"name": "Windows"}),
                    BinaryNode(tag="device", attrs={"jid": "111@s.whatsapp.net", "lid": "111@lid"}),
                    BinaryNode(tag="biz", attrs={"name": "Acme"}),
                ],
            )
        ],
    )
    creds = {
        "advSecretKey": base64.b64encode(b"s" * 32).decode("ascii"),
        "signedIdentityKey": {"public": b"pub", "private": b"priv"},
        "signalIdentities": [],
    }

    result = vc.configure_successful_pairing(stanza, creds)
    assert result["reply"].tag == "iq"
    assert result["reply"].attrs["id"] == "msg-1"
    inner = result["reply"].content[0].content[0]
    assert inner.attrs["key-index"] == "7"
    assert result["creds"]["me"]["id"] == "111@s.whatsapp.net"
    assert result["creds"]["me"]["lid"] == "111@lid"
    assert result["creds"]["account"]["deviceSignature"] == b"device-signature"
    assert result["creds"]["signalIdentities"] == [{"identifierKey": "id-key", "name": "abc", "deviceId": 1}]


def test_configure_successful_pairing_rejects_invalid_hmac(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(vc, "hmac_sign", lambda msg, key: b"\x00")

    stanza = BinaryNode(
        tag="iq",
        attrs={"id": "m1"},
        content=[
            BinaryNode(
                tag="pair-success",
                attrs={},
                content=[
                    BinaryNode(
                        tag="device-identity",
                        attrs={"hmac": base64.b64encode(b"\x01").decode("ascii"), "key-index": "1"},
                        content=b"details",
                    ),
                    BinaryNode(tag="device", attrs={"jid": "1@s.whatsapp.net", "lid": "1@lid"}),
                ],
            )
        ],
    )

    creds = {
        "advSecretKey": base64.b64encode(b"s" * 32).decode("ascii"),
        "signedIdentityKey": {"public": b"pub", "private": b"priv"},
        "signalIdentities": [],
    }
    with pytest.raises(ValueError, match="Invalid account signature"):
        vc.configure_successful_pairing(stanza, creds)


def test_decode_encoded_bytes_prefers_hex_for_hex_input() -> None:
    assert vc._decode_encoded_bytes("61626364") == b"abcd"


def test_decode_encoded_bytes_uses_base64_for_non_hex_input() -> None:
    encoded = base64.b64encode(b"payload").decode("ascii")
    assert vc._decode_encoded_bytes(encoded) == b"payload"
