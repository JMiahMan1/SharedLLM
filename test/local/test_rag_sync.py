# test/local/test_rag_sync.py
import os
import time

import httpx
import pytest

LIVE_TEST_URL = os.getenv("LIVE_TEST_URL")
GATEWAY_URL = LIVE_TEST_URL if LIVE_TEST_URL else "http://localhost:11435"
RAG_URL = "http://localhost:8004"
INTERNAL_SECRET = os.environ.get("INTERNAL_SECRET", "")
# Flags (no real defaults; the test skips without them):
#   HA_DEFAULT_USER       the RAG user the household's entities are indexed under
#   LIVE_HA_ENTITY_QUERY  a friendly name that exists in that Home Assistant (e.g. "piano lamp")
RAG_USER = os.getenv("HA_DEFAULT_USER", "")
ENTITY_QUERY = os.getenv("LIVE_HA_ENTITY_QUERY", "")

@pytest.fixture
def client():
    return httpx.Client(timeout=180.0)

@pytest.mark.local_only
@pytest.mark.skipif(LIVE_TEST_URL is not None, reason="RAG service is internal and not exposed externally")
@pytest.mark.skipif(not (RAG_USER and ENTITY_QUERY), reason="Set HA_DEFAULT_USER and LIVE_HA_ENTITY_QUERY to run the RAG sync test")
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
        "query": ENTITY_QUERY,
        "user_id": RAG_USER,
        "k": 1
    }
    print(f"[Test] Querying RAG Service for {ENTITY_QUERY!r}...")
    search_resp = client.post(
        f"{RAG_URL}/rag/search",
        json=search_payload,
        headers={"X-Internal-Secret": INTERNAL_SECRET}
    )

    assert search_resp.status_code == 200
    results = search_resp.json().get("results", [])

    assert len(results) > 0, f"No results found in RAG for {ENTITY_QUERY!r}"
    content = results[0].get("content", "").lower()
    assert ENTITY_QUERY.lower() in content or ENTITY_QUERY.lower().replace(" ", "_") in content
    print(f"[Test] Found in RAG: {content[:100]}...")

if __name__ == "__main__":
    pytest.main([__file__, "-s"])
