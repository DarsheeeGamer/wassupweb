from __future__ import annotations

from wassupweb.utils.generics import fetch_latest_wa_web_version


def test_fetch_latest_wa_web_version_integration_shape() -> None:
    result = fetch_latest_wa_web_version(timeout=10.0)

    assert isinstance(result.get("version"), tuple)
    version = result["version"]
    assert len(version) == 3
    assert isinstance(version[0], int)
    assert isinstance(version[1], int)
    assert isinstance(version[2], int)
    assert isinstance(result.get("isLatest"), bool)


def test_fetch_latest_wa_web_version_fallback_shape_when_network_unavailable() -> None:
    result = fetch_latest_wa_web_version(timeout=0.001)

    assert "version" in result
    assert "isLatest" in result
    assert isinstance(result["version"], tuple)
    assert len(result["version"]) == 3
