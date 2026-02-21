from __future__ import annotations

from nacl.signing import SigningKey

from wassupweb.utils.crypto import Curve, generate_signal_pub_key


def test_curve_verify_accepts_raw_and_signal_prefixed_public_keys() -> None:
    signing_key = SigningKey.generate()
    verify_key = bytes(signing_key.verify_key)
    message = b"noise-cert-payload"
    signature = bytes(signing_key.sign(message).signature)

    assert Curve.verify(verify_key, message, signature) is True
    assert Curve.verify(generate_signal_pub_key(verify_key), message, signature) is True

