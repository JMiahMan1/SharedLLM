import pytest
import sys
from fastapi import Request
from fastapi.testclient import TestClient
import os
from typing import cast
from unittest.mock import MagicMock, AsyncMock

os.environ["INTERNAL_SECRET"] = "test-secret"

@pytest.fixture(name="client")
def client_fixture(monkeypatch):
    # Mocking heavy/problematic dependencies
    sys.modules["fastembed"] = MagicMock()
    
    # Mock intent_engine and background_worker
    mock_engine = MagicMock()
    mock_engine.engine = MagicMock()
    mock_engine.engine.classify.return_value = ("unknown", 0.0)
    mock_engine.engine.should_bypass_llm.return_value = False
    sys.modules["intent_engine"] = mock_engine
    
    mock_worker = MagicMock()
    sys.modules["background_worker"] = mock_worker
    
    from services.gateway.main import app
    from services.gateway import main
    # Disable background tasks for testing
    main.background_tasks = None  # pyright: ignore[reportAttributeAccessIssue]
    
    return TestClient(app)

def test_health_check(client: TestClient):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"

@pytest.mark.asyncio
async def test_bulk_settings_proxy_forwards_post(monkeypatch):
    from services.gateway import main

    captured = {}

    class MockResponse:
        def __init__(self, status_code=200, payload=None):
            self.status = status_code
            self._payload = payload or {"status": "SUCCESS"}

        async def json(self):
            return self._payload

    class MockAsyncClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, json=None, headers=None):
            captured["url"] = url
            captured["json"] = json
            captured["headers"] = headers
            return MockResponse(payload={"status": "SUCCESS"})

    monkeypatch.setattr(main.aiohttp, "ClientSession", lambda timeout=None: MockAsyncClient())

    class FakeRequest:
        headers = {"Authorization": "Bearer test-token"}

        async def json(self):
            return {"assistant_model": "qwen3.5:14b"}

    resp = await main.proxy_update_settings_bulk(cast(Request, FakeRequest()))

    assert resp.status_code == 200
    assert captured["url"].endswith("/api/settings")
    assert captured["json"] == {"assistant_model": "qwen3.5:14b"}
    assert captured["headers"] == {"Authorization": "Bearer test-token"}

@pytest.mark.asyncio
async def test_chat_conversational_with_mocks(client: TestClient, monkeypatch):
    from services.gateway import main
    # Mock dependencies to avoid real network/ML calls
    monkeypatch.setattr(main, "resolve_identity", AsyncMock(return_value={"user": "alice", "ha_url": "http://ha.local", "ha_token": "tok"}))
    monkeypatch.setattr(main, "fetch_ha_entities", AsyncMock(return_value=[]))
    monkeypatch.setattr(main, "update_history", AsyncMock(return_value=None))
    monkeypatch.setattr(main, "get_history", AsyncMock(return_value=[]))
    monkeypatch.setattr(main, "contextualize_query", AsyncMock(return_value="hello"))
    monkeypatch.setattr(main, "decompose_command_query", AsyncMock(return_value=[]))
    monkeypatch.setattr(main, "get_llm_settings", AsyncMock(return_value={"active_llm_provider": "ollama", "assistant_model": "qwen3:8b"}))
    monkeypatch.setattr(main, "get_assistant_model", AsyncMock(return_value="qwen3:8b"))
    monkeypatch.setattr(main, "fetch_global_setting", AsyncMock(return_value="0.85"))
    # Mock system instruction loading to avoid real Identity service calls
    monkeypatch.setattr(main, "select_system_instruction_for_query", lambda q, m: "# System instruction mock")
    
    # Mock the job queue to avoid Redis calls
    mock_job_queue = MagicMock()
    mock_job_queue.enqueue_job = AsyncMock(return_value="test-job-123")
    mock_job_queue.get_job_status = AsyncMock(return_value={
        "status": "completed",
        "result": "Mocked LLM response"
    })
    mock_job_queue.get_chunks = AsyncMock(return_value=["Mocked LLM response"])
    monkeypatch.setattr(main, "job_queue", mock_job_queue)
    
    class MockResponse:
        def __init__(self, json_data, status_code=200):
            self.json_data = json_data
            self.status = status_code
        async def json(self): return self.json_data
        def raise_for_status(self): pass
        async def text(self): return str(self.json_data)

    monkeypatch.setattr(main, "call_ollama", AsyncMock(return_value=MockResponse({"message": {"content": "Mocked LLM response"}})))
    
    # Mock the RAG httpx call
    async def mock_post_rag(*args, **kwargs):
        if "/rag/search" in args[0]:
            return MockResponse({"results": []})
        return MockResponse({"status": "SUCCESS"})
    
    # We need to mock get_http_client().post
    mock_http = MagicMock()
    mock_http.post = AsyncMock(side_effect=mock_post_rag)
    monkeypatch.setattr(main, "get_http_client", lambda: mock_http)

    resp = client.post("/api/chat", json={
        "query": "hello",
        "user_id": "alice"
    }, headers={"X-Internal-Secret": "test-secret"})
    
    assert resp.status_code == 200
    data = resp.json()
    assert "Mocked" in data["message"]["content"]


@pytest.mark.asyncio
async def test_openai_chat_completions(client: TestClient, monkeypatch):
    from services.gateway import main
    
    # Mock resolve_identity, get_llm_settings, get_assistant_model, update_history
    monkeypatch.setattr(main, "resolve_identity", AsyncMock(return_value={"user": "alice"}))
    monkeypatch.setattr(main, "update_history", AsyncMock(return_value=None))
    monkeypatch.setattr(main, "get_llm_settings", AsyncMock(return_value={"active_llm_provider": "ollama", "assistant_model": "qwen3:8b"}))
    monkeypatch.setattr(main, "get_assistant_model", AsyncMock(return_value="qwen3:8b"))
    monkeypatch.setattr(main, "fetch_global_setting", AsyncMock(return_value="0.85"))
    # Mock system instruction loading to avoid real Identity service calls
    monkeypatch.setattr(main, "select_system_instruction_for_query", lambda q, m: "# System instruction mock")
    
    # Mock the job queue to return a simulated response
    mock_job_queue = MagicMock()
    mock_job_queue.enqueue_job = AsyncMock(return_value="test-job-123")
    mock_job_queue.get_job_status = AsyncMock(return_value={
        "status": "completed",
        "result": "Hello from OpenAI compatible endpoint!"
    })
    mock_job_queue.get_chunks = AsyncMock(return_value=["Hello from OpenAI compatible endpoint!"])
    monkeypatch.setattr(main, "job_queue", mock_job_queue)
    
    # Post to /v1/chat/completions in standard OpenAI format
    resp = client.post("/v1/chat/completions", json={
        "model": "qwen3:8b",
        "messages": [{"role": "user", "content": "hello"}],
        "stream": False
    }, headers={"X-Internal-Secret": "test-secret"})
    
    assert resp.status_code == 200
    data = resp.json()
    assert data["object"] == "chat.completion"
    assert data["choices"][0]["message"]["content"] == "Hello from OpenAI compatible endpoint!"


@pytest.mark.asyncio
async def test_list_openai_models(client: TestClient, monkeypatch):
    from services.gateway import main
    monkeypatch.setattr(main, "get_llm_settings", AsyncMock(return_value={
        "assistant_model": "qwen3.6-35b-a3b:q4_k_m",
        "coding_model": "qwen2.5-coder:7b",
        "librarian_model": "qwen3:8b"
    }))
    
    # Mock provider to be openrouter so we fall back to settings
    from services.gateway.llm_providers import OpenRouterProvider
    mock_provider = MagicMock(spec=OpenRouterProvider)
    monkeypatch.setattr(main, "get_provider", AsyncMock(return_value=mock_provider))
    
    resp = client.get("/v1/models")
    assert resp.status_code == 200
    data = resp.json()
    assert data["object"] == "list"
    assert len(data["data"]) >= 3
    model_ids = [m["id"] for m in data["data"]]
    assert "qwen3.6-35b-a3b:q4_k_m" in model_ids


@pytest.mark.asyncio
async def test_list_ollama_tags(client: TestClient, monkeypatch):
    from services.gateway import main
    monkeypatch.setattr(main, "get_all_settings", AsyncMock(return_value={
        "llm_local_url": "http://ollama.local"
    }))
    
    class MockResponse:
        def __init__(self, json_data, status_code=200):
            self.json_data = json_data
            self.status = status_code
        async def json(self): return self.json_data
        def raise_for_status(self): pass
        async def text(self): return str(self.json_data)
        
    class MockAsyncClient:
        async def __aenter__(self): return self
        async def __aexit__(self, exc_type, exc, tb): return False
        async def get(self, url, params=None, headers=None):
            if "/api/tags" in url:
                return MockResponse({
                    "models": [
                        {
                            "name": "qwen3.6-35b-a3b:q4_k_m",
                            "model": "qwen3.6-35b-a3b:q4_k_m"
                        }
                    ]
                })
            return MockResponse({})
            
    monkeypatch.setattr(main.aiohttp, "ClientSession", lambda *args, **kwargs: MockAsyncClient())
    
    resp = client.get("/api/tags")
    assert resp.status_code == 200
    data = resp.json()
    assert "models" in data
    assert len(data["models"]) >= 1
    assert data["models"][0]["name"] == "qwen3.6-35b-a3b:q4_k_m"


@pytest.mark.asyncio
async def test_proxy_show_embed_embeddings(client: TestClient, monkeypatch):
    from services.gateway import main
    monkeypatch.setattr(main, "get_all_settings", AsyncMock(return_value={
        "llm_local_url": "http://ollama.local"
    }))
    
    class MockResponse:
        def __init__(self, json_data, status_code=200):
            self.json_data = json_data
            self.status = status_code
        async def json(self): return self.json_data
        def raise_for_status(self): pass
        async def text(self): return str(self.json_data)
        
    class MockAsyncClient:
        async def __aenter__(self): return self
        async def __aexit__(self, exc_type, exc, tb): return False
        async def post(self, url, json=None, headers=None):
            if "/api/show" in url:
                return MockResponse({"modelfile": "FROM qwen3.6-35b-a3b:q4_k_m"})
            elif "/api/embeddings" in url:
                return MockResponse({"embedding": [0.1, 0.2, 0.3]})
            elif "/api/embed" in url:
                return MockResponse({"embeddings": [[0.1, 0.2, 0.3]]})
            return MockResponse({})
            
    monkeypatch.setattr(main.aiohttp, "ClientSession", lambda *args, **kwargs: MockAsyncClient())
    
    # 1. /api/show
    resp_show = client.post("/api/show", json={"name": "qwen3.6-35b-a3b:q4_k_m"})
    assert resp_show.status_code == 200
    assert "modelfile" in resp_show.json()
    
    # 2. /api/embeddings
    resp_embeddings = client.post("/api/embeddings", json={"model": "qwen3.6-35b-a3b:q4_k_m", "prompt": "hello"})
    assert resp_embeddings.status_code == 200
    assert "embedding" in resp_embeddings.json()
    
    # 3. /api/embed
    resp_embed = client.post("/api/embed", json={"model": "qwen3.6-35b-a3b:q4_k_m", "input": ["hello"]})
    assert resp_embed.status_code == 200
    assert "embeddings" in resp_embed.json()
    
    # 4. /v1/embeddings
    resp_openai_embed = client.post("/v1/embeddings", json={"model": "qwen3.6-35b-a3b:q4_k_m", "input": "hello"})
    assert resp_openai_embed.status_code == 200
    openai_data = resp_openai_embed.json()
    assert openai_data["object"] == "list"
    assert len(openai_data["data"]) == 1
    assert openai_data["data"][0]["object"] == "embedding"
    assert openai_data["data"][0]["embedding"] == [0.1, 0.2, 0.3]
