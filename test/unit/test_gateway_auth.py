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
        def raise_for_status(self):
            return None

    class MockRequestCM:
        """Mimic aiohttp's _RequestContextManager.

        aiohttp's ``session.get()`` can be either awaited or used as an async
        context manager, and the gateway now does the latter
        (``async with shared_http_client() as client, client.get(...) as resp``).
        A plain coroutine only supports the former, which made this mock fail
        with "coroutine object does not support the asynchronous context
        manager protocol".
        """
        def __init__(self, resp):
            self._resp = resp

        def __await__(self):
            async def _coro():
                return self._resp
            return _coro().__await__()

        async def __aenter__(self):
            return self._resp

        async def __aexit__(self, *a):
            return False

    class MockAsyncClient:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False

        def get(self, url, **kwargs):
            return MockRequestCM(self._get(url, **kwargs))

        def post(self, url, json=None, headers=None, **kwargs):
            return MockRequestCM(self._post(url, json=json, headers=headers, **kwargs))

        def _get(self, url, **kwargs):
            if "/api/settings" in url:
                return MockIdentityResp([
                    {"key": "active_llm_provider", "value": "ollama"},
                    {"key": "assistant_model", "value": "qwen3:8b"},
                    {"key": "coding_model", "value": "qwen3:8b"},
                    {"key": "llm_local_url", "value": "http://localhost:11434"},
                    {"key": "embedding_model", "value": "nomic-ai/nomic-embed-text-v1.5"},
                    {"key": "redis_url", "value": "redis://localhost:6379/0"},
                    {"key": "fast_path_threshold", "value": "0.8"},
                    {"key": "assistant_system_instruction", "value": "You are a test assistant."},
                ])
            return MockIdentityResp({}, 404)

        def _post(self, url, json=None, headers=None, **kwargs):
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

    # Identity resolutions are cached for IDENTITY_CACHE_TTL. If any earlier
    # test already resolved a token, this request is served from that cache and
    # never calls /api/resolve, so _capture["body"] stays None.
    from services.gateway.cache import invalidate_identity

    invalidate_identity()

    # Inference now runs through the Redis-backed job queue, which is created in
    # the app lifespan. A bare TestClient does not run that, so stub the queue
    # the same way services/tests/test_gateway_model_selection.py does — this
    # test is about identity resolution, not inference.
    from unittest.mock import AsyncMock

    from services.gateway.messaging import JobStatus

    mock_jq = AsyncMock()
    mock_jq.enqueue_job = AsyncMock(return_value="test-job-id")
    mock_jq.get_job_status = AsyncMock(
        return_value={"status": JobStatus.COMPLETED, "result": "indexing started"}
    )
    mock_jq.get_chunks = AsyncMock(return_value=[])
    mock_jq.get_queue_position = AsyncMock(return_value=0)
    monkeypatch.setattr(main, "job_queue", mock_jq)

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
