import pytest
from fastapi.testclient import TestClient
import os
import sys
from unittest.mock import MagicMock, AsyncMock

# Ensure parent directory is in sys.path for imports
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

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
    
    from main import app
    import main
    # Disable background tasks for testing
    main.background_tasks = None
    
    return TestClient(app)

def test_health_check(client: TestClient):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"

@pytest.mark.asyncio
async def test_chat_conversational_with_mocks(client: TestClient, monkeypatch):
    import main
    # Mock dependencies to avoid real network/ML calls
    monkeypatch.setattr(main, "resolve_identity", AsyncMock(return_value={"user": "alice", "ha_url": "http://ha.local", "ha_token": "tok"}))
    monkeypatch.setattr(main, "fetch_ha_entities", AsyncMock(return_value=[]))
    monkeypatch.setattr(main, "update_history", AsyncMock(return_value=None))
    monkeypatch.setattr(main, "get_history", AsyncMock(return_value=[]))
    monkeypatch.setattr(main, "contextualize_query", AsyncMock(return_value="hello"))
    monkeypatch.setattr(main, "decompose_command_query", AsyncMock(return_value=[]))
    
    # Mock RAG to return empty results
    async def mock_get_rag(coll, query, user_id):
        return []
    # Actually gateway/main.py uses httpx.post for RAG
    # I'll mock the httpx client if possible, or the call_ollama which is the final step
    
    class MockResponse:
        def __init__(self, json_data, status_code=200):
            self.json_data = json_data
            self.status_code = status_code
        def json(self): return self.json_data
        def raise_for_status(self): pass
        @property
        def text(self): return str(self.json_data)

    monkeypatch.setattr(main, "call_ollama", AsyncMock(return_value=MockResponse({"message": {"content": "Mocked LLM response"}})))
    
    # Mock the RAG httpx call
    import httpx
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
