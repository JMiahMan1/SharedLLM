"""Smoke test for the conftest_media fixtures (fake identity + aioresponses)."""
import pytest

from conftest_media import mock_upstream


@pytest.mark.asyncio
async def test_ma_recent_resolves_identity_and_proxies(client, upstream):
    mock_upstream(
        upstream,
        "GET",
        "http://host.docker.internal:8003/execute/media/music-assistant/recent",
        payload={"status": "SUCCESS", "recent": [{"name": "Fixture Track"}]},
    )

    resp = client.get("/api/media/music-assistant/recent")

    assert resp.status_code == 200
    assert resp.json()["recent"] == [{"name": "Fixture Track"}]
