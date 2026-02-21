from __future__ import annotations

from .sender_chain_key import SenderChainKey
from .sender_message_key import SenderMessageKey


class SenderKeyState:
    MAX_MESSAGE_KEYS = 2000

    def __init__(
        self,
        key_id: int | None = None,
        iteration: int | None = None,
        chain_key: bytes | None = None,
        signature_key_pair: dict[str, bytes] | None = None,
        signature_key_public: bytes | None = None,
        signature_key_private: bytes | None = None,
        sender_key_state_structure: dict[str, object] | None = None,
    ) -> None:
        if sender_key_state_structure is not None:
            self._state = dict(sender_key_state_structure)
            self._state["senderMessageKeys"] = list(self._state.get("senderMessageKeys") or [])
        else:
            if signature_key_pair:
                signature_key_public = signature_key_pair["public"]
                signature_key_private = signature_key_pair["private"]
            self._state = {
                "senderKeyId": key_id or 0,
                "senderChainKey": {
                    "iteration": iteration or 0,
                    "seed": bytes(chain_key or b""),
                },
                "senderSigningKey": {
                    "public": bytes(signature_key_public or b""),
                    "private": bytes(signature_key_private or b""),
                },
                "senderMessageKeys": [],
            }

    def get_key_id(self) -> int:
        return int(self._state["senderKeyId"])

    def get_sender_chain_key(self) -> SenderChainKey:
        chain = self._state["senderChainKey"]
        return SenderChainKey(int(chain["iteration"]), bytes(chain["seed"]))

    def set_sender_chain_key(self, chain_key: SenderChainKey) -> None:
        self._state["senderChainKey"] = {
            "iteration": chain_key.get_iteration(),
            "seed": chain_key.get_seed(),
        }

    def get_signing_key_public(self) -> bytes:
        public_key = bytes(self._state["senderSigningKey"]["public"])
        if len(public_key) == 32:
            return bytes([0x05]) + public_key
        return public_key

    def get_signing_key_private(self) -> bytes:
        private_key = self._state["senderSigningKey"].get("private") or b""
        return bytes(private_key)

    def has_sender_message_key(self, iteration: int) -> bool:
        return any(int(key["iteration"]) == iteration for key in self._state["senderMessageKeys"])

    def add_sender_message_key(self, sender_message_key: SenderMessageKey) -> None:
        keys = self._state["senderMessageKeys"]
        keys.append(
            {
                "iteration": sender_message_key.get_iteration(),
                "seed": sender_message_key.get_seed(),
            }
        )
        if len(keys) > self.MAX_MESSAGE_KEYS:
            keys.pop(0)

    def remove_sender_message_key(self, iteration: int) -> SenderMessageKey | None:
        keys = self._state["senderMessageKeys"]
        for idx, item in enumerate(keys):
            if int(item["iteration"]) == iteration:
                found = keys.pop(idx)
                return SenderMessageKey(int(found["iteration"]), bytes(found["seed"]))
        return None

    def get_structure(self) -> dict[str, object]:
        return self._state

    # camelCase aliases for Baileys API parity
    getKeyId = get_key_id
    getSenderChainKey = get_sender_chain_key
    setSenderChainKey = set_sender_chain_key
    getSigningKeyPublic = get_signing_key_public
    getSigningKeyPrivate = get_signing_key_private
    hasSenderMessageKey = has_sender_message_key
    addSenderMessageKey = add_sender_message_key
    removeSenderMessageKey = remove_sender_message_key
    getStructure = get_structure
