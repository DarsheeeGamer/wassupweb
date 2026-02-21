from __future__ import annotations

import asyncio
import re
from typing import Any
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from pydantic import BaseModel, ConfigDict, Field

from ..types.message import WAUrlInfo
from .messages_media import extract_image_thumb, get_http_stream, to_buffer

THUMBNAIL_WIDTH_PX = 192
TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
META_RE = re.compile(
    r'<meta[^>]+(?:name|property)=["\'](?P<name>[^"\']+)["\'][^>]+content=["\'](?P<content>[^"\']*)["\']',
    re.IGNORECASE,
)


class URLFetchOptions(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="allow")
    timeout: int = 3000
    proxy_url: str | None = Field(default=None, alias="proxyUrl")
    headers: dict[str, str] | list[tuple[str, str]] | None = None


class URLGenerationOptions(BaseModel):
    model_config = ConfigDict(populate_by_name=True, arbitrary_types_allowed=True)
    thumbnail_width: int = Field(default=THUMBNAIL_WIDTH_PX, alias="thumbnailWidth")
    fetch_opts: URLFetchOptions = Field(default_factory=URLFetchOptions, alias="fetchOpts")
    upload_image: Any = Field(default=None, alias="uploadImage")
    logger: Any = None


def _headers_from_opts(headers: Any) -> dict[str, str]:
    if headers is None:
        return {}
    if isinstance(headers, dict):
        return {str(k): str(v) for k, v in headers.items()}
    if isinstance(headers, list):
        output: dict[str, str] = {}
        for item in headers:
            if isinstance(item, (tuple, list)) and len(item) == 2:
                output[str(item[0])] = str(item[1])
        return output
    return {}


def _extract_preview_fields(html: str) -> dict[str, str | None]:
    title_match = TITLE_RE.search(html)
    title = title_match.group(1).strip() if title_match else None

    fields: dict[str, str] = {}
    for match in META_RE.finditer(html):
        name = match.group("name").strip().lower()
        content = match.group("content").strip()
        fields[name] = content

    description = fields.get("description") or fields.get("og:description")
    image = fields.get("og:image") or fields.get("twitter:image")
    canonical = fields.get("og:url")
    return {"title": title, "description": description, "image": image, "canonical": canonical}


def _normalize_preview_link(text: str) -> str:
    parsed = urlparse(text)
    if parsed.scheme in {"http", "https"}:
        return text
    return f"https://{text}"


def _fetch_html(url: str, timeout_ms: int, headers: dict[str, str]) -> str:
    req = Request(url, headers=headers, method="GET")
    timeout = timeout_ms / 1000.0
    with urlopen(req, timeout=timeout) as resp:  # noqa: S310 - public URL lookup utility
        return resp.read().decode("utf-8", errors="replace")


async def _get_compressed_jpeg_thumbnail(url: str, opts: URLGenerationOptions) -> dict[str, Any]:
    stream = get_http_stream(url, opts.fetch_opts.model_dump(by_alias=True))
    result = await extract_image_thumb(stream, opts.thumbnail_width)
    return result


async def get_url_info(text: str, opts: URLGenerationOptions | dict[str, Any] | None = None) -> dict[str, Any] | None:
    if not text:
        return None
    options = opts if isinstance(opts, URLGenerationOptions) else URLGenerationOptions.model_validate(opts or {})
    preview_link = _normalize_preview_link(text)
    timeout = int(options.fetch_opts.timeout or 3000)
    headers = _headers_from_opts(options.fetch_opts.headers)
    if "User-Agent" not in {k.title(): v for k, v in headers.items()}:
        headers.setdefault("User-Agent", "Mozilla/5.0 (compatible; wassupweb/1.0)")

    html = await asyncio.to_thread(_fetch_html, preview_link, timeout, headers)
    fields = _extract_preview_fields(html)
    if not fields.get("title"):
        return None

    url_info = WAUrlInfo(
        **{
            "canonical-url": fields.get("canonical") or preview_link,
            "matched-text": text,
            "title": fields["title"],
            "description": fields.get("description"),
            "originalThumbnailUrl": fields.get("image"),
        }
    ).model_dump(by_alias=True, exclude_none=True)

    image = fields.get("image")
    if image:
        try:
            thumb = await _get_compressed_jpeg_thumbnail(image, options)
            url_info["jpegThumbnail"] = thumb["buffer"]
        except Exception as exc:  # pragma: no cover - remote image failures
            logger = options.logger
            if logger:
                logger.debug("error generating thumbnail", extra={"url": preview_link, "error": str(exc)})

    return url_info


# camelCase aliases
getUrlInfo = get_url_info


__all__ = ["URLFetchOptions", "URLGenerationOptions", "get_url_info"]
