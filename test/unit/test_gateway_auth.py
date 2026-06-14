import os
import sys
from unittest.mock import MagicMock

# Mock Redis before importing anything that uses it
_mock_redis_async = MagicMock()
_mock_redis = MagicMock()
_mock_redis.asyncio = _mock_redis_async
sys.modules['redis'] = _mock_redis
sys.modules['redis.asyncio'] = _mock_redis_async

os.environ["INTERNAL_SECRET"] = "test-secret"
os.environ["IDENTITY_SVC_URL"] = "http://identity"
os.environ["REDIS_URL"] = "redis://localhost:6379/0"
os.environ["RAG_SVC"] = "http://localhost:8004"

import respx
import httpx
import json
from fastapi.testclient import TestClient
from services.gateway.main import app, STORAGE_SVC
from services.gateway.config import IDENTITY_SVC

client = TestClient(app)

# Use a mutable object to capture the request body across respx callbacks
_capture = {"body": None}

@respx.mock
def test_gateway_extracts_bearer_token():
    """
    Test that the Gateway extracts the Bearer token from the Authorization header
    and passes it to the Identity service's /api/resolve endpoint.
    """
    # Mock identity settings
    respx.get(f"{IDENTITY_SVC}/api/settings").mock(
        return_value=httpx.Response(200, json=[
            {"key": "active_llm_provider", "value": "ollama"},
            {"key": "assistant_model", "value": "qwen3:8b"},
            {"key": "coding_model", "value": "qwen3:8b"},
            {"key": "llm_local_url", "value": "http://localhost:11434"},
            {"key": "embedding_model", "value": "BAAI/bge-small-en-v1.5"},
            {"key": "redis_url", "value": "redis://localhost:6379/0"},
            {"key": "fast_path_threshold", "value": "0.8"},
        ])
    )
    
    # Mock identity resolve - capture the call
    def capture_resolve(request):
        _capture["body"] = json.loads(request.content)
        return httpx.Response(200, json={
            "user": "testuser",
            "ha_url": "http://ha",
            "ha_token": "token",
            "nextcloud_url": "http://nc",
            "nextcloud_user": "ncuser",
            "nextcloud_pass": "ncpass"
        })
    
    respx.post(f"{IDENTITY_SVC}/api/resolve").mock(side_effect=capture_resolve)
    
    # Mock storage index (fast path for "index" query)
    respx.post(f"{STORAGE_SVC}/index/full").mock(
        return_value=httpx.Response(200, json={"message": "Indexing started"})
    )
    
    # Query "index" triggers index_storage intent (confidence=1.0, fast path)
    resp = client.post(
        "/api/chat",
        json={"query": "index"},
        headers={"Authorization": "Bearer sk-test-123"}
    )
    
    assert resp.status_code == 200
    
    # Verify identity was called with the bearer token
    body = _capture["body"]
    assert body is not None
    assert "api_key" in body
    assert body["api_key"] == "sk-test-123"
