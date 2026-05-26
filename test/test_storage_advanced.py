import pytest
import httpx

# Phase 4.3: Test Storage Async Indexing
# This test ensures that indexing requests return 202 Accepted immediately.

@pytest.mark.asyncio
async def test_storage_indexing_is_async():
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
        resp = await ac.post("/index/full", json=payload)
        # 1. Assert status code is 202
        assert resp.status_code == 202
        assert resp.json()["status"] == "ACCEPTED"
