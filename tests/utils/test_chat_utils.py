from __future__ import annotations

import json

import pytest

import wassupweb.utils.chat_utils as chat_utils
from wassupweb.wabinary import BinaryNode


def _node(tag: str, attrs: dict[str, str] | None = None, content: object = None) -> BinaryNode:
    return BinaryNode(tag=tag, attrs=attrs or {}, content=content)


@pytest.mark.asyncio
async def test_extract_syncd_patches_applies_patch_version_fallback() -> None:
    patch = {"keyId": {"id": "abc"}, "mutations": []}
    collection = _node(
        "collection",
        {"name": "regular_low", "version": "4", "has_more_patches": "false"},
        [_node("patch", content=json.dumps(patch).encode("utf-8"))],
    )
    root = _node("iq", content=[_node("sync", content=[collection])])

    out = await chat_utils.extract_syncd_patches(root, {})
    assert out["regular_low"]["hasMorePatches"] is False
    assert out["regular_low"]["patches"][0]["version"] == {"version": 5}


@pytest.mark.asyncio
async def test_extract_syncd_patches_downloads_external_snapshot_reference(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot_ref = {"directPath": "/mms/snapshot", "mediaKey": "abc"}
    expected_snapshot = {"version": {"version": 9}, "records": []}

    async def _fake_download(blob: dict[str, object], options: dict[str, object]) -> bytes:
        _ = options
        assert blob["directPath"] == "/mms/snapshot"
        return json.dumps(expected_snapshot).encode("utf-8")

    monkeypatch.setattr(chat_utils, "download_external_blob", _fake_download)

    collection = _node(
        "collection",
        {"name": "regular", "version": "8", "has_more_patches": "true"},
        [
            _node("snapshot", content=json.dumps(snapshot_ref).encode("utf-8")),
            _node("patch", content=json.dumps({"version": {"version": 9}, "mutations": []}).encode("utf-8")),
        ],
    )
    root = _node("iq", content=[_node("sync", content=[collection])])

    out = await chat_utils.extract_syncd_patches(root, {})
    assert out["regular"]["hasMorePatches"] is True
    assert out["regular"]["snapshot"] == expected_snapshot
    assert out["regular"]["patches"][0]["version"] == {"version": 9}


@pytest.mark.asyncio
async def test_extract_syncd_patches_decodes_numeric_byte_map_snapshot() -> None:
    snapshot_payload = {"version": {"version": 2}, "records": []}
    encoded = json.dumps(snapshot_payload).encode("utf-8")
    byte_map = {str(i): encoded[i] for i in range(len(encoded))}

    collection = _node(
        "collection",
        {"name": "regular", "version": "1", "has_more_patches": "false"},
        [_node("snapshot", content=byte_map)],
    )
    root = _node("iq", content=[_node("sync", content=[collection])])

    out = await chat_utils.extract_syncd_patches(root, {})
    assert out["regular"]["snapshot"] == snapshot_payload
