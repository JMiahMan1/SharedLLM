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

import json  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

from services.gateway.main import app  # noqa: E402

client = TestClient(app)

# Use a mutable object to capture the request body across respx callbacks
_capture = {"body": None}

def test_gateway_extracts_bearer_token(monkeypatch):
    """
    Test that the Gateway extracts the Bearer token from the Authorization header
    and passes it to the Identity service's /api/resolve endpoint.
    """
    from services.gateway import main
    class MockIdentityResp:
        def __init__(self, payload, status=200):
            self._payload = payload
            self.status = status
        async def json(self):
            return self._payload
        async def text(self):
            return json.dumps(self._payload)

    class MockAsyncClient:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False

        async def get(self, url, **kwargs):
            if "/api/settings" in url:
                return MockIdentityResp([
                    {"key": "active_llm_provider", "value": "ollama"},
                    {"key": "assistant_model", "value": "qwen3:8b"},
                    {"key": "coding_model", "value": "qwen3:8b"},
                    {"key": "llm_local_url", "value": "http://localhost:11434"},
                    {"key": "embedding_model", "value": "nomic-ai/nomic-embed-text-v1.5"},
                    {"key": "redis_url", "value": "redis://localhost:6379/0"},
                    {"key": "fast_path_threshold", "value": "0.8"},
                ])
            return MockIdentityResp({}, 404)

        async def post(self, url, json=None, headers=None, **kwargs):
            if "/api/resolve" in url:
                _capture["body"] = json
                return MockIdentityResp({
                    "user": "testuser",
                    "ha_url": "http://ha",
                    "ha_token": "token",
                    "nextcloud_url": "http://nc",
                    "nextcloud_user": "ncuser",
                    "nextcloud_pass": "ncpass"
                })
            if "/index/full" in url:
                return MockIdentityResp({"message": "Indexing started"})
            return MockIdentityResp({}, 404)

    monkeypatch.setattr(main, "get_http_client", lambda: MockAsyncClient())

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
