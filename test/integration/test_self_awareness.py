# test/integration/test_self_awareness.py
import os
import requests
import pytest  # pyright: ignore[reportUnusedImport]

RAG_SVC_URL = os.getenv("RAG_SVC_URL", "http://127.0.0.1:8004")
GATEWAY_SVC_URL = os.getenv("GATEWAY_SVC_URL", "http://127.0.0.1:11435")
INTERNAL_SECRET = os.getenv("INTERNAL_SECRET", "change-me-in-production")

def test_rag_capability_search():
    """Verify that we can search for capabilities in RAG directly."""
    query = "How do I control the lights?"
    resp = requests.post(
        f"{RAG_SVC_URL}/rag/search",
        json={
            "query": query,
            "user_id": "default",
            "collection_name": "system_capabilities",
            "k": 3
        },
        headers={"X-Internal-Secret": INTERNAL_SECRET},
        timeout=10
    )
    assert resp.status_code == 200
    results = resp.json().get("results", [])
    assert len(results) > 0
    
    # Check if LightControlRequest or related schema is in results
    content_blob = "".join([r.get("content", "").lower() for r in results])
    assert "light" in content_blob
    assert "schema" in content_blob

def test_gateway_self_awareness():
    """Verify that the Gateway injects capability context into its reasoning."""
    # We use 'debug': True to see the rag_context in the response
    query = "What is the schema for TV casting?"
    payload = {
        "query": query,
        "user": "admin",
        "debug": True,
        "stream": False
    }
    resp = requests.post(
        f"{GATEWAY_SVC_URL}/api/chat",
        json=payload,
        timeout=30
    )
    assert resp.status_code == 200
    data = resp.json()
    
    # The debug_context should contain the capability info
    debug_context = data.get("debug_context", "")
    assert "System Capability Context" in debug_context
    assert "TVCastRequest" in debug_context
    
    # The assistant response should mention the schema or how to use it
    content = data.get("message", {}).get("content", "").lower()
    assert "tvcastrequest" in content or "power_on_wait_ms" in content

if __name__ == "__main__":
    print("Running integration tests for self-awareness...")
    try:
        test_rag_capability_search()
        print("[OK] RAG Capability Search")
        test_gateway_self_awareness()
        print("[OK] Gateway Self-Awareness Injection")
        print("\nAll self-awareness tests passed!")
    except Exception as e:
        print(f"[FAIL] {e}")
        import traceback
        traceback.print_exc()
        exit(1)
