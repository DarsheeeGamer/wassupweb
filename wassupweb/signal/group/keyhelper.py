from __future__ import annotations

import secrets

from nacl.signing import SigningKey


def generate_sender_key() -> bytes:
    return secrets.token_bytes(32)


def generate_sender_key_id() -> int:
    return secrets.randbelow(2_147_483_647)


def generate_sender_signing_key(key: dict[str, bytes] | None = None) -> dict[str, bytes]:
    if key is None:
        signing_key = SigningKey.generate()
        public = bytes(signing_key.verify_key)
        private = bytes(signing_key)
    else:
        public = key["public"]
        private = key["private"]
    return {"public": public, "private": private}


# camelCase aliases for Baileys API parity
generateSenderKey = generate_sender_key
generateSenderKeyId = generate_sender_key_id
generateSenderSigningKey = generate_sender_signing_key
