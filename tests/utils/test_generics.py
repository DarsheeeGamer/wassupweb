from __future__ import annotations

import json

from wassupweb.utils.generics import BufferJSON


def _serialize_roundtrip(value: object) -> object:
    serialized = json.dumps(BufferJSON.replacer(None, value))
    return BufferJSON.reviver(None, json.loads(serialized))


def test_buffer_json_round_trip() -> None:
    original = {
        "id": 1,
        "key": bytes([1, 2, 3, 4, 5]),
        "nested": {"data": bytes([6, 7, 8])},
    }
    revived = _serialize_roundtrip(original)

    assert isinstance(revived, dict)
    assert revived["key"] == original["key"]
    assert revived["nested"]["data"] == original["nested"]["data"]
    assert revived == original


def test_buffer_json_legacy_object_like_buffer() -> None:
    legacy = {
        "id": 1,
        "key": {"0": 1, "1": 2, "2": 3, "3": 4, "4": 5},
        "nested": {"data": {"0": 6, "1": 7, "2": 8}},
    }
    revived = BufferJSON.reviver(None, legacy)

    assert revived["key"] == bytes([1, 2, 3, 4, 5])
    assert revived["nested"]["data"] == bytes([6, 7, 8])


def test_buffer_json_does_not_corrupt_legit_objects() -> None:
    legit = {"0": "some-value", "1": "another-value"}
    revived = BufferJSON.reviver(None, legit)
    assert revived == legit


def test_buffer_json_does_not_convert_other_objects_or_nulls() -> None:
    other = {"a": 1, "b": 2, "c": None, "d": {"0": 1, "foo": "bar"}}
    revived = _serialize_roundtrip(other)
    assert revived == other


def test_buffer_json_handles_empty_object() -> None:
    revived = BufferJSON.reviver(None, {})
    assert revived == {}
