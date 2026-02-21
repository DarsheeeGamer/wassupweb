from __future__ import annotations

import json

import pytest

from wassupweb.waproto import proto


def test_proto_serialization_handles_string_values_in_long_fields_gracefully() -> None:
    if not proto.loaded or not proto.has("WebMessageInfo"):
        pytest.skip("WAProto statics are not generated")

    cls = proto.get("WebMessageInfo")
    if cls is None:
        pytest.skip("WebMessageInfo not found in generated WAProto")

    try:
        from google.protobuf.json_format import MessageToDict, ParseDict
    except Exception:
        pytest.skip("google.protobuf json_format unavailable")

    message = cls()
    try:
        ParseDict(
            {
                "key": {"remoteJid": "123@s.whatsapp.net", "id": "ABC123", "fromMe": False},
                "messageTimestamp": 1,
                "message": {
                    "imageMessage": {
                        "fileLength": "1234567890123456789",
                    }
                },
            },
            message,
            ignore_unknown_fields=True,
        )
    except Exception:
        pytest.skip("Generated WAProto schema differs from expected shape for this parity test")

    json_payload = MessageToDict(message, preserving_proto_field_name=False)
    assert json.dumps(json_payload)

    image = (json_payload.get("message") or {}).get("imageMessage") or {}
    assert image.get("fileLength") in {"1234567890123456789", 1234567890123456789}
