from __future__ import annotations

import asyncio

import pytest

from wassupweb.defaults import NOISE_WA_HEADER, WA_CERT_DETAILS
from wassupweb.waproto import proto
from wassupweb.utils.crypto import Curve
from wassupweb.utils.noise_handler import make_noise_handler


class _Logger:
    def child(self, _ctx: object) -> "_Logger":
        return self

    def debug(self, *_args: object, **_kwargs: object) -> None:
        return

    def warning(self, *_args: object, **_kwargs: object) -> None:
        return


def _create_frame(payload: bytes) -> bytes:
    frame = bytearray(3 + len(payload))
    frame[0] = (len(payload) >> 16) & 0xFF
    frame[1] = (len(payload) >> 8) & 0xFF
    frame[2] = len(payload) & 0xFF
    frame[3:] = payload
    return bytes(frame)


@pytest.mark.asyncio
async def test_decode_frame_multiple_unencrypted_frames() -> None:
    key_pair = Curve.generate_key_pair()
    handler = make_noise_handler(key_pair=key_pair, NOISE_HEADER=NOISE_WA_HEADER, logger=_Logger())

    payload1 = b"\x01\x02\x03\x04\x05"
    payload2 = b"\x06\x07\x08\x09\x0a"
    combined = _create_frame(payload1) + _create_frame(payload2)

    received: list[bytes] = []
    await handler.decode_frame(combined, lambda frame: received.append(bytes(frame)))
    assert received == [payload1, payload2]


@pytest.mark.asyncio
async def test_decode_frame_split_payload_across_calls() -> None:
    key_pair = Curve.generate_key_pair()
    handler = make_noise_handler(key_pair=key_pair, NOISE_HEADER=NOISE_WA_HEADER, logger=_Logger())

    payload = b"\x01\x02\x03\x04\x05\x06\x07\x08\x09\x0a"
    frame = _create_frame(payload)
    part1, part2 = frame[:5], frame[5:]

    received: list[bytes] = []
    await handler.decode_frame(part1, lambda f: received.append(bytes(f)))
    assert received == []

    await handler.decode_frame(part2, lambda f: received.append(bytes(f)))
    assert received == [payload]


@pytest.mark.asyncio
async def test_decode_frame_concurrent_calls_no_buffer_corruption() -> None:
    key_pair = Curve.generate_key_pair()
    handler = make_noise_handler(key_pair=key_pair, NOISE_HEADER=NOISE_WA_HEADER, logger=_Logger())

    payloads = [f"payload-{i}".encode("utf-8") for i in range(5)]
    frames = [_create_frame(payload) for payload in payloads]
    received: list[bytes] = []

    await asyncio.gather(*[handler.decode_frame(frame, lambda f: received.append(bytes(f))) for frame in frames])
    assert len(received) == 5
    assert sorted(received) == sorted(payloads)


def _build_cert_chain_payload() -> bytes:
    cert_chain_cls = proto.get("CertChain")
    noise_cert_cls = getattr(cert_chain_cls, "NoiseCertificate", None) if cert_chain_cls is not None else None
    if cert_chain_cls is None or noise_cert_cls is None:
        pytest.skip("generated WAProto statics are unavailable")

    details_cls = getattr(noise_cert_cls, "Details", None)
    if details_cls is None:
        pytest.skip("NoiseCertificate.Details is unavailable")

    details = details_cls()
    details.serial = 1
    if hasattr(details, "issuerSerial"):
        details.issuerSerial = int(WA_CERT_DETAILS["SERIAL"])
    elif hasattr(details, "issuer"):
        details.issuer = str(WA_CERT_DETAILS["ISSUER"])
    details.key = b"\x44" * 32
    if hasattr(details, "notBefore"):
        details.notBefore = 0
    if hasattr(details, "notAfter"):
        details.notAfter = 999999999999

    intermediate = noise_cert_cls()
    intermediate.details = details.SerializeToString()
    intermediate.signature = b"\x55" * 64

    leaf = noise_cert_cls()
    leaf.details = b"leaf-details"
    leaf.signature = b"\x66" * 64

    cert_chain = cert_chain_cls()
    cert_chain.intermediate.CopyFrom(intermediate)
    cert_chain.leaf.CopyFrom(leaf)
    return cert_chain.SerializeToString()


def test_process_handshake_requires_server_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    key_pair = Curve.generate_key_pair()
    noise_key = Curve.generate_key_pair()
    handler = make_noise_handler(key_pair=key_pair, NOISE_HEADER=NOISE_WA_HEADER, logger=_Logger())

    monkeypatch.setattr("wassupweb.utils.noise_handler.Curve.shared_key", lambda _a, _b: b"\x00" * 32)
    monkeypatch.setattr(handler, "decrypt", lambda _c: b"\x11" * 32)
    monkeypatch.setattr(handler, "mix_into_key", lambda _d: None)
    monkeypatch.setattr(handler, "authenticate", lambda _d: None)

    with pytest.raises(ValueError, match="missing payload"):
        handler.process_handshake({"serverHello": {"ephemeral": b"\x22" * 32, "static": b"\x33" * 48}}, noise_key)


def test_process_handshake_rejects_invalid_noise_certificate(monkeypatch: pytest.MonkeyPatch) -> None:
    key_pair = Curve.generate_key_pair()
    noise_key = Curve.generate_key_pair()
    handler = make_noise_handler(key_pair=key_pair, NOISE_HEADER=NOISE_WA_HEADER, logger=_Logger())
    cert_payload = _build_cert_chain_payload()

    outputs = iter([b"\x77" * 32, cert_payload])
    monkeypatch.setattr("wassupweb.utils.noise_handler.Curve.shared_key", lambda _a, _b: b"\x88" * 32)
    monkeypatch.setattr("wassupweb.utils.noise_handler.Curve.verify", lambda _pk, _msg, _sig: False)
    monkeypatch.setattr(handler, "decrypt", lambda _c: next(outputs))
    monkeypatch.setattr(handler, "mix_into_key", lambda _d: None)
    monkeypatch.setattr(handler, "authenticate", lambda _d: None)
    monkeypatch.setattr(handler, "encrypt", lambda _d: b"encrypted-noise-key")

    with pytest.raises(ValueError, match="noise certificate signature invalid"):
        handler.process_handshake(
            {
                "serverHello": {
                    "ephemeral": b"\x99" * 32,
                    "static": b"\xaa" * 48,
                    "payload": b"\xbb",
                }
            },
            noise_key,
        )


def test_process_handshake_validates_cert_chain_and_returns_key(monkeypatch: pytest.MonkeyPatch) -> None:
    key_pair = Curve.generate_key_pair()
    noise_key = Curve.generate_key_pair()
    handler = make_noise_handler(key_pair=key_pair, NOISE_HEADER=NOISE_WA_HEADER, logger=_Logger())
    cert_payload = _build_cert_chain_payload()

    outputs = iter([b"\x01" * 32, cert_payload])
    verify_calls: list[tuple[bytes, bytes, bytes]] = []

    def _verify(pub: bytes, msg: bytes, sig: bytes) -> bool:
        verify_calls.append((pub, msg, sig))
        return True

    monkeypatch.setattr("wassupweb.utils.noise_handler.Curve.shared_key", lambda _a, _b: b"\x02" * 32)
    monkeypatch.setattr("wassupweb.utils.noise_handler.Curve.verify", _verify)
    monkeypatch.setattr(handler, "decrypt", lambda _c: next(outputs))
    monkeypatch.setattr(handler, "mix_into_key", lambda _d: None)
    monkeypatch.setattr(handler, "authenticate", lambda _d: None)
    monkeypatch.setattr(handler, "encrypt", lambda _d: b"encrypted-noise-key")

    key_enc = handler.process_handshake(
        {
            "serverHello": {
                "ephemeral": b"\x03" * 32,
                "static": b"\x04" * 48,
                "payload": b"\x05",
            }
        },
        noise_key,
    )

    assert key_enc == b"encrypted-noise-key"
    assert len(verify_calls) == 2


def test_process_handshake_compat_mode_skips_bad_noise_certificate(monkeypatch: pytest.MonkeyPatch) -> None:
    key_pair = Curve.generate_key_pair()
    noise_key = Curve.generate_key_pair()
    handler = make_noise_handler(
        key_pair=key_pair,
        NOISE_HEADER=NOISE_WA_HEADER,
        logger=_Logger(),
        strict_cert_validation=False,
    )
    cert_payload = _build_cert_chain_payload()

    outputs = iter([b"\x10" * 32, cert_payload])
    monkeypatch.setattr("wassupweb.utils.noise_handler.Curve.shared_key", lambda _a, _b: b"\x11" * 32)
    monkeypatch.setattr("wassupweb.utils.noise_handler.Curve.verify", lambda _pk, _msg, _sig: False)
    monkeypatch.setattr(handler, "decrypt", lambda _c: next(outputs))
    monkeypatch.setattr(handler, "mix_into_key", lambda _d: None)
    monkeypatch.setattr(handler, "authenticate", lambda _d: None)
    monkeypatch.setattr(handler, "encrypt", lambda _d: b"encrypted-noise-key")

    key_enc = handler.process_handshake(
        {
            "serverHello": {
                "ephemeral": b"\x12" * 32,
                "static": b"\x13" * 48,
                "payload": b"\x14",
            }
        },
        noise_key,
    )
    assert key_enc == b"encrypted-noise-key"
