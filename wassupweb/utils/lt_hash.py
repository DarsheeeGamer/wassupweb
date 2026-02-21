from __future__ import annotations

import hashlib
from typing import Iterable


class LTHashAntiTampering:
    """
    Deterministic anti-tampering mixer used for app-state LT hash progression.

    WhatsApp's native implementation uses a rust bridge. This Python port keeps the
    same subtract/add contract and output size (128 bytes) so higher layers remain
    plug-compatible.
    """

    HASH_SIZE = 128

    @staticmethod
    def _expand_value(value_mac: bytes) -> bytes:
        first = hashlib.sha512(value_mac).digest()
        second = hashlib.sha512(value_mac + b"\x01").digest()
        return first + second

    def subtract_then_add(
        self,
        current_hash: bytes | bytearray,
        subtract_values: Iterable[bytes | bytearray],
        add_values: Iterable[bytes | bytearray],
    ) -> bytes:
        base = bytes(current_hash or b"")
        if len(base) < self.HASH_SIZE:
            base = base + bytes(self.HASH_SIZE - len(base))
        elif len(base) > self.HASH_SIZE:
            base = base[: self.HASH_SIZE]

        state = bytearray(base)
        for value in subtract_values:
            expanded = self._expand_value(bytes(value))
            for idx in range(self.HASH_SIZE):
                state[idx] = (state[idx] - expanded[idx]) & 0xFF

        for value in add_values:
            expanded = self._expand_value(bytes(value))
            for idx in range(self.HASH_SIZE):
                state[idx] = (state[idx] + expanded[idx]) & 0xFF

        return bytes(state)


LT_HASH_ANTI_TAMPERING = LTHashAntiTampering()


__all__ = ["LTHashAntiTampering", "LT_HASH_ANTI_TAMPERING"]
