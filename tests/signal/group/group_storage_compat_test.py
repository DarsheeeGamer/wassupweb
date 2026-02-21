from __future__ import annotations

from wassupweb.signal.group.group_cipher import GroupCipher
from wassupweb.signal.group.group_session_builder import GroupSessionBuilder
from wassupweb.signal.group.keyhelper import generate_sender_signing_key
from wassupweb.signal.group.sender_key_name import Sender, SenderKeyName
from wassupweb.signal.group.sender_key_record import SenderKeyRecord


class _SnakeStore:
    def __init__(self) -> None:
        self.record = SenderKeyRecord()
        self.calls: list[str] = []

    async def load_sender_key(self, _name: SenderKeyName) -> SenderKeyRecord:
        self.calls.append("snake_load")
        return self.record

    async def store_sender_key(self, _name: SenderKeyName, record: SenderKeyRecord) -> None:
        self.calls.append("snake_store")
        self.record = record


class _CamelStore:
    def __init__(self) -> None:
        self.record = SenderKeyRecord()
        self.calls: list[str] = []

    async def loadSenderKey(self, _name: SenderKeyName) -> SenderKeyRecord:  # noqa: N802 - parity alias
        self.calls.append("camel_load")
        return self.record

    async def storeSenderKey(self, _name: SenderKeyName, record: SenderKeyRecord) -> None:  # noqa: N802 - parity alias
        self.calls.append("camel_store")
        self.record = record


async def _seed_record(store: _SnakeStore | _CamelStore) -> None:
    signing = generate_sender_signing_key()
    store.record.set_sender_key_state(1, 0, b"\x01" * 32, signing)


def _sender_key_name() -> SenderKeyName:
    return SenderKeyName("group@g.us", Sender("user", 1))


async def _roundtrip_with_cipher(store: _SnakeStore | _CamelStore) -> None:
    name = _sender_key_name()
    await _seed_record(store)
    receiver = _SnakeStore() if isinstance(store, _SnakeStore) else _CamelStore()
    receiver.record = SenderKeyRecord(store.record.serialize())

    sender_cipher = GroupCipher(store, name)
    receiver_cipher = GroupCipher(receiver, name)
    encrypted = await sender_cipher.encrypt(b"hello")
    decrypted = await receiver_cipher.decrypt(encrypted)
    assert decrypted == b"hello"


async def _exercise_builder(store: _SnakeStore | _CamelStore) -> None:
    builder = GroupSessionBuilder(store)
    name = _sender_key_name()
    dist = await builder.create(name)
    await builder.process(name, dist)


import pytest


@pytest.mark.asyncio
async def test_group_cipher_accepts_snake_store_methods() -> None:
    store = _SnakeStore()
    await _roundtrip_with_cipher(store)
    assert "snake_load" in store.calls
    assert "snake_store" in store.calls


@pytest.mark.asyncio
async def test_group_cipher_accepts_camel_store_methods() -> None:
    store = _CamelStore()
    await _roundtrip_with_cipher(store)
    assert "camel_load" in store.calls
    assert "camel_store" in store.calls


@pytest.mark.asyncio
async def test_group_session_builder_accepts_snake_and_camel_store_methods() -> None:
    snake = _SnakeStore()
    camel = _CamelStore()
    await _exercise_builder(snake)
    await _exercise_builder(camel)
    assert "snake_load" in snake.calls and "snake_store" in snake.calls
    assert "camel_load" in camel.calls and "camel_store" in camel.calls
