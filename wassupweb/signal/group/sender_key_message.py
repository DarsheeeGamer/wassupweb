from __future__ import annotations

import struct

from ...utils.crypto import Curve
from .ciphertext_message import CiphertextMessage


class SenderKeyMessage(CiphertextMessage):
    SIGNATURE_LENGTH = 64

    def __init__(
        self,
        key_id: int | None = None,
        iteration: int | None = None,
        ciphertext: bytes | None = None,
        signature_key: bytes | None = None,
        serialized: bytes | None = None,
    ) -> None:
        if serialized is not None:
            self._from_serialized(serialized)
            return

        if key_id is None or iteration is None or ciphertext is None or signature_key is None:
            raise ValueError("Missing parameters for SenderKeyMessage construction")

        version = (((self.CURRENT_VERSION << 4) | self.CURRENT_VERSION) & 0xFF) % 256
        body = bytearray()
        body.append(version)
        body.extend(struct.pack("<I", key_id))
        body.extend(struct.pack("<I", iteration))
        body.extend(struct.pack("<I", len(ciphertext)))
        body.extend(ciphertext)
        signature = self._get_signature(signature_key, bytes(body))

        self._serialized = bytes(body + signature)
        self._message_version = self.CURRENT_VERSION
        self._key_id = key_id
        self._iteration = iteration
        self._ciphertext = bytes(ciphertext)
        self._signature = signature

    def _from_serialized(self, serialized: bytes) -> None:
        if len(serialized) < 1 + 4 + 4 + 4 + self.SIGNATURE_LENGTH:
            raise ValueError("invalid SenderKeyMessage payload")
        self._serialized = bytes(serialized)
        version = serialized[0]
        self._message_version = (version & 0xFF) >> 4

        body = serialized[:-self.SIGNATURE_LENGTH]
        self._signature = serialized[-self.SIGNATURE_LENGTH :]

        offset = 1
        self._key_id = struct.unpack_from("<I", body, offset)[0]
        offset += 4
        self._iteration = struct.unpack_from("<I", body, offset)[0]
        offset += 4
        cipher_len = struct.unpack_from("<I", body, offset)[0]
        offset += 4
        self._ciphertext = body[offset : offset + cipher_len]

    def get_key_id(self) -> int:
        return self._key_id

    def get_iteration(self) -> int:
        return self._iteration

    def get_cipher_text(self) -> bytes:
        return self._ciphertext

    def verify_signature(self, signature_key: bytes) -> None:
        key = signature_key
        if len(key) == 33 and key[0] == 0x05:
            key = key[1:]
        body = self._serialized[:-self.SIGNATURE_LENGTH]
        sig = self._serialized[-self.SIGNATURE_LENGTH :]
        if not Curve.verify(key, body, sig):
            raise ValueError("Invalid signature")

    @staticmethod
    def _get_signature(signature_key: bytes, serialized: bytes) -> bytes:
        key = signature_key[:32]
        return Curve.sign(key, serialized)

    def serialize(self) -> bytes:
        return self._serialized

    def get_type(self) -> int:
        return self.SENDERKEY_TYPE

    # camelCase aliases for Baileys API parity
    getKeyId = get_key_id
    getIteration = get_iteration
    getCipherText = get_cipher_text
    verifySignature = verify_signature
    getType = get_type
