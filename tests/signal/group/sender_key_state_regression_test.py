from __future__ import annotations

from wassupweb.signal.group.sender_key_state import SenderKeyState
from wassupweb.signal.group.sender_message_key import SenderMessageKey


def test_sender_key_state_initializes_sender_message_keys_when_absent() -> None:
    legacy_structure = {
        "senderKeyId": 42,
        "senderChainKey": {"iteration": 0, "seed": bytes([1, 2, 3])},
        "senderSigningKey": {"public": bytes([4, 5, 6])},
    }

    state = SenderKeyState(sender_key_state_structure=legacy_structure)
    msg_key = SenderMessageKey(0, bytes([7, 8, 9]))
    state.add_sender_message_key(msg_key)

    structure = state.get_structure()
    assert "senderMessageKeys" in structure
    assert isinstance(structure["senderMessageKeys"], list)
    assert len(structure["senderMessageKeys"]) == 1
    assert structure["senderMessageKeys"][0]["iteration"] == 0
