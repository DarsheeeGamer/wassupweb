from __future__ import annotations

import asyncio
from typing import Protocol

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from .sender_key_message import SenderKeyMessage
from .sender_key_name import SenderKeyName
from .sender_key_record import SenderKeyRecord
from .sender_key_state import SenderKeyState


class SenderKeyStoreProtocol(Protocol):
    async def load_sender_key(self, sender_key_name: SenderKeyName) -> SenderKeyRecord:
        ...

    async def store_sender_key(self, sender_key_name: SenderKeyName, record: SenderKeyRecord) -> None:
        ...


class GroupCipher:
    def __init__(self, sender_key_store: SenderKeyStoreProtocol, sender_key_name: SenderKeyName) -> None:
        self._sender_key_store = sender_key_store
        self._sender_key_name = sender_key_name

    async def _load_sender_key(self) -> SenderKeyRecord:
        loader = getattr(self._sender_key_store, "load_sender_key", None) or getattr(
            self._sender_key_store, "loadSenderKey", None
        )
        if not callable(loader):
            raise RuntimeError("sender key store must implement load_sender_key/loadSenderKey")
        result = loader(self._sender_key_name)
        if asyncio.iscoroutine(result):
            result = await result
        return result

    async def _store_sender_key(self, record: SenderKeyRecord) -> None:
        storer = getattr(self._sender_key_store, "store_sender_key", None) or getattr(
            self._sender_key_store, "storeSenderKey", None
        )
        if not callable(storer):
            raise RuntimeError("sender key store must implement store_sender_key/storeSenderKey")
        result = storer(self._sender_key_name, record)
        if asyncio.iscoroutine(result):
            await result

    async def encrypt(self, padded_plaintext: bytes) -> bytes:
        record = await self._load_sender_key()
        if not record:
            raise RuntimeError("No SenderKeyRecord found for encryption")

        sender_key_state = record.get_sender_key_state()
        if not sender_key_state:
            raise RuntimeError("No session to encrypt message")

        iteration = sender_key_state.get_sender_chain_key().get_iteration()
        sender_key = self._get_sender_key(sender_key_state, 0 if iteration == 0 else iteration + 1)

        ciphertext = self._encrypt(sender_key.get_iv(), sender_key.get_cipher_key(), padded_plaintext)
        sender_key_message = SenderKeyMessage(
            sender_key_state.get_key_id(),
            sender_key.get_iteration(),
            ciphertext,
            sender_key_state.get_signing_key_private(),
        )
        await self._store_sender_key(record)
        return sender_key_message.serialize()

    async def decrypt(self, sender_key_message_bytes: bytes) -> bytes:
        record = await self._load_sender_key()
        if not record:
            raise RuntimeError("No SenderKeyRecord found for decryption")

        sender_key_message = SenderKeyMessage(serialized=sender_key_message_bytes)
        sender_key_state = record.get_sender_key_state(sender_key_message.get_key_id())
        if not sender_key_state:
            raise RuntimeError("No session found to decrypt message")

        sender_key_message.verify_signature(sender_key_state.get_signing_key_public())
        sender_key = self._get_sender_key(sender_key_state, sender_key_message.get_iteration())
        plaintext = self._decrypt(sender_key.get_iv(), sender_key.get_cipher_key(), sender_key_message.get_cipher_text())
        await self._store_sender_key(record)
        return plaintext

    def _get_sender_key(self, sender_key_state: SenderKeyState, iteration: int):
        sender_chain_key = sender_key_state.get_sender_chain_key()
        if sender_chain_key.get_iteration() > iteration:
            if sender_key_state.has_sender_message_key(iteration):
                message_key = sender_key_state.remove_sender_message_key(iteration)
                if not message_key:
                    raise RuntimeError("No sender message key found for iteration")
                return message_key
            raise RuntimeError(
                f"Received message with old counter: {sender_chain_key.get_iteration()}, {iteration}"
            )

        if iteration - sender_chain_key.get_iteration() > 2000:
            raise RuntimeError("Over 2000 messages into the future")

        while sender_chain_key.get_iteration() < iteration:
            sender_key_state.add_sender_message_key(sender_chain_key.get_sender_message_key())
            sender_chain_key = sender_chain_key.get_next()

        sender_key_state.set_sender_chain_key(sender_chain_key.get_next())
        return sender_chain_key.get_sender_message_key()

    @staticmethod
    def _encrypt(iv: bytes, key: bytes, plaintext: bytes) -> bytes:
        cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
        encryptor = cipher.encryptor()
        padder = padding.PKCS7(algorithms.AES.block_size).padder()
        padded = padder.update(plaintext) + padder.finalize()
        return encryptor.update(padded) + encryptor.finalize()

    @staticmethod
    def _decrypt(iv: bytes, key: bytes, ciphertext: bytes) -> bytes:
        cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
        decryptor = cipher.decryptor()
        padded = decryptor.update(ciphertext) + decryptor.finalize()
        unpadder = padding.PKCS7(algorithms.AES.block_size).unpadder()
        return unpadder.update(padded) + unpadder.finalize()
