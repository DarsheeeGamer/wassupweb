from __future__ import annotations

from wassupweb.signal.group.keyhelper import (
    generate_sender_key,
    generate_sender_signing_key,
    generateSenderKey,
    generateSenderKeyId,
    generateSenderSigningKey,
)
from wassupweb.signal.group.sender_chain_key import SenderChainKey
from wassupweb.signal.group.sender_key_distribution_message import SenderKeyDistributionMessage
from wassupweb.signal.group.sender_key_message import SenderKeyMessage
from wassupweb.signal.group.sender_key_name import Sender, SenderKeyName
from wassupweb.signal.group.sender_key_record import SenderKeyRecord
from wassupweb.signal.group.sender_key_state import SenderKeyState
from wassupweb.signal.group.sender_message_key import SenderMessageKey


def test_keyhelper_camelcase_aliases() -> None:
    sender_key = generateSenderKey()
    assert sender_key == generate_sender_key() or len(sender_key) == 32
    assert isinstance(generateSenderKeyId(), int)
    signing_key = generateSenderSigningKey()
    assert signing_key["public"]
    assert signing_key["private"]


def test_sender_message_and_chain_key_aliases() -> None:
    message_key = SenderMessageKey(3, b"\x01" * 32)
    assert message_key.getIteration() == message_key.get_iteration()
    assert message_key.getIv() == message_key.get_iv()
    assert message_key.getCipherKey() == message_key.get_cipher_key()
    assert message_key.getSeed() == message_key.get_seed()

    chain_key = SenderChainKey(7, b"\x02" * 32)
    assert chain_key.getIteration() == chain_key.get_iteration()
    assert chain_key.getSeed() == chain_key.get_seed()
    next_chain = chain_key.getNext()
    assert next_chain.getIteration() == 8
    assert chain_key.getSenderMessageKey().getIteration() == 7


def test_sender_key_state_and_record_aliases() -> None:
    signing = generate_sender_signing_key()
    state = SenderKeyState(42, 0, b"\x03" * 32, signing)
    msg_key = SenderMessageKey(0, b"\x04" * 32)
    state.addSenderMessageKey(msg_key)
    assert state.getKeyId() == 42
    assert state.hasSenderMessageKey(0)
    removed = state.removeSenderMessageKey(0)
    assert removed is not None
    assert state.getStructure()["senderKeyId"] == 42

    record = SenderKeyRecord()
    assert record.isEmpty()
    record.setSenderKeyState(7, 0, b"\x05" * 32, signing)
    current = record.getSenderKeyState()
    assert current is not None
    assert current.getKeyId() == 7


def test_sender_key_message_and_distribution_aliases() -> None:
    signing = generate_sender_signing_key()
    message = SenderKeyMessage(11, 2, b"cipher", signing["private"])
    assert message.getKeyId() == 11
    assert message.getIteration() == 2
    assert message.getCipherText() == b"cipher"
    message.verifySignature(signing["public"])
    assert message.getType() == message.get_type()

    distribution = SenderKeyDistributionMessage(15, 4, b"\x09" * 32, signing["public"])
    assert distribution.getId() == 15
    assert distribution.getIteration() == 4
    assert distribution.getChainKey() == b"\x09" * 32
    assert distribution.getSignatureKey() == signing["public"]
    assert distribution.getType() == distribution.get_type()


def test_sender_key_name_aliases() -> None:
    name = SenderKeyName("group-1", Sender("alice", 1))
    assert name.getGroupId() == "group-1"
    assert str(name.getSender()) == "alice:1"
    assert name.hashCode() == name.hash_code()
