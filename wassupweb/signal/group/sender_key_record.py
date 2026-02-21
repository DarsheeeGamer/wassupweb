from __future__ import annotations

import json
import base64
from typing import Any

from .sender_key_state import SenderKeyState


def _json_default(value: Any) -> Any:
    if isinstance(value, (bytes, bytearray)):
        return {"__type__": "bytes", "data": base64.b64encode(bytes(value)).decode("ascii")}
    raise TypeError(f"Unsupported type in SenderKeyRecord serialization: {type(value)!r}")


def _json_object_hook(value: dict[str, Any]) -> Any:
    if value.get("__type__") == "bytes":
        return base64.b64decode(value["data"])
    return value


class SenderKeyRecord:
    MAX_STATES = 5

    def __init__(self, serialized: list[dict[str, object]] | None = None) -> None:
        self._sender_key_states: list[SenderKeyState] = []
        if serialized:
            for structure in serialized:
                self._sender_key_states.append(
                    SenderKeyState(
                        None,
                        None,
                        None,
                        None,
                        None,
                        None,
                        structure,
                    )
                )

    def is_empty(self) -> bool:
        return len(self._sender_key_states) == 0

    def get_sender_key_state(self, key_id: int | None = None) -> SenderKeyState | None:
        if key_id is None and self._sender_key_states:
            return self._sender_key_states[-1]
        return next((state for state in self._sender_key_states if state.get_key_id() == key_id), None)

    def add_sender_key_state(self, key_id: int, iteration: int, chain_key: bytes, signature_key: bytes) -> None:
        self._sender_key_states.append(SenderKeyState(key_id, iteration, chain_key, None, signature_key))
        if len(self._sender_key_states) > self.MAX_STATES:
            self._sender_key_states.pop(0)

    def set_sender_key_state(self, key_id: int, iteration: int, chain_key: bytes, key_pair: dict[str, bytes]) -> None:
        self._sender_key_states.clear()
        self._sender_key_states.append(SenderKeyState(key_id, iteration, chain_key, key_pair))

    def serialize(self) -> list[dict[str, object]]:
        return [state.get_structure() for state in self._sender_key_states]

    def serialize_bytes(self) -> bytes:
        return json.dumps(self.serialize(), default=_json_default, separators=(",", ":")).encode("utf-8")

    @classmethod
    def deserialize(cls, data: bytes) -> "SenderKeyRecord":
        parsed = json.loads(data.decode("utf-8"), object_hook=_json_object_hook)
        return cls(parsed)

    # camelCase aliases for Baileys API parity
    isEmpty = is_empty
    getSenderKeyState = get_sender_key_state
    addSenderKeyState = add_sender_key_state
    setSenderKeyState = set_sender_key_state
