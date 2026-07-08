# test/local/test_rag_sync.py
import os
import time

import httpx
import pytest

LIVE_TEST_URL = os.getenv("LIVE_TEST_URL")
GATEWAY_URL = LIVE_TEST_URL if LIVE_TEST_URL else "http://localhost:11435"
RAG_URL = "http://localhost:8004"
INTERNAL_SECRET = os.getenv("INTERNAL_SECRET", "change-me-in-production")

@pytest.fixture
def client():
    return httpx.Client(timeout=180.0)

@pytest.mark.local_only
@pytest.mark.skipif(LIVE_TEST_URL is not None, reason="RAG service is internal and not exposed externally")
def test_rag_sync_flow(client):
    """
    Test: Trigger HA entity fetch and verify RAG indexing.
    """
    # 1. Trigger Identity resolution and Entity Fetch in Gateway
    # This should trigger the background RAG sync we just added.
    print("\n[Test] Triggering entity fetch via Gateway...")
    payload = {"query": "list my devices", "voice_id": "default"}
    resp = client.post(f"{GATEWAY_URL}/api/chat", json=payload)
    assert resp.status_code == 200

    # 2. Wait for background task to complete
    print("[Test] Waiting for RAG sync background task...")
    time.sleep(3)

    # 3. Query RAG service directly to see if 'piano_lamp' is indexed
    search_payload = {
        "collection_name": "ha_entities",
        "query": "piano lamp",
        "user_id": "Summers", # From .env HA_DEFAULT_USER
        "k": 1
    }
    print("[Test] Querying RAG Service for 'piano lamp'...")
    search_resp = client.post(
        f"{RAG_URL}/rag/search",
        json=search_payload,
        headers={"X-Internal-Secret": INTERNAL_SECRET}
    )

    assert search_resp.status_code == 200
    results = search_resp.json().get("results", [])

    assert len(results) > 0, "No results found in RAG for 'piano lamp'"
    content = results[0].get("content", "").lower()
    assert "piano_lamp" in content or "piano lamp" in content
    print(f"[Test] Found in RAG: {content[:100]}...")

if __name__ == "__main__":
    pytest.main([__file__, "-s"])
