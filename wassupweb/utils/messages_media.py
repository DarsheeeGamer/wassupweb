from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import http.client
import hmac
import io
import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Iterable
from urllib.parse import quote, urljoin, urlparse
from urllib.request import Request, urlopen

from ..defaults import DEFAULT_ORIGIN, MEDIA_HKDF_KEY_MAPPING, MEDIA_PATH_MAP
from ..types.message import MediaDecryptionKeyInfo
from ..wabinary import (
    BinaryNode,
    get_binary_node_child,
    get_binary_node_child_buffer,
    jid_normalized_user,
)
from ..waproto import proto
from .crypto import aes_decrypt_gcm, aes_encrypt_gcm, hkdf
from .generics import generate_message_id_v2

CHUNK_SIZE = 64 * 1024
DEF_HOST = "mmg.whatsapp.net"


def _as_headers(headers: Any) -> dict[str, str]:
    if not headers:
        return {}
    if isinstance(headers, dict):
        return {str(k): str(v) for k, v in headers.items()}
    if isinstance(headers, list):
        out: dict[str, str] = {}
        for item in headers:
            if isinstance(item, (tuple, list)) and len(item) == 2:
                out[str(item[0])] = str(item[1])
        return out
    return {}


async def _iter_bytes(data: bytes) -> AsyncIterator[bytes]:
    yield data


async def _iter_file(path: str | Path) -> AsyncIterator[bytes]:
    file_path = Path(path)
    handle = file_path.open("rb")
    try:
        while True:
            chunk = await asyncio.to_thread(handle.read, CHUNK_SIZE)
            if not chunk:
                break
            yield chunk
    finally:
        handle.close()


async def _iter_sync_readable(stream: Any) -> AsyncIterator[bytes]:
    while True:
        chunk = await asyncio.to_thread(stream.read, CHUNK_SIZE)
        if not chunk:
            break
        if isinstance(chunk, str):
            chunk = chunk.encode("utf-8")
        yield bytes(chunk)


async def _iter_async_readable(stream: Any) -> AsyncIterator[bytes]:
    while True:
        chunk = await stream.read(CHUNK_SIZE)
        if not chunk:
            break
        if isinstance(chunk, str):
            chunk = chunk.encode("utf-8")
        yield bytes(chunk)


async def _iter_sync_iterable(stream: Iterable[bytes]) -> AsyncIterator[bytes]:
    for chunk in stream:
        if isinstance(chunk, str):
            chunk = chunk.encode("utf-8")
        yield bytes(chunk)


def _make_request(url: str, headers: dict[str, str], timeout_ms: int | None = None) -> Request:
    timeout = timeout_ms / 1000.0 if timeout_ms and timeout_ms > 0 else None
    req = Request(url=url, headers=headers, method="GET")
    req.timeout = timeout  # type: ignore[attr-defined]
    return req


def _fetch_bytes(url: str, headers: dict[str, str], timeout_ms: int | None = None) -> bytes:
    timeout = timeout_ms / 1000.0 if timeout_ms and timeout_ms > 0 else None
    req = Request(url=url, headers=headers, method="GET")
    with urlopen(req, timeout=timeout) as resp:  # noqa: S310 - user-controlled URL by design
        return resp.read()


def to_readable(buffer: bytes) -> io.BytesIO:
    return io.BytesIO(buffer)


async def to_buffer(stream: Any) -> bytes:
    if stream is None:
        return b""
    if isinstance(stream, (bytes, bytearray)):
        return bytes(stream)

    chunks: list[bytes] = []
    try:
        if hasattr(stream, "__aiter__"):
            async for chunk in stream:
                if isinstance(chunk, str):
                    chunk = chunk.encode("utf-8")
                chunks.append(bytes(chunk))
        elif hasattr(stream, "read"):
            if asyncio.iscoroutinefunction(getattr(stream, "read")):
                async for chunk in _iter_async_readable(stream):
                    chunks.append(chunk)
            else:
                async for chunk in _iter_sync_readable(stream):
                    chunks.append(chunk)
        else:
            async for chunk in _iter_sync_iterable(stream):
                chunks.append(chunk)
    finally:
        destroy = getattr(stream, "destroy", None)
        if callable(destroy):
            destroy()
        close = getattr(stream, "close", None)
        if callable(close):
            close()

    return b"".join(chunks)


async def get_http_stream(url: str, options: dict[str, Any] | None = None) -> AsyncIterator[bytes]:
    options = options or {}
    headers = _as_headers(options.get("headers"))
    timeout_ms = options.get("timeout") or options.get("timeoutMs")
    data = await asyncio.to_thread(_fetch_bytes, str(url), headers, timeout_ms)
    async for chunk in _iter_bytes(data):
        yield chunk


async def get_stream(item: Any, opts: dict[str, Any] | None = None) -> dict[str, Any]:
    if isinstance(item, (bytes, bytearray)):
        return {"stream": _iter_bytes(bytes(item)), "type": "buffer"}

    if isinstance(item, Path):
        return {"stream": _iter_file(item), "type": "file"}

    if isinstance(item, str):
        parsed = urlparse(item)
        if parsed.scheme in {"http", "https"}:
            return {"stream": get_http_stream(item, opts), "type": "remote"}
        if item.startswith("data:"):
            payload = item.split(",", 1)[1] if "," in item else ""
            return {"stream": _iter_bytes(base64.b64decode(payload)), "type": "buffer"}
        return {"stream": _iter_file(item), "type": "file"}

    if isinstance(item, dict):
        if "stream" in item:
            stream = item["stream"]
            if hasattr(stream, "__aiter__"):
                return {"stream": stream, "type": "readable"}
            if hasattr(stream, "read"):
                if asyncio.iscoroutinefunction(getattr(stream, "read")):
                    return {"stream": _iter_async_readable(stream), "type": "readable"}
                return {"stream": _iter_sync_readable(stream), "type": "readable"}
            return {"stream": _iter_sync_iterable(stream), "type": "readable"}

        if "url" in item:
            return await get_stream(str(item["url"]), opts)

    raise TypeError(f"unsupported media upload type: {type(item)!r}")


def hkdf_info_key(media_type: str) -> str:
    hkdf_info = MEDIA_HKDF_KEY_MAPPING.get(media_type, "")
    return f"WhatsApp {hkdf_info} Keys"


async def get_media_keys(buffer: bytes | str | None, media_type: str) -> MediaDecryptionKeyInfo:
    if not buffer:
        raise ValueError("cannot derive from empty media key")

    if isinstance(buffer, str):
        text = buffer.replace("data:;base64,", "")
        buffer = base64.b64decode(text)

    expanded = hkdf(buffer, 112, info=hkdf_info_key(media_type))
    return MediaDecryptionKeyInfo(
        iv=expanded[:16],
        cipherKey=expanded[16:48],
        macKey=expanded[48:80],
    )


async def get_raw_media_upload_data(media: Any, media_type: str, logger: Any = None) -> dict[str, Any]:
    stream_info = await get_stream(media)
    stream = stream_info["stream"]
    hasher = hashlib.sha256()
    file_path = os.path.join(tempfile.gettempdir(), f"{media_type}{generate_message_id_v2()}")

    file_length = 0
    with open(file_path, "wb") as out:
        async for data in stream:
            file_length += len(data)
            hasher.update(data)
            out.write(data)

    if logger:
        logger.debug("hashed data for raw upload")
    return {"filePath": file_path, "fileSha256": hasher.digest(), "fileLength": file_length}


def _load_pillow_image_library() -> Any:
    try:
        from PIL import Image  # type: ignore[import-untyped]

        return Image
    except Exception as exc:  # pragma: no cover - optional dependency
        raise RuntimeError("Pillow is required for image processing") from exc


async def extract_image_thumb(buffer_or_file_path: Any, width: int = 32) -> dict[str, Any]:
    if not isinstance(buffer_or_file_path, (bytes, bytearray, str, Path)):
        buffer_or_file_path = await to_buffer(buffer_or_file_path)

    Image = _load_pillow_image_library()
    if isinstance(buffer_or_file_path, (str, Path)):
        img = Image.open(str(buffer_or_file_path))
    else:
        img = Image.open(io.BytesIO(bytes(buffer_or_file_path)))

    original = {"width": img.width, "height": img.height}
    ratio = width / float(img.width or 1)
    height = max(1, int((img.height or 1) * ratio))
    resized = img.convert("RGB").resize((width, height))
    out = io.BytesIO()
    resized.save(out, format="JPEG", quality=50)
    return {"buffer": out.getvalue(), "original": original}


def encode_base64_encoded_string_for_upload(b64: str) -> str:
    transformed = b64.replace("+", "-").replace("/", "_").rstrip("=")
    return quote(transformed, safe="")


async def generate_profile_picture(media_upload: Any, dimensions: dict[str, int] | None = None) -> dict[str, bytes]:
    dimensions = dimensions or {}
    width = int(dimensions.get("width", 640))
    height = int(dimensions.get("height", 640))

    if isinstance(media_upload, (bytes, bytearray)):
        buffer = bytes(media_upload)
    else:
        stream_info = await get_stream(media_upload)
        buffer = await to_buffer(stream_info["stream"])

    Image = _load_pillow_image_library()
    img = Image.open(io.BytesIO(buffer)).convert("RGB")
    min_side = min(img.width, img.height)
    cropped = img.crop((0, 0, min_side, min_side))
    resized = cropped.resize((width, height))

    out = io.BytesIO()
    resized.save(out, format="JPEG", quality=50)
    return {"img": out.getvalue()}


def media_message_sha256_b64(message: dict[str, Any]) -> str | None:
    media = next(iter(message.values()), None) if isinstance(message, dict) else None
    if not isinstance(media, dict):
        return None
    file_sha = media.get("fileSha256")
    if not isinstance(file_sha, (bytes, bytearray)):
        return None
    return base64.b64encode(bytes(file_sha)).decode("ascii")


async def get_audio_duration(_buffer: Any) -> float | None:
    # Optional dependency path. Keep this graceful for environments without media tooling.
    try:
        from mutagen import File as MutagenFile  # type: ignore[import-untyped]
    except Exception:  # pragma: no cover - optional dependency
        return None

    path: str | None = None
    if isinstance(_buffer, (str, Path)):
        path = str(_buffer)
    elif isinstance(_buffer, (bytes, bytearray)):
        fd, temp_path = tempfile.mkstemp(prefix="wassup-audio-", suffix=".bin")
        os.close(fd)
        with open(temp_path, "wb") as handle:
            handle.write(bytes(_buffer))
        path = temp_path

    if not path:
        return None
    try:
        audio = MutagenFile(path)
        return float(getattr(getattr(audio, "info", None), "length", 0.0) or 0.0)
    finally:
        if isinstance(_buffer, (bytes, bytearray)):
            with contextlib.suppress(Exception):
                os.unlink(path)


async def get_audio_waveform(_buffer: Any, logger: Any = None) -> bytes | None:
    # Placeholder compatible output (64 bars) when DSP dependencies are not present.
    try:
        raw = bytes(_buffer) if isinstance(_buffer, (bytes, bytearray)) else b""
        if not raw:
            return None
        digest = hashlib.sha256(raw).digest()
        waveform = bytes((digest[i % len(digest)] % 101) for i in range(64))
        return waveform
    except Exception as exc:  # pragma: no cover - optional dependency path
        if logger:
            logger.debug("Failed to generate waveform", extra={"error": str(exc)})
        return None


async def generate_thumbnail(file: str, media_type: str, options: dict[str, Any] | None = None) -> dict[str, Any]:
    options = options or {}
    thumbnail: str | None = None
    original_dims: dict[str, int] | None = None

    if media_type == "image":
        result = await extract_image_thumb(file, 32)
        thumbnail = base64.b64encode(result["buffer"]).decode("ascii")
        original = result.get("original") or {}
        if original.get("width") and original.get("height"):
            original_dims = {"width": int(original["width"]), "height": int(original["height"])}
    elif media_type == "video":
        img_filename = os.path.join(tempfile.gettempdir(), f"{generate_message_id_v2()}.jpg")
        try:
            cmd = [
                "ffmpeg",
                "-ss",
                "00:00:00",
                "-i",
                file,
                "-y",
                "-vf",
                "scale=32:-1",
                "-vframes",
                "1",
                "-f",
                "image2",
                img_filename,
            ]
            proc = await asyncio.to_thread(subprocess.run, cmd, capture_output=True, text=True)
            if proc.returncode == 0 and os.path.exists(img_filename):
                with open(img_filename, "rb") as handle:
                    thumbnail = base64.b64encode(handle.read()).decode("ascii")
        except Exception as exc:  # pragma: no cover - ffmpeg path
            logger = options.get("logger")
            if logger:
                logger.debug("could not generate video thumb", extra={"error": str(exc)})
        finally:
            if os.path.exists(img_filename):
                with contextlib.suppress(Exception):
                    os.unlink(img_filename)

    return {"thumbnail": thumbnail, "originalImageDimensions": original_dims}


async def encrypted_stream(
    media: Any,
    media_type: str,
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    options = options or {}
    save_original = bool(options.get("saveOriginalFileIfRequired"))
    logger = options.get("logger")

    stream_info = await get_stream(media, options.get("opts"))
    stream = stream_info["stream"]
    source_type = stream_info["type"]

    media_key = os.urandom(32)
    keys = await get_media_keys(media_key, media_type)
    cipher_key = keys.cipher_key
    iv = keys.iv
    mac_key = keys.mac_key or b""

    enc_file_path = os.path.join(tempfile.gettempdir(), f"{media_type}{generate_message_id_v2()}-enc")
    original_file_path: str | None = None
    if save_original:
        original_file_path = os.path.join(tempfile.gettempdir(), f"{media_type}{generate_message_id_v2()}-orig")

    file_length = 0
    sha_plain = hashlib.sha256()
    sha_enc = hashlib.sha256()
    mac = hmac.new(mac_key, iv, hashlib.sha256)

    from cryptography.hazmat.primitives import padding
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    cipher = Cipher(algorithms.AES(cipher_key), modes.CBC(iv))
    encryptor = cipher.encryptor()
    padder = padding.PKCS7(algorithms.AES.block_size).padder()

    try:
        with open(enc_file_path, "wb") as enc_out:
            original_out = open(original_file_path, "wb") if original_file_path else None
            try:
                async for data in stream:
                    file_length += len(data)
                    max_len = (((options.get("opts") or {}).get("maxContentLength")) if isinstance(options.get("opts"), dict) else None)
                    if source_type == "remote" and max_len and file_length > int(max_len):
                        raise ValueError(f'content length exceeded when encrypting "{source_type}"')

                    sha_plain.update(data)
                    if original_out:
                        original_out.write(data)

                    padded = padder.update(data)
                    if padded:
                        encrypted = encryptor.update(padded)
                        if encrypted:
                            sha_enc.update(encrypted)
                            mac.update(encrypted)
                            enc_out.write(encrypted)

                final_padded = padder.finalize()
                final_enc = encryptor.update(final_padded) + encryptor.finalize()
                if final_enc:
                    sha_enc.update(final_enc)
                    mac.update(final_enc)
                    enc_out.write(final_enc)
            finally:
                if original_out:
                    original_out.close()

            mac10 = mac.digest()[:10]
            sha_enc.update(mac10)
            enc_out.write(mac10)

        if logger:
            logger.debug("encrypted data successfully")

        return {
            "mediaKey": media_key,
            "originalFilePath": original_file_path,
            "encFilePath": enc_file_path,
            "mac": mac10,
            "fileEncSha256": sha_enc.digest(),
            "fileSha256": sha_plain.digest(),
            "fileLength": file_length,
        }
    except Exception:
        with contextlib.suppress(Exception):
            os.unlink(enc_file_path)
        if original_file_path:
            with contextlib.suppress(Exception):
                os.unlink(original_file_path)
        raise


def get_url_from_direct_path(direct_path: str) -> str:
    return f"https://{DEF_HOST}{direct_path}"


async def _download_bytes(download_url: str, headers: dict[str, str], timeout_ms: int | None = None) -> bytes:
    return await asyncio.to_thread(_fetch_bytes, download_url, headers, timeout_ms)


async def download_encrypted_content(
    download_url: str,
    keys: MediaDecryptionKeyInfo,
    opts: dict[str, Any] | None = None,
) -> AsyncIterator[bytes]:
    opts = opts or {}
    options = opts.get("options") or {}
    start_byte = opts.get("startByte")
    end_byte = opts.get("endByte")

    aes_chunk_size = 16

    def _to_smallest_chunk_size(num: int) -> int:
        return (num // aes_chunk_size) * aes_chunk_size

    bytes_fetched = 0
    start_chunk = 0
    first_block_is_iv = False
    if isinstance(start_byte, int):
        chunk = _to_smallest_chunk_size(int(start_byte or 0))
        if chunk:
            start_chunk = chunk - aes_chunk_size
            bytes_fetched = chunk
            first_block_is_iv = True

    end_chunk = _to_smallest_chunk_size(int(end_byte or 0)) + aes_chunk_size if isinstance(end_byte, int) else None

    headers = {"Origin": DEFAULT_ORIGIN}
    headers.update(_as_headers(options.get("headers")))
    if start_chunk or end_chunk is not None:
        range_header = f"bytes={start_chunk}-"
        if end_chunk is not None:
            range_header = f"{range_header}{end_chunk}"
        headers["Range"] = range_header
    timeout_ms = options.get("timeout") or options.get("timeoutMs")

    encrypted = await _download_bytes(download_url, headers, timeout_ms)

    cipher_key = keys.cipher_key if isinstance(keys, MediaDecryptionKeyInfo) else keys["cipherKey"]
    iv = keys.iv if isinstance(keys, MediaDecryptionKeyInfo) else keys["iv"]

    from cryptography.hazmat.primitives import padding
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    remaining_bytes = b""
    plaintext_acc = bytearray()
    decryptor: Any | None = None
    unpadder: Any | None = None

    def _push_bytes(data: bytes) -> None:
        nonlocal bytes_fetched
        if isinstance(start_byte, int) or isinstance(end_byte, int):
            start = None
            end = None
            if isinstance(start_byte, int) and bytes_fetched < start_byte:
                start = max(start_byte - bytes_fetched, 0)
            if isinstance(end_byte, int) and (bytes_fetched + len(data)) >= end_byte:
                end = max(end_byte - bytes_fetched, 0)
            plaintext_acc.extend(data[slice(start, end)])
            bytes_fetched += len(data)
            return
        plaintext_acc.extend(data)

    data = bytes(encrypted)
    decrypt_length = _to_smallest_chunk_size(len(data))
    remaining_bytes = data[decrypt_length:]
    data = data[:decrypt_length]

    if data:
        iv_value = iv
        if first_block_is_iv:
            iv_value = data[:aes_chunk_size]
            data = data[aes_chunk_size:]

        decryptor = Cipher(algorithms.AES(cipher_key), modes.CBC(iv_value)).decryptor()
        unpadder = padding.PKCS7(algorithms.AES.block_size).unpadder()
        if data:
            decrypted_chunk = decryptor.update(data)
            if isinstance(end_byte, int):
                _push_bytes(decrypted_chunk)
            else:
                _push_bytes(unpadder.update(decrypted_chunk))

    if decryptor is not None:
        final_bytes = decryptor.finalize()
        if isinstance(end_byte, int):
            _push_bytes(final_bytes)
        else:
            if final_bytes:
                _push_bytes(unpadder.update(final_bytes))
            _push_bytes(unpadder.finalize())

    _ = remaining_bytes  # trailing MAC/partial bytes, intentionally ignored for parity with stream transform

    async for chunk in _iter_bytes(bytes(plaintext_acc)):
        yield chunk


async def download_content_from_message(message: dict[str, Any], media_type: str, opts: dict[str, Any] | None = None) -> AsyncIterator[bytes]:
    opts = opts or {}
    media_key = message.get("mediaKey")
    direct_path = message.get("directPath")
    url = message.get("url")

    is_valid_media_url = isinstance(url, str) and url.startswith("https://mmg.whatsapp.net/")
    download_url = url if is_valid_media_url else (get_url_from_direct_path(direct_path) if direct_path else None)
    if not download_url:
        raise ValueError("no valid media URL or directPath present in message")

    keys = await get_media_keys(media_key, media_type)
    return download_encrypted_content(download_url, keys, opts)


def extension_for_media_message(message: dict[str, Any]) -> str:
    def _get_extension(mimetype: str) -> str:
        return (mimetype.split(";")[0].split("/")[-1]).strip()

    msg_type = next(iter(message.keys()), "")
    if msg_type in {"locationMessage", "liveLocationMessage", "productMessage"}:
        return ".jpeg"

    content = message.get(msg_type) or {}
    mimetype = content.get("mimetype") or "application/octet-stream"
    return _get_extension(mimetype)


def _upload_media_sync(
    *,
    url: str,
    file_path: str,
    headers: dict[str, str],
    timeout_ms: int | None = None,
    redirect_count: int = 0,
    max_redirects: int = 5,
) -> dict[str, Any] | None:
    timeout = timeout_ms / 1000.0 if timeout_ms and timeout_ms > 0 else None
    with open(file_path, "rb") as handle:
        payload = handle.read()

    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        raise ValueError(f"invalid upload URL: {url!r}")
    scheme = parsed.scheme.lower()
    if scheme not in {"http", "https"}:
        raise ValueError(f"unsupported upload URL scheme: {scheme}")

    host = parsed.hostname or ""
    if not host:
        raise ValueError(f"invalid upload URL host: {url!r}")
    port = parsed.port or (443 if scheme == "https" else 80)
    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"

    request_headers = dict(headers)
    request_headers["Content-Length"] = str(len(payload))

    conn_cls = http.client.HTTPSConnection if scheme == "https" else http.client.HTTPConnection
    conn = conn_cls(host, port, timeout=timeout)
    try:
        conn.request("POST", path, body=payload, headers=request_headers)
        response = conn.getresponse()
        status = int(response.status)
        body = response.read().decode("utf-8", errors="replace")
        location = response.getheader("Location")
    finally:
        conn.close()

    if status in {301, 302, 303, 307, 308} and location:
        if redirect_count >= max_redirects:
            raise RuntimeError("Too many redirects")
        redirected = urljoin(url, location)
        return _upload_media_sync(
            url=redirected,
            file_path=file_path,
            headers=headers,
            timeout_ms=timeout_ms,
            redirect_count=redirect_count + 1,
            max_redirects=max_redirects,
        )

    if status < 200 or status >= 300:
        raise RuntimeError(f"upload failed with status {status}")

    try:
        parsed = json.loads(body)
        return parsed if isinstance(parsed, dict) else None
    except Exception:
        return None


async def upload_with_node_http(params: dict[str, Any], redirect_count: int = 0) -> dict[str, Any] | None:
    if redirect_count > 5:
        raise RuntimeError("Too many redirects")
    return await asyncio.to_thread(
        _upload_media_sync,
        url=str(params["url"]),
        file_path=str(params["filePath"]),
        headers=_as_headers(params.get("headers")),
        timeout_ms=params.get("timeoutMs"),
        redirect_count=redirect_count,
    )


async def _upload_media(params: dict[str, Any], logger: Any = None) -> dict[str, Any] | None:
    if logger:
        logger.debug("Using urllib upload path")
    return await upload_with_node_http(params)


def get_wa_upload_to_server(
    config: Any,
    refresh_media_conn: Callable[[bool], Any],
) -> Callable[[str, dict[str, Any]], Any]:
    async def _upload(file_path: str, upload_opts: dict[str, Any]) -> dict[str, Any]:
        media_type = upload_opts["mediaType"]
        file_enc_sha_b64 = upload_opts["fileEncSha256B64"]
        timeout_ms = upload_opts.get("timeoutMs")

        upload_info = await refresh_media_conn(False)
        custom_hosts = getattr(config, "custom_upload_hosts", None) or getattr(config, "customUploadHosts", []) or []
        hosts = [*custom_hosts, *(getattr(upload_info, "hosts", None) or upload_info.get("hosts", []))]

        encoded = encode_base64_encoded_string_for_upload(file_enc_sha_b64)
        options = getattr(config, "options", None) or {}
        custom_headers = _as_headers(options.get("headers"))
        headers = {**custom_headers, "Content-Type": "application/octet-stream", "Origin": DEFAULT_ORIGIN}
        auth = getattr(upload_info, "auth", None) or upload_info.get("auth")
        urls: dict[str, Any] | None = None

        for host_info in hosts:
            hostname = host_info.get("hostname") if isinstance(host_info, dict) else getattr(host_info, "hostname", None)
            if not hostname:
                continue
            path = MEDIA_PATH_MAP.get(media_type, "")
            media_url = f"https://{hostname}{path}/{encoded}?auth={quote(str(auth), safe='')}&token={encoded}"

            result: dict[str, Any] | None = None
            try:
                result = await _upload_media(
                    {
                        "url": media_url,
                        "filePath": file_path,
                        "headers": headers,
                        "timeoutMs": timeout_ms,
                    },
                    getattr(config, "logger", None),
                )
                if result and (result.get("url") or result.get("direct_path")):
                    urls = {
                        "mediaUrl": result.get("url"),
                        "directPath": result.get("direct_path"),
                        "meta_hmac": result.get("meta_hmac"),
                        "fbid": result.get("fbid"),
                        "ts": result.get("ts"),
                    }
                    break
                upload_info = await refresh_media_conn(True)
                raise RuntimeError(f"upload failed, reason: {result}")
            except Exception as exc:
                logger = getattr(config, "logger", None)
                if logger:
                    logger.warning(
                        "Error in uploading media",
                        extra={"hostname": hostname, "error": str(exc), "uploadResult": result},
                    )

        if not urls:
            raise RuntimeError("Media upload failed on all hosts")
        return urls

    return _upload


def _get_media_retry_key(media_key: bytes | bytearray) -> bytes:
    return hkdf(bytes(media_key), 32, info="WhatsApp Media Retry Notification")


def _proto_cls(name: str) -> Any:
    getter = getattr(proto, "get", None)
    if callable(getter):
        return getter(name)
    return getattr(proto, name, None)


def encrypt_media_retry_request(key: dict[str, Any], media_key: bytes | bytearray, me_id: str) -> BinaryNode:
    payload: bytes
    server_error_receipt_cls = _proto_cls("ServerErrorReceipt")
    if server_error_receipt_cls is not None:
        receipt = server_error_receipt_cls()
        receipt.stanzaId = str(key.get("id", ""))
        payload = receipt.SerializeToString()
    else:
        payload = json.dumps({"stanzaId": key.get("id")}).encode("utf-8")

    iv = os.urandom(12)
    retry_key = _get_media_retry_key(media_key)
    ciphertext = aes_encrypt_gcm(payload, retry_key, iv, str(key.get("id", "")).encode("utf-8"))

    return BinaryNode(
        tag="receipt",
        attrs={"id": key.get("id", ""), "to": jid_normalized_user(me_id), "type": "server-error"},
        content=[
            BinaryNode(
                tag="encrypt",
                attrs={},
                content=[
                    BinaryNode(tag="enc_p", attrs={}, content=ciphertext),
                    BinaryNode(tag="enc_iv", attrs={}, content=iv),
                ],
            ),
            BinaryNode(
                tag="rmr",
                attrs={
                    "jid": key.get("remoteJid", ""),
                    "from_me": str(bool(key.get("fromMe"))).lower(),
                    "participant": key.get("participant"),
                },
            ),
        ],
    )


def decode_media_retry_node(node: BinaryNode) -> dict[str, Any]:
    rmr_node = get_binary_node_child(node, "rmr")
    if not rmr_node:
        raise ValueError("missing rmr node")

    event = {
        "key": {
            "id": node.attrs.get("id"),
            "remoteJid": rmr_node.attrs.get("jid"),
            "fromMe": rmr_node.attrs.get("from_me") == "true",
            "participant": rmr_node.attrs.get("participant"),
        }
    }

    error_node = get_binary_node_child(node, "error")
    if error_node:
        code = int(error_node.attrs.get("code") or 0)
        event["error"] = RuntimeError(f"Failed to re-upload media ({code})")
        event["statusCode"] = get_status_code_for_media_retry(code)
        return event

    enc_node = get_binary_node_child(node, "encrypt")
    ciphertext = get_binary_node_child_buffer(enc_node, "enc_p")
    iv = get_binary_node_child_buffer(enc_node, "enc_iv")
    if ciphertext and iv:
        event["media"] = {"ciphertext": ciphertext, "iv": iv}
    else:
        event["error"] = RuntimeError("Failed to re-upload media (missing ciphertext)")
        event["statusCode"] = 404
    return event


def decrypt_media_retry_data(
    encrypted: dict[str, bytes],
    media_key: bytes | bytearray,
    msg_id: str,
) -> dict[str, Any]:
    retry_key = _get_media_retry_key(media_key)
    plaintext = aes_decrypt_gcm(
        encrypted["ciphertext"],
        retry_key,
        encrypted["iv"],
        msg_id.encode("utf-8"),
    )
    media_retry_cls = _proto_cls("MediaRetryNotification")
    if media_retry_cls is not None:
        parsed: dict[str, Any] | None = None
        try:
            from google.protobuf.json_format import MessageToDict

            decoded = media_retry_cls()
            decoded.ParseFromString(plaintext)
            parsed_candidate = MessageToDict(decoded, preserving_proto_field_name=False)
            parsed = parsed_candidate if isinstance(parsed_candidate, dict) else None
        except Exception:
            parsed = None
        if parsed is not None:
            return parsed

    try:
        decoded_json = json.loads(plaintext.decode("utf-8"))
        return decoded_json if isinstance(decoded_json, dict) else {"raw": plaintext}
    except Exception:
        return {"raw": plaintext}


MEDIA_RETRY_STATUS_MAP = {1: 200, 3: 412, 2: 404, 0: 418}


def get_status_code_for_media_retry(code: int) -> int:
    return MEDIA_RETRY_STATUS_MAP.get(int(code), 500)


# camelCase aliases for parity
hkdfInfoKey = hkdf_info_key
getRawMediaUploadData = get_raw_media_upload_data
getMediaKeys = get_media_keys
extractImageThumb = extract_image_thumb
encodeBase64EncodedStringForUpload = encode_base64_encoded_string_for_upload
generateProfilePicture = generate_profile_picture
mediaMessageSHA256B64 = media_message_sha256_b64
getAudioDuration = get_audio_duration
getAudioWaveform = get_audio_waveform
toReadable = to_readable
toBuffer = to_buffer
getStream = get_stream
generateThumbnail = generate_thumbnail
getHttpStream = get_http_stream
encryptedStream = encrypted_stream
getUrlFromDirectPath = get_url_from_direct_path
downloadContentFromMessage = download_content_from_message
downloadEncryptedContent = download_encrypted_content
extensionForMediaMessage = extension_for_media_message
uploadWithNodeHttp = upload_with_node_http
getWAUploadToServer = get_wa_upload_to_server
encryptMediaRetryRequest = encrypt_media_retry_request
decodeMediaRetryNode = decode_media_retry_node
decryptMediaRetryData = decrypt_media_retry_data
getStatusCodeForMediaRetry = get_status_code_for_media_retry


__all__ = [
    "hkdf_info_key",
    "get_raw_media_upload_data",
    "get_media_keys",
    "extract_image_thumb",
    "encode_base64_encoded_string_for_upload",
    "generate_profile_picture",
    "media_message_sha256_b64",
    "get_audio_duration",
    "get_audio_waveform",
    "to_readable",
    "to_buffer",
    "get_stream",
    "generate_thumbnail",
    "get_http_stream",
    "encrypted_stream",
    "get_url_from_direct_path",
    "download_content_from_message",
    "download_encrypted_content",
    "extension_for_media_message",
    "upload_with_node_http",
    "get_wa_upload_to_server",
    "encrypt_media_retry_request",
    "decode_media_retry_node",
    "decrypt_media_retry_data",
    "get_status_code_for_media_retry",
]
