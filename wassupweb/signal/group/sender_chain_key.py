from __future__ import annotations

import hmac
import hashlib

from .sender_message_key import SenderMessageKey


class SenderChainKey:
    MESSAGE_KEY_SEED = bytes([0x01])
    CHAIN_KEY_SEED = bytes([0x02])

    def __init__(self, iteration: int, chain_key: bytes) -> None:
        self._iteration = iteration
        self._chain_key = bytes(chain_key)

    def get_iteration(self) -> int:
        return self._iteration

    def get_sender_message_key(self) -> SenderMessageKey:
        return SenderMessageKey(self._iteration, self._get_derivative(self.MESSAGE_KEY_SEED, self._chain_key))

    def get_next(self) -> "SenderChainKey":
        return SenderChainKey(self._iteration + 1, self._get_derivative(self.CHAIN_KEY_SEED, self._chain_key))

    def get_seed(self) -> bytes:
        return self._chain_key

    @staticmethod
    def _get_derivative(seed: bytes, key: bytes) -> bytes:
        return hmac.new(key, seed, hashlib.sha256).digest()

    # camelCase aliases for Baileys API parity
    getIteration = get_iteration
    getSenderMessageKey = get_sender_message_key
    getNext = get_next
    getSeed = get_seed
