from __future__ import annotations

import asyncio
from typing import Protocol

from . import keyhelper
from .sender_key_distribution_message import SenderKeyDistributionMessage
from .sender_key_name import SenderKeyName
from .sender_key_record import SenderKeyRecord


class SenderKeyStoreProtocol(Protocol):
    async def load_sender_key(self, sender_key_name: SenderKeyName) -> SenderKeyRecord:
        ...

    async def store_sender_key(self, sender_key_name: SenderKeyName, record: SenderKeyRecord) -> None:
        ...


class GroupSessionBuilder:
    def __init__(self, sender_key_store: SenderKeyStoreProtocol) -> None:
        self._sender_key_store = sender_key_store

    async def _load_sender_key(self, sender_key_name: SenderKeyName) -> SenderKeyRecord:
        loader = getattr(self._sender_key_store, "load_sender_key", None) or getattr(
            self._sender_key_store, "loadSenderKey", None
        )
        if not callable(loader):
            raise RuntimeError("sender key store must implement load_sender_key/loadSenderKey")
        result = loader(sender_key_name)
        if asyncio.iscoroutine(result):
            result = await result
        return result

    async def _store_sender_key(self, sender_key_name: SenderKeyName, record: SenderKeyRecord) -> None:
        storer = getattr(self._sender_key_store, "store_sender_key", None) or getattr(
            self._sender_key_store, "storeSenderKey", None
        )
        if not callable(storer):
            raise RuntimeError("sender key store must implement store_sender_key/storeSenderKey")
        result = storer(sender_key_name, record)
        if asyncio.iscoroutine(result):
            await result

    async def process(
        self,
        sender_key_name: SenderKeyName,
        sender_key_distribution_message: SenderKeyDistributionMessage,
    ) -> None:
        sender_key_record = await self._load_sender_key(sender_key_name)
        sender_key_record.add_sender_key_state(
            sender_key_distribution_message.get_id(),
            sender_key_distribution_message.get_iteration(),
            sender_key_distribution_message.get_chain_key(),
            sender_key_distribution_message.get_signature_key(),
        )
        await self._store_sender_key(sender_key_name, sender_key_record)

    async def create(self, sender_key_name: SenderKeyName) -> SenderKeyDistributionMessage:
        sender_key_record = await self._load_sender_key(sender_key_name)
        if sender_key_record.is_empty():
            key_id = keyhelper.generate_sender_key_id()
            sender_key = keyhelper.generate_sender_key()
            signing_key = keyhelper.generate_sender_signing_key()
            sender_key_record.set_sender_key_state(key_id, 0, sender_key, signing_key)
            await self._store_sender_key(sender_key_name, sender_key_record)

        state = sender_key_record.get_sender_key_state()
        if not state:
            raise RuntimeError("No session state available")

        chain_key = state.get_sender_chain_key()
        return SenderKeyDistributionMessage(
            state.get_key_id(),
            chain_key.get_iteration(),
            chain_key.get_seed(),
            state.get_signing_key_public(),
        )
