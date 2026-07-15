import json
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, create_engine, select

from services.gateway import main as gateway_main
from services.gateway.main import app
from services.identity.models import GlobalSetting

_mock_redis_async = MagicMock()
_mock_redis = MagicMock()
_mock_redis.asyncio = _mock_redis_async
sys.modules['redis'] = _mock_redis
sys.modules['redis.asyncio'] = _mock_redis_async

# Provide default model values so get_test_settings() doesn't raise
os.environ.setdefault("ASSISTANT_MODEL", "qwen3:8b")
os.environ.setdefault("CODING_MODEL", "qwen2.5-coder:7b")
os.environ.setdefault("LIBRARIAN_MODEL", "qwen3:8b")
os.environ.setdefault("EMBEDDING_MODEL", "nomic-ai/nomic-embed-text-v1.5")


class MockRequestContextManager:
    def __init__(self, response):
        self.response = response

    def __await__(self):
        async def _async_func():
            return self.response
        return _async_func().__await__()

    async def __aenter__(self):
        return self.response

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        pass


def _aio_resp(status=200, json_data=None, text=None):
    if json_data is None:
        json_data = {"status": "SUCCESS"}
    if text is None:
        text = json.dumps(json_data)
    m = MagicMock()
    m.status = status
    m.json = AsyncMock(return_value=json_data)
    m.text = AsyncMock(return_value=text)
    m.content = MagicMock()
    m.content.iter_chunked = MagicMock(return_value=iter([text.encode()]))
    m.read = AsyncMock(return_value=text.encode())
    m.release = AsyncMock()
    m.raise_for_status = MagicMock()
    return m


def get_test_settings():
    db_path = "/data/identity.db"
    if not os.path.exists(db_path):
        _root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
        db_path = os.path.join(_root, "data", "identity.db")

    settings = {}
    if os.path.exists(db_path) and os.path.getsize(db_path) > 0:
        try:
            engine = create_engine(f"sqlite:///{db_path}")
            with Session(engine) as session:
                for s in session.exec(select(GlobalSetting)).all():
                    settings[s.key] = s.value
        except Exception:
            pass

    if not settings.get("assistant_model") or not settings.get("coding_model"):
        identity_url = os.getenv("IDENTITY_SVC_URL", "http://127.0.0.1:8001")
        internal_secret = os.getenv("INTERNAL_SECRET", "change-me-in-production")
        try:
            import aiohttp as _aio
            with _aio.ClientSession(timeout=5.0) as client:
                resp = client.get(
                    f"{identity_url}/api/settings",
                    headers={"X-Internal-Secret": internal_secret}
                )
                if resp.status_code == 200:
                    for item in resp.json():
                        settings[item["key"]] = item["value"]
        except Exception:
            pass

    if not settings.get("assistant_model"):
        settings["assistant_model"] = os.getenv("ASSISTANT_MODEL")
    if not settings.get("coding_model"):
        settings["coding_model"] = os.getenv("CODING_MODEL")
    if not settings.get("librarian_model"):
        settings["librarian_model"] = os.getenv("LIBRARIAN_MODEL") or settings.get("assistant_model")
    if not settings.get("llm_local_url"):
        settings["llm_local_url"] = os.getenv("OLLAMA_URL") or "http://localhost:11434"
    if not settings.get("embedding_model"):
        settings["embedding_model"] = os.getenv("EMBEDDING_MODEL") or "nomic-ai/nomic-embed-text-v1.5"
    if not settings.get("active_llm_provider"):
        settings["active_llm_provider"] = os.getenv("ACTIVE_LLM_PROVIDER") or "ollama"

    for key, env_var in [
        ("identity_svc_url", "IDENTITY_SVC_URL"),
        ("execution_svc_url", "EXECUTION_SVC_URL"),
        ("rag_svc_url", "RAG_SVC_URL"),
        ("storage_svc_url", "STORAGE_SVC_URL"),
        ("logging_svc_url", "LOGGING_SVC_URL"),
        ("workspace_runtime_svc_url", "WORKSPACE_RUNTIME_SVC_URL"),
        ("control_plane_url", "CONTROL_PLANE_URL"),
        ("llama_server_proxy_url", "LLAMA_SERVER_PROXY_URL")
    ]:
        if os.getenv(env_var):
            settings[key] = os.getenv(env_var)

    return settings


client = TestClient(app)


@pytest.fixture
def auth_headers():
    return {"Authorization": "Bearer test-token"}


def _make_session(settings, ollama_iter):
    """Mock aiohttp session; monkeypatch get_http_client to return it."""
    settings_list = [{"key": k, "value": v} for k, v in settings.items()]

    def post_side_effect(url, **kwargs):
        if "/api/resolve" in url:
            return MockRequestContextManager(_aio_resp(200, {
                "user": "testuser", "is_admin": True,
                "ha_url": "http://ha.local", "ha_token": "token",
                "nextcloud_url": "http://nc.local",
                "nextcloud_user": "ncuser", "nextcloud_pass": "ncpass",
            }))
        if "/rag/search" in url:
            return MockRequestContextManager(_aio_resp(200, {"results": []}))
        if "/api/chat" in url:
            return MockRequestContextManager(_aio_resp(200, next(ollama_iter)))
        if "/index/full" in url:
            return MockRequestContextManager(_aio_resp(200, {"message": "Indexing started"}))
        return MockRequestContextManager(_aio_resp(200, {}))

    def get_side_effect(url, **kwargs):
        if "/api/settings" in url:
            return MockRequestContextManager(_aio_resp(200, settings_list))
        return MockRequestContextManager(_aio_resp(200, {}))

    sess = MagicMock()
    sess.post = MagicMock(side_effect=post_side_effect)
    sess.get = MagicMock(side_effect=get_side_effect)
    sess.__aenter__ = AsyncMock(return_value=sess)
    sess.__aexit__ = AsyncMock(return_value=False)
    return sess


@pytest.mark.asyncio
async def test_chat_storage_routing(auth_headers, monkeypatch):
    settings = get_test_settings()

    # Mock system instruction loading to avoid real Identity service calls
    gateway_main.select_system_instruction_for_query = lambda q, m: "# System instruction mock"
    gateway_main.load_prompt = AsyncMock(return_value="# Raven protocol mock")
    import services.gateway.orchestrator as orchestrator
    monkeypatch.setattr(orchestrator, "load_prompt_sync", lambda k: "# Single turn guide mock")

    mock_job_queue = MagicMock()
    mock_job_queue._jobs = {}

    async def mock_enqueue_job(user_id, job_payload):
        from services.gateway.orchestrator import process_full_orchestration
        ans = await process_full_orchestration(job_payload)
        mock_job_queue._jobs["test-job-123"] = {
            "status": "completed",
            "result": ans
        }
        return "test-job-123"

    async def mock_get_job_status(job_id):
        return mock_job_queue._jobs.get(job_id, {"status": "pending"})

    async def mock_get_chunks(job_id):
        job = mock_job_queue._jobs.get(job_id)
        return [job["result"]] if job else []

    mock_job_queue.enqueue_job = mock_enqueue_job
    mock_job_queue.get_job_status = mock_get_job_status
    mock_job_queue.get_chunks = mock_get_chunks
    monkeypatch.setattr(gateway_main, "job_queue", mock_job_queue)

    # Simulate a generated JSON tool block from LLM, then a conversational response.
    responses = [
        {
            "model": settings.get("assistant_model"),
            "message": {
                "role": "assistant",
                "content": "Sure, I will index your storage.\n```json\n{\"action\": \"storageindexrequest\", \"payload\": {\"path\": \"/myfolder\"}}\n```"
            },
            "done": True
        },
        {
            "model": settings.get("assistant_model"),
            "message": {
                "role": "assistant",
                "content": "I have started the indexing process for you. (System Update: Indexing started)"
            },
            "done": True
        }
    ]
    ollama_iter = iter(responses)

    sess = _make_session(settings, ollama_iter)
    monkeypatch.setattr(gateway_main, "get_http_client", lambda: sess)

    # Also cover orchestrator's direct aiohttp.ClientSession usage for /api/settings
    with patch("aiohttp.ClientSession", return_value=sess):
        # Trigger chat handler
        response = client.post("/api/chat", json={
            "query": "index my storage please",
            "stream": False
        }, headers=auth_headers)

    assert response.status_code == 200
    data = response.json()
    assert "Indexing started" in data["message"]["content"]

    # 1. Verify the anti-refusal nudge was injected into the prompt
    chat_calls = [c for c in sess.post.call_args_list if "/api/chat" in str(c[0][0])]
    assert chat_calls, "Ollama /api/chat was not called"
    llm_request_payload = chat_calls[0][1]["json"]
    system_prompt = llm_request_payload["messages"][0]["content"]
    assert "CRITICAL DIRECTIVE: You have full permission to access the storage system" in system_prompt

    # 2. Verify the storage routing correctly mapped the payload
    storage_calls = [c for c in sess.post.call_args_list if "/index/full" in str(c[0][0])]
    assert storage_calls, "Storage /index/full was not called"
    storage_request_payload = storage_calls[0][1]["json"]

    # 3. Verify it overrode the payload to match NextCloud Provider schema
    assert "provider" in storage_request_payload
    assert storage_request_payload["provider"]["kind"] == "nextcloud"
    assert storage_request_payload["provider"]["settings"]["username"] == "ncuser"
    assert storage_request_payload["provider"]["settings"]["password"] == "ncpass"
    assert storage_request_payload["path"] == "/myfolder"
    assert storage_request_payload["recursive"] is True
