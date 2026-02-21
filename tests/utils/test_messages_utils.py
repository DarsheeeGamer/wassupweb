from __future__ import annotations

import base64
from types import SimpleNamespace
from typing import Any

import pytest

import wassupweb.utils.messages as messages_mod
from wassupweb.utils.messages import (
    download_media_message,
    generate_wa_message,
    generate_wa_message_content,
    generate_wa_message_from_content,
    prepare_wa_message_media,
)


@pytest.mark.asyncio
async def test_generate_wa_message_content_prepares_and_uploads_image(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_encrypted_stream(_media: Any, _media_type: str, _options: dict[str, Any]) -> dict[str, Any]:
        return {
            "encFilePath": "enc.bin",
            "originalFilePath": "orig.bin",
            "mediaKey": b"k" * 32,
            "fileEncSha256": b"e" * 32,
            "fileSha256": b"s" * 32,
            "fileLength": 123,
        }

    async def _fake_thumb(_file: str, _media_type: str, _options: dict[str, Any] | None = None) -> dict[str, Any]:
        return {
            "thumbnail": base64.b64encode(b"thumb").decode("ascii"),
            "originalImageDimensions": {"width": 10, "height": 20},
        }

    async def _fake_upload(path: str, opts: dict[str, Any]) -> dict[str, Any]:
        assert path == "enc.bin"
        assert opts["mediaType"] == "image"
        return {"mediaUrl": "https://mmg.whatsapp.net/x", "directPath": "/mms/image/x"}

    monkeypatch.setattr(messages_mod, "encrypted_stream", _fake_encrypted_stream)
    monkeypatch.setattr(messages_mod, "generate_thumbnail", _fake_thumb)

    result = await generate_wa_message_content(
        {"image": b"raw-bytes", "caption": "hello"},
        {"upload": _fake_upload, "logger": None},
    )
    msg = result["imageMessage"]
    assert msg["url"] == "https://mmg.whatsapp.net/x"
    assert msg["directPath"] == "/mms/image/x"
    assert msg["caption"] == "hello"
    assert msg["jpegThumbnail"] == b"thumb"
    assert msg["width"] == 10
    assert msg["height"] == 20


@pytest.mark.asyncio
async def test_generate_wa_message_content_keeps_preuploaded_media() -> None:
    result = await generate_wa_message_content(
        {"image": {"url": "https://mmg.whatsapp.net/a", "mediaKey": b"k", "directPath": "/mms/image/a"}},
        {},
    )
    assert result["imageMessage"]["url"] == "https://mmg.whatsapp.net/a"
    assert result["imageMessage"]["directPath"] == "/mms/image/a"


@pytest.mark.asyncio
async def test_generate_wa_message_content_requires_upload_for_raw_media() -> None:
    with pytest.raises(ValueError, match="upload function is required for image messages"):
        await generate_wa_message_content({"image": b"raw"}, {})


@pytest.mark.asyncio
async def test_generate_wa_message_content_ptv_uses_ptv_message(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_encrypted_stream(_media: Any, _media_type: str, _options: dict[str, Any]) -> dict[str, Any]:
        return {
            "encFilePath": "enc.bin",
            "originalFilePath": None,
            "mediaKey": b"k" * 32,
            "fileEncSha256": b"e" * 32,
            "fileSha256": b"s" * 32,
            "fileLength": 456,
        }

    async def _fake_upload(_path: str, _opts: dict[str, Any]) -> dict[str, Any]:
        return {"mediaUrl": "https://mmg.whatsapp.net/v", "directPath": "/mms/video/v"}

    monkeypatch.setattr(messages_mod, "encrypted_stream", _fake_encrypted_stream)
    result = await generate_wa_message_content({"ptv": True, "video": b"raw-video"}, {"upload": _fake_upload})
    assert "ptvMessage" in result
    assert result["ptvMessage"]["url"] == "https://mmg.whatsapp.net/v"
    assert "videoMessage" not in result


@pytest.mark.asyncio
async def test_generate_wa_message_content_product_prepares_product_image(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_encrypted_stream(_media: Any, _media_type: str, _options: dict[str, Any]) -> dict[str, Any]:
        return {
            "encFilePath": "enc.bin",
            "originalFilePath": None,
            "mediaKey": b"k" * 32,
            "fileEncSha256": b"e" * 32,
            "fileSha256": b"s" * 32,
            "fileLength": 11,
        }

    async def _fake_upload(_path: str, _opts: dict[str, Any]) -> dict[str, Any]:
        return {"mediaUrl": "https://mmg.whatsapp.net/p", "directPath": "/mms/image/p"}

    monkeypatch.setattr(messages_mod, "encrypted_stream", _fake_encrypted_stream)
    result = await generate_wa_message_content(
        {"product": {"id": "P1", "productImage": b"raw-image"}},
        {"upload": _fake_upload},
    )
    product = result["productMessage"]["product"]
    assert product["id"] == "P1"
    assert product["productImage"]["url"] == "https://mmg.whatsapp.net/p"


@pytest.mark.asyncio
async def test_generate_wa_message_content_newsletter_uses_raw_upload(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_raw(_media: Any, _media_type: str, _logger: Any = None) -> dict[str, Any]:
        return {"filePath": "raw.bin", "fileSha256": b"r" * 32, "fileLength": 99}

    async def _fake_upload(path: str, _opts: dict[str, Any]) -> dict[str, Any]:
        assert path == "raw.bin"
        return {"mediaUrl": "https://mmg.whatsapp.net/n", "directPath": "/mms/image/n"}

    async def _never_encrypted(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise AssertionError("encrypted_stream should not be used for newsletter media")

    monkeypatch.setattr(messages_mod, "get_raw_media_upload_data", _fake_raw)
    monkeypatch.setattr(messages_mod, "encrypted_stream", _never_encrypted)

    result = await generate_wa_message_content(
        {"image": b"newsletter-image"},
        {"upload": _fake_upload, "jid": "abc@newsletter"},
    )
    msg = result["imageMessage"]
    assert msg["url"] == "https://mmg.whatsapp.net/n"
    assert msg["directPath"] == "/mms/image/n"
    assert msg["fileLength"] == 99
    assert msg["fileSha256"] == b"r" * 32
    assert "mediaKey" not in msg


@pytest.mark.asyncio
async def test_generate_wa_message_content_audio_enriches_duration_waveform_and_background(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake_encrypted_stream(_media: Any, _media_type: str, _options: dict[str, Any]) -> dict[str, Any]:
        return {
            "encFilePath": "enc.bin",
            "originalFilePath": "orig.bin",
            "mediaKey": b"k" * 32,
            "fileEncSha256": b"e" * 32,
            "fileSha256": b"s" * 32,
            "fileLength": 5,
        }

    async def _fake_upload(_path: str, _opts: dict[str, Any]) -> dict[str, Any]:
        return {"mediaUrl": "https://mmg.whatsapp.net/a", "directPath": "/mms/audio/a"}

    async def _fake_duration(_path: str) -> float:
        return 12.5

    async def _fake_waveform(_path: str, _logger: Any = None) -> bytes:
        return b"\x01\x02\x03"

    monkeypatch.setattr(messages_mod, "encrypted_stream", _fake_encrypted_stream)
    monkeypatch.setattr(messages_mod, "get_audio_duration", _fake_duration)
    monkeypatch.setattr(messages_mod, "get_audio_waveform", _fake_waveform)

    result = await generate_wa_message_content(
        {"audio": b"raw-audio", "ptt": True},
        {"upload": _fake_upload, "backgroundColor": "#ff0000"},
    )
    msg = result["audioMessage"]
    assert msg["seconds"] == 12.5
    assert msg["waveform"] == b"\x01\x02\x03"
    assert isinstance(msg["backgroundArgb"], int)


@pytest.mark.asyncio
async def test_generate_wa_message_content_event_builds_join_link_from_get_call_link() -> None:
    async def _fake_get_call_link(call_type: str, event: dict[str, int]) -> str:
        assert call_type == "audio"
        assert event["startTime"] == 1735689600
        return "abc-token"

    result = await generate_wa_message_content(
        {
            "event": {
                "name": "Daily Standup",
                "description": "sync",
                "startDate": 1735689600,
                "endDate": 1735693200,
                "call": "audio",
            }
        },
        {"getCallLink": _fake_get_call_link},
    )
    event_msg = result["eventMessage"]
    assert event_msg["joinLink"] == "https://call.whatsapp.com/voice/abc-token"
    assert event_msg["startTime"] == 1735689600


@pytest.mark.asyncio
async def test_download_media_message_returns_buffer(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_download(_media: dict[str, Any], _media_type: str, _opts: dict[str, Any]) -> Any:
        async def _stream() -> Any:
            yield b"a"
            yield b"b"

        return _stream()

    monkeypatch.setattr(messages_mod, "download_content_from_message", _fake_download)
    out = await download_media_message(
        {"message": {"imageMessage": {"url": "https://mmg.whatsapp.net/x", "mediaKey": b"k" * 32}}},
        "buffer",
        {},
    )
    assert out == b"ab"


@pytest.mark.asyncio
async def test_download_media_message_reuploads_on_404(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0

    class _Err(RuntimeError):
        def __init__(self) -> None:
            super().__init__("download failed")
            self.status = 404

    async def _fake_download(media: dict[str, Any], _media_type: str, _opts: dict[str, Any]) -> Any:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise _Err()

        async def _stream() -> Any:
            yield f"ok:{media.get('url')}".encode("utf-8")

        return _stream()

    async def _reupload(_msg: dict[str, Any]) -> dict[str, Any]:
        return {"message": {"imageMessage": {"url": "https://mmg.whatsapp.net/new", "mediaKey": b"k" * 32}}}

    monkeypatch.setattr(messages_mod, "download_content_from_message", _fake_download)
    logger = SimpleNamespace(info=lambda *_args, **_kwargs: None)
    out = await download_media_message(
        {"key": {"id": "m1"}, "message": {"imageMessage": {"url": "https://mmg.whatsapp.net/old", "mediaKey": b"k" * 32}}},
        "buffer",
        {},
        {"reuploadRequest": _reupload, "logger": logger},
    )
    assert out == b"ok:https://mmg.whatsapp.net/new"
    assert calls == 2


@pytest.mark.asyncio
async def test_download_media_message_thumbnail_path_uses_thumbnail_media_type(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    async def _fake_download(media: dict[str, Any], media_type: str, _opts: dict[str, Any]) -> Any:
        captured["media"] = media
        captured["media_type"] = media_type

        async def _stream() -> Any:
            yield b"x"

        return _stream()

    monkeypatch.setattr(messages_mod, "download_content_from_message", _fake_download)
    out = await download_media_message(
        {"message": {"extendedTextMessage": {"thumbnailDirectPath": "/thumb/path", "mediaKey": b"k" * 32}}},
        "buffer",
        {},
    )
    assert out == b"x"
    assert captured["media_type"] == "thumbnail-link"
    assert captured["media"]["directPath"] == "/thumb/path"


@pytest.mark.asyncio
async def test_download_media_message_raises_for_non_media() -> None:
    with pytest.raises(ValueError, match="not a media message"):
        await download_media_message({"message": {"conversation": "hello"}}, "buffer", {})


@pytest.mark.asyncio
async def test_prepare_wa_message_media_exposes_public_wrapper(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_prepare(_message: dict[str, Any], _opts: dict[str, Any]) -> dict[str, Any]:
        return {"imageMessage": {"url": "https://mmg.whatsapp.net/x"}}

    monkeypatch.setattr(messages_mod, "_prepare_media_message", _fake_prepare)
    out = await prepare_wa_message_media({"image": b"raw"}, {"upload": object()})
    assert out["imageMessage"]["url"] == "https://mmg.whatsapp.net/x"


@pytest.mark.asyncio
async def test_prepare_wa_message_media_raises_for_invalid_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_prepare(_message: dict[str, Any], _opts: dict[str, Any]) -> Any:
        return None

    monkeypatch.setattr(messages_mod, "_prepare_media_message", _fake_prepare)
    with pytest.raises(ValueError, match="invalid media message payload"):
        await prepare_wa_message_media({"conversation": "x"}, {})


@pytest.mark.asyncio
async def test_generate_wa_message_content_fallback_uses_prepare_media_wrapper(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    async def _fake_prepare(payload: dict[str, Any], _opts: dict[str, Any]) -> dict[str, Any]:
        captured["payload"] = payload
        return {"documentMessage": {"url": "https://mmg.whatsapp.net/d"}}

    monkeypatch.setattr(messages_mod, "prepare_wa_message_media", _fake_prepare)
    out = await generate_wa_message_content({"unknownPayload": {"foo": "bar"}}, {"upload": object()})
    assert out["documentMessage"]["url"] == "https://mmg.whatsapp.net/d"
    assert captured["payload"] == {"unknownPayload": {"foo": "bar"}}


@pytest.mark.asyncio
async def test_generate_wa_message_content_adds_reporting_secret_when_needed() -> None:
    out = await generate_wa_message_content({"text": "hello world"}, {})
    assert "messageContextInfo" in out
    secret = out["messageContextInfo"]["messageSecret"]
    assert isinstance(secret, (bytes, bytearray))
    assert len(secret) == 32


@pytest.mark.asyncio
async def test_generate_wa_message_content_applies_context_info_after_edit_wrap() -> None:
    out = await generate_wa_message_content(
        {
            "text": "hello world",
            "edit": {"id": "orig-1", "remoteJid": "123@s.whatsapp.net"},
            "contextInfo": {"forwardingScore": 8},
        },
        {},
    )
    protocol = out["protocolMessage"]
    assert protocol["type"] == "MESSAGE_EDIT"
    assert protocol["contextInfo"]["forwardingScore"] == 8
    edited = protocol["editedMessage"]["extendedTextMessage"]
    assert "contextInfo" not in edited or "forwardingScore" not in edited.get("contextInfo", {})


def test_generate_wa_message_from_content_quoted_prefers_message_participant_and_strips_context_info() -> None:
    quoted = {
        "participant": "quoted-participant@s.whatsapp.net",
        "key": {
            "id": "quoted-1",
            "remoteJid": "group@g.us",
            "participant": "key-participant@s.whatsapp.net",
            "fromMe": False,
        },
        "message": {
            "extendedTextMessage": {
                "text": "quoted",
                "contextInfo": {"mentionedJid": ["x@s.whatsapp.net"]},
            }
        },
    }
    generated = generate_wa_message_from_content(
        "group@g.us",
        {"extendedTextMessage": {"text": "reply"}},
        {"quoted": quoted, "userJid": "me@s.whatsapp.net"},
    )
    ctx = generated.message["extendedTextMessage"]["contextInfo"]
    assert ctx["participant"] == "quoted-participant@s.whatsapp.net"
    assert ctx["stanzaId"] == "quoted-1"
    quoted_msg = ctx["quotedMessage"]["extendedTextMessage"]
    assert "contextInfo" not in quoted_msg


@pytest.mark.asyncio
async def test_generate_wa_message_passes_jid_into_content_generation(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    async def _fake_content(_content: dict[str, Any], opts: dict[str, Any]) -> dict[str, Any]:
        captured["jid"] = opts.get("jid")
        captured["logger"] = opts.get("logger")
        return {"conversation": "ok"}

    def _fake_from_content(jid: str, msg: dict[str, Any], opts: dict[str, Any]) -> dict[str, Any]:
        captured["from_jid"] = jid
        captured["from_opts"] = opts
        return {"jid": jid, "message": msg}

    class _Logger:
        def __init__(self) -> None:
            self.children: list[dict[str, Any]] = []

        def child(self, extra: dict[str, Any]) -> "_Logger":
            self.children.append(extra)
            return self

    logger = _Logger()
    monkeypatch.setattr(messages_mod, "generate_wa_message_content", _fake_content)
    monkeypatch.setattr(messages_mod, "generate_wa_message_from_content", _fake_from_content)

    out = await generate_wa_message(
        "120363@newsletter",
        {"text": "hello"},
        {"messageId": "msg-1", "logger": logger},
    )
    assert out == {"jid": "120363@newsletter", "message": {"conversation": "ok"}}
    assert captured["jid"] == "120363@newsletter"
    assert captured["from_jid"] == "120363@newsletter"
    assert logger.children == [{"msgId": "msg-1"}]
