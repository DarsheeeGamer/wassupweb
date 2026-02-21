from __future__ import annotations

from dataclasses import dataclass


def _int_value(num: int) -> int:
    max_value = 0x7FFFFFFF
    min_value = -0x80000000
    if num > max_value or num < min_value:
        return num & 0xFFFFFFFF
    return num


def _hash_code(text: str | None) -> int:
    value = 0
    if text:
        for char in text:
            value = _int_value(value * 31 + ord(char))
    return value


@dataclass(slots=True)
class Sender:
    id: str
    device_id: int

    def __str__(self) -> str:
        return f"{self.id}:{self.device_id}"


class SenderKeyName:
    def __init__(self, group_id: str, sender: Sender) -> None:
        self._group_id = group_id
        self._sender = sender

    def get_group_id(self) -> str:
        return self._group_id

    def get_sender(self) -> Sender:
        return self._sender

    def serialize(self) -> str:
        return f"{self._group_id}::{self._sender.id}::{self._sender.device_id}"

    def __str__(self) -> str:
        return self.serialize()

    def equals(self, other: "SenderKeyName | None") -> bool:
        if other is None:
            return False
        return self._group_id == other._group_id and str(self._sender) == str(other._sender)

    def hash_code(self) -> int:
        return _hash_code(self._group_id) ^ _hash_code(str(self._sender))

    # camelCase aliases for Baileys API parity
    getGroupId = get_group_id
    getSender = get_sender
    hashCode = hash_code
