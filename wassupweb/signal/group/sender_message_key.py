from __future__ import annotations

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF


def _derive_secrets(seed: bytes, salt: bytes, info: bytes) -> tuple[bytes, bytes]:
    hkdf = HKDF(algorithm=hashes.SHA256(), length=64, salt=salt, info=info)
    material = hkdf.derive(seed)
    return material[:32], material[32:]


class SenderMessageKey:
    def __init__(self, iteration: int, seed: bytes) -> None:
        first, second = _derive_secrets(seed, b"\x00" * 32, b"WhisperGroup")
        keys = bytearray(32)
        keys[0:16] = first[16:32]
        keys[16:32] = second[0:16]
        self._iv = first[0:16]
        self._cipher_key = bytes(keys)
        self._iteration = iteration
        self._seed = seed

    def get_iteration(self) -> int:
        return self._iteration

    def get_iv(self) -> bytes:
        return self._iv

    def get_cipher_key(self) -> bytes:
        return self._cipher_key

    def get_seed(self) -> bytes:
        return self._seed

    # camelCase aliases for Baileys API parity
    getIteration = get_iteration
    getIv = get_iv
    getCipherKey = get_cipher_key
    getSeed = get_seed
