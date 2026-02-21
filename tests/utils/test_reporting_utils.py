from __future__ import annotations

import os

import pytest

from wassupweb.utils.reporting_utils import get_message_reporting_token, should_include_reporting_token
from wassupweb.wabinary import BinaryNode


def _encode_varint(value: int) -> bytes:
    out = bytearray()
    remaining = value
    while remaining > 0x7F:
        out.append((remaining & 0x7F) | 0x80)
        remaining >>= 7
    out.append(remaining)
    return bytes(out)


def _encode_bytes_field(field_num: int, value: bytes) -> bytes:
    tag = (field_num << 3) | 2
    return _encode_varint(tag) + _encode_varint(len(value)) + value


def _create_key(**overrides: object) -> dict[str, object]:
    key: dict[str, object] = {
        "id": "test-id",
        "fromMe": True,
        "remoteJid": "123@s.whatsapp.net",
    }
    key.update(overrides)
    return key


def _with_secret(content: dict[str, object], secret: bytes | None = None) -> dict[str, object]:
    msg = dict(content)
    msg["messageContextInfo"] = {"messageSecret": secret or os.urandom(32)}
    return msg


def _get_token(result: BinaryNode | None) -> bytes | None:
    if not result or not isinstance(result.content, list) or not result.content:
        return None
    item = result.content[0]
    return item.content if isinstance(item.content, (bytes, bytearray)) else None


@pytest.mark.parametrize(
    "message",
    [
        {"conversation": "Hello"},
        {"extendedTextMessage": {"text": "Link"}},
        {"imageMessage": {"url": "url", "mimetype": "image/jpeg"}},
        {"videoMessage": {"url": "url", "mimetype": "video/mp4"}},
        {"documentMessage": {"url": "url", "mimetype": "application/pdf"}},
        {"audioMessage": {"url": "url", "mimetype": "audio/ogg"}},
        {"stickerMessage": {"url": "url", "mimetype": "image/webp"}},
    ],
)
def test_should_include_reporting_token_true_for_normal_messages(message: dict[str, object]) -> None:
    assert should_include_reporting_token(message) is True


@pytest.mark.parametrize(
    "message",
    [
        {"reactionMessage": {"key": {"id": "id"}, "text": "+1"}},
        {"encReactionMessage": {"targetMessageKey": {"id": "id"}, "encPayload": b"x", "encIv": b"x"}},
        {
            "pollUpdateMessage": {
                "pollCreationMessageKey": {"id": "id"},
                "vote": {"encPayload": b"x", "encIv": b"x"},
            }
        },
        {
            "encEventResponseMessage": {
                "eventCreationMessageKey": {"id": "id"},
                "encPayload": b"x",
                "encIv": b"x",
            }
        },
    ],
)
def test_should_include_reporting_token_false_for_excluded_messages(message: dict[str, object]) -> None:
    assert should_include_reporting_token(message) is False


@pytest.mark.asyncio
async def test_get_message_reporting_token_returns_none_without_secret() -> None:
    message = {"conversation": "Hello"}
    msg_protobuf = _encode_bytes_field(1, b"Hello")
    assert await get_message_reporting_token(msg_protobuf, message, _create_key()) is None


@pytest.mark.asyncio
async def test_get_message_reporting_token_returns_none_without_key_id() -> None:
    message = _with_secret({"conversation": "Hello"})
    msg_protobuf = _encode_bytes_field(1, b"Hello")
    assert await get_message_reporting_token(msg_protobuf, message, _create_key(id="")) is None


@pytest.mark.asyncio
async def test_get_message_reporting_token_returns_valid_node() -> None:
    message = _with_secret({"conversation": "Hello"})
    msg_protobuf = _encode_bytes_field(1, b"Hello")
    result = await get_message_reporting_token(msg_protobuf, message, _create_key())

    assert result is not None
    assert result.tag == "reporting"
    assert result.attrs == {}
    assert isinstance(result.content, list)
    assert len(result.content) == 1
    child = result.content[0]
    assert child.tag == "reporting_token"
    assert child.attrs == {"v": "2"}
    assert isinstance(child.content, (bytes, bytearray))
    assert len(child.content) == 16


@pytest.mark.asyncio
async def test_get_message_reporting_token_consistent_for_same_input() -> None:
    secret = os.urandom(32)
    message = _with_secret({"conversation": "Test"}, secret)
    key = _create_key()
    msg_protobuf = _encode_bytes_field(1, b"Test")

    token1 = _get_token(await get_message_reporting_token(msg_protobuf, message, key))
    token2 = _get_token(await get_message_reporting_token(msg_protobuf, message, key))
    assert token1 == token2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "setup",
    [
        lambda: (
            _with_secret({"conversation": "Same"}, os.urandom(32)),
            _create_key(),
            _with_secret({"conversation": "Same"}, os.urandom(32)),
            _create_key(),
        ),
        lambda: (
            _with_secret({"conversation": "Same"}, os.urandom(32)),
            _create_key(id="id-1"),
            _with_secret({"conversation": "Same"}, os.urandom(32)),
            _create_key(id="id-2"),
        ),
        lambda: (
            _with_secret({"conversation": "Same"}, os.urandom(32)),
            _create_key(remoteJid="111@s.whatsapp.net"),
            _with_secret({"conversation": "Same"}, os.urandom(32)),
            _create_key(remoteJid="222@s.whatsapp.net"),
        ),
    ],
)
async def test_get_message_reporting_token_changes_with_key_material(
    setup: object,
) -> None:
    msg1, key1, msg2, key2 = setup()  # type: ignore[misc,operator]
    payload = _encode_bytes_field(1, b"Same")

    token1 = _get_token(await get_message_reporting_token(payload, msg1, key1))
    token2 = _get_token(await get_message_reporting_token(payload, msg2, key2))
    assert token1 != token2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "key_overrides",
    [
        {"remoteJid": "123@g.us", "participant": "456@s.whatsapp.net"},
        {"fromMe": False},
    ],
)
async def test_get_message_reporting_token_handles_group_and_incoming_messages(
    key_overrides: dict[str, object],
) -> None:
    message = _with_secret({"conversation": "Test"})
    key = _create_key(**key_overrides)
    payload = _encode_bytes_field(1, b"Test")
    result = await get_message_reporting_token(payload, message, key)
    assert result is not None
    assert result.tag == "reporting"


@pytest.mark.asyncio
async def test_get_message_reporting_token_returns_none_when_filtered_content_empty() -> None:
    message = _with_secret({"conversation": "Test"})
    payload = _encode_bytes_field(120, b"ignored")
    result = await get_message_reporting_token(payload, message, _create_key())
    assert result is None
