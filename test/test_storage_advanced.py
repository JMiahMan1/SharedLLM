import httpx
import pytest

# Phase 4.3: Test Storage Async Indexing
# This test ensures that indexing requests return 202 Accepted immediately.

SECRET = "test-secret-storage"


@pytest.mark.asyncio
async def test_storage_indexing_is_async(monkeypatch):
    # /index/full is now gated by _require_internal_secret; without the header
    # the request is rejected with 403 before it ever reaches the handler.
    # Pin the secret rather than relying on whatever another test module set.
    monkeypatch.setenv("INTERNAL_SECRET", SECRET)

    from services.storage.main import app

    payload = {
        "provider": {
            "kind": "nextcloud",
            "settings": {"url": "http://cloud", "username": "admin", "password": "abc"}
        },
        "path": "/",
        "recursive": True
    }

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.post(
            "/index/full", json=payload, headers={"X-Internal-Secret": SECRET}
        )
        # 1. Assert status code is 202
        assert resp.status_code == 202
        assert resp.json()["status"] == "ACCEPTED"
