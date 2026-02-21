from __future__ import annotations

import json
import os
import tempfile
import threading
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from wassupweb.utils.messages_media import (
    decode_media_retry_node,
    decrypt_media_retry_data,
    download_encrypted_content,
    encrypt_media_retry_request,
    extension_for_media_message,
    get_media_keys,
    get_status_code_for_media_retry,
    upload_with_node_http,
)


class _Server:
    def __init__(self, handler_cls: type[BaseHTTPRequestHandler]) -> None:
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        host, port = self.httpd.server_address
        return f"http://{host}:{port}"

    def __enter__(self) -> "_Server":
        self.thread.start()
        return self

    def __exit__(self, _exc_type, _exc, _tb) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=2)


def _temp_file(content: str) -> Path:
    fd, path_str = tempfile.mkstemp(prefix="wwp-upload-", suffix=".txt")
    os.close(fd)
    Path(path_str).write_text(content, encoding="utf-8")
    return Path(path_str)


@pytest.mark.asyncio
async def test_upload_with_node_http_success_and_body() -> None:
    expected_response = {"url": "https://example.com/media/123", "direct_path": "/media/123"}
    received = {"body": ""}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802 - stdlib method name
            length = int(self.headers.get("Content-Length", "0"))
            received["body"] = self.rfile.read(length).decode("utf-8")
            payload = json.dumps(expected_response).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, _format: str, *_args: object) -> None:
            return

    file_path = _temp_file("Hello, upload!")
    try:
        with _Server(Handler) as server:
            result = await upload_with_node_http(
                {
                    "url": f"{server.url}/upload",
                    "filePath": str(file_path),
                    "headers": {"Content-Type": "application/octet-stream"},
                }
            )
        assert result == expected_response
        assert received["body"] == "Hello, upload!"
    finally:
        file_path.unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_upload_with_node_http_follows_redirect() -> None:
    expected_response = {"ok": True}
    calls = {"count": 0}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802 - stdlib method name
            calls["count"] += 1
            if self.path == "/upload":
                self.send_response(302)
                self.send_header("Location", "/final")
                self.end_headers()
                return
            payload = json.dumps(expected_response).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, _format: str, *_args: object) -> None:
            return

    file_path = _temp_file("redirect me")
    try:
        with _Server(Handler) as server:
            result = await upload_with_node_http(
                {
                    "url": f"{server.url}/upload",
                    "filePath": str(file_path),
                    "headers": {"Content-Type": "application/octet-stream"},
                }
            )
        assert result == expected_response
        assert calls["count"] == 2
    finally:
        file_path.unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_upload_with_node_http_too_many_redirects() -> None:
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802 - stdlib method name
            self.send_response(302)
            self.send_header("Location", "/loop")
            self.end_headers()

        def log_message(self, _format: str, *_args: object) -> None:
            return

    file_path = _temp_file("redirect loop")
    try:
        with _Server(Handler) as server:
            with pytest.raises(RuntimeError, match="Too many redirects"):
                await upload_with_node_http(
                    {
                        "url": f"{server.url}/loop",
                        "filePath": str(file_path),
                        "headers": {"Content-Type": "application/octet-stream"},
                    }
                )
    finally:
        file_path.unlink(missing_ok=True)


def test_extension_for_media_message_matches_baileys_shape() -> None:
    assert extension_for_media_message({"videoMessage": {"mimetype": "video/mp4"}}) == "mp4"
    assert extension_for_media_message({"imageMessage": {"mimetype": "image/jpeg; charset=utf-8"}}) == "jpeg"
    assert extension_for_media_message({"locationMessage": {}}) == ".jpeg"


def test_get_status_code_for_media_retry_parity() -> None:
    assert get_status_code_for_media_retry(1) == 200
    assert get_status_code_for_media_retry(3) == 412
    assert get_status_code_for_media_retry(2) == 404
    assert get_status_code_for_media_retry(0) == 418
    assert get_status_code_for_media_retry(999) == 500


def test_media_retry_encrypt_decode_decrypt_roundtrip() -> None:
    msg_key = {
        "id": "ABC123",
        "remoteJid": "123456789@s.whatsapp.net",
        "fromMe": False,
    }
    media_key = b"\x11" * 32
    node = encrypt_media_retry_request(msg_key, media_key, "19998887777@s.whatsapp.net")
    event = decode_media_retry_node(node)

    assert event["key"]["id"] == "ABC123"
    assert event["key"]["remoteJid"] == "123456789@s.whatsapp.net"
    assert event["key"]["fromMe"] is False
    assert "media" in event

    parsed = decrypt_media_retry_data(event["media"], media_key, "ABC123")
    assert parsed.get("stanzaId") == "ABC123"


def _encrypt_media_payload_for_download(plaintext: bytes, cipher_key: bytes, iv: bytes, mac_key: bytes) -> bytes:
    from cryptography.hazmat.primitives import padding
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    padder = padding.PKCS7(algorithms.AES.block_size).padder()
    padded = padder.update(plaintext) + padder.finalize()
    encryptor = Cipher(algorithms.AES(cipher_key), modes.CBC(iv)).encryptor()
    ciphertext = encryptor.update(padded) + encryptor.finalize()
    mac = hmac.new(mac_key, iv, hashlib.sha256)
    mac.update(ciphertext)
    return ciphertext + mac.digest()[:10]


async def _collect_async_bytes(stream: object) -> bytes:
    chunks: list[bytes] = []
    async for chunk in stream:  # type: ignore[operator]
        chunks.append(bytes(chunk))
    return b"".join(chunks)


@pytest.mark.asyncio
async def test_download_encrypted_content_full_and_range() -> None:
    media_key = b"\x23" * 32
    key_info = await get_media_keys(media_key, "image")
    plaintext = b"".join(bytes([i % 251]) for i in range(512))
    payload = _encrypt_media_payload_for_download(
        plaintext,
        key_info.cipher_key,
        key_info.iv,
        key_info.mac_key or b"",
    )

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - stdlib method name
            data = payload
            range_header = self.headers.get("Range")
            if range_header and range_header.startswith("bytes="):
                values = range_header[len("bytes="):]
                start_text, end_text = values.split("-", 1)
                start = int(start_text) if start_text else 0
                end = int(end_text) if end_text else len(data) - 1
                start = max(start, 0)
                end = min(end, len(data) - 1)
                data = data[start : end + 1]
                self.send_response(206)
                self.send_header("Content-Range", f"bytes {start}-{end}/{len(payload)}")
            else:
                self.send_response(200)

            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, _format: str, *_args: object) -> None:
            return

    with _Server(Handler) as server:
        full_stream = download_encrypted_content(f"{server.url}/media", key_info)
        full = await _collect_async_bytes(full_stream)
        assert full == plaintext

        ranged_stream = download_encrypted_content(
            f"{server.url}/media",
            key_info,
            {"startByte": 37, "endByte": 211},
        )
        ranged = await _collect_async_bytes(ranged_stream)
        assert ranged == plaintext[37:211]
