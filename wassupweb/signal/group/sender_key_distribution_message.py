from __future__ import annotations

import struct

from .ciphertext_message import CiphertextMessage


class SenderKeyDistributionMessage(CiphertextMessage):
    def __init__(
        self,
        key_id: int | None = None,
        iteration: int | None = None,
        chain_key: bytes | None = None,
        signature_key: bytes | None = None,
        serialized: bytes | None = None,
    ) -> None:
        if serialized is not None:
            self._from_serialized(serialized)
            return

        if key_id is None or iteration is None or chain_key is None or signature_key is None:
            raise ValueError("Missing parameters for SenderKeyDistributionMessage construction")

        version = self._ints_to_byte_high_and_low(self.CURRENT_VERSION, self.CURRENT_VERSION)
        self._id = key_id
        self._iteration = iteration
        self._chain_key = bytes(chain_key)
        self._signature_key = bytes(signature_key)

        payload = bytearray()
        payload.append(version)
        payload.extend(struct.pack("<I", self._id))
        payload.extend(struct.pack("<I", self._iteration))
        payload.extend(struct.pack("<I", len(self._chain_key)))
        payload.extend(self._chain_key)
        payload.extend(struct.pack("<I", len(self._signature_key)))
        payload.extend(self._signature_key)
        self._serialized = bytes(payload)

    def _from_serialized(self, serialized: bytes) -> None:
        self._serialized = bytes(serialized)
        offset = 1
        self._id = struct.unpack_from("<I", serialized, offset)[0]
        offset += 4
        self._iteration = struct.unpack_from("<I", serialized, offset)[0]
        offset += 4
        chain_len = struct.unpack_from("<I", serialized, offset)[0]
        offset += 4
        self._chain_key = serialized[offset : offset + chain_len]
        offset += chain_len
        sig_len = struct.unpack_from("<I", serialized, offset)[0]
        offset += 4
        self._signature_key = serialized[offset : offset + sig_len]

    @staticmethod
    def _ints_to_byte_high_and_low(high_value: int, low_value: int) -> int:
        return (((high_value << 4) | low_value) & 0xFF) % 256

    def serialize(self) -> bytes:
        return self._serialized

    def get_type(self) -> int:
        return self.SENDERKEY_DISTRIBUTION_TYPE

    def get_iteration(self) -> int:
        return self._iteration

    def get_chain_key(self) -> bytes:
        return self._chain_key

    def get_signature_key(self) -> bytes:
        return self._signature_key

    def get_id(self) -> int:
        return self._id

    # camelCase aliases for Baileys API parity
    getType = get_type
    getIteration = get_iteration
    getChainKey = get_chain_key
    getSignatureKey = get_signature_key
    getId = get_id
