import pytest
from fastapi.testclient import TestClient
import os
from unittest.mock import MagicMock

@pytest.fixture(name="client")
def client_fixture(monkeypatch):
    import sys
    from unittest.mock import MagicMock
    
    # Mocking heavy/problematic dependencies
    sys.modules["fastembed"] = MagicMock()
    
    # Mock intent_engine and background_worker
    mock_engine = MagicMock()
    mock_engine.engine = MagicMock()
    mock_engine.engine.classify.return_value = ("unknown", 0.0)
    sys.modules["intent_engine"] = mock_engine
    
    mock_worker = MagicMock()
    sys.modules["background_worker"] = mock_worker
    
    from main import app
    return TestClient(app)

def test_health_check(client: TestClient):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"

def test_chat_mocked(client: TestClient, monkeypatch):
    import main
    
    # Mocking resolve_identity
    async def mock_resolve(body):
        return {
            "user": "default",
            "is_admin": True,
            "ha_url": "http://ha",
            "ha_token": "token",
            "nextcloud_url": "http://nc",
            "nextcloud_user": "user",
            "nextcloud_pass": "pass"
        }
    
    # Mocking call_ollama or chat logic
    # In gateway/main.py, chat_handler calls call_ollama or similar
    # Actually, I'll mock the whole handler response if possible, 
    # but I want to test the routing logic.
    
    monkeypatch.setattr(main, "resolve_identity", mock_resolve)
    
    # Mock call_ollama to avoid real network calls
    async def mock_ollama(payload, use_chat=True):
        m = MagicMock()
        m.status_code = 200
        m.json.return_value = {"message": {"content": "Mocked LLM response"}, "done": True, "response": "Mocked LLM response"}
        return m
    
    monkeypatch.setattr(main, "call_ollama", mock_ollama)
    
    payload = {"query": "hello", "user_id": "default"}
    resp = client.post("/api/chat", json=payload, headers={"X-Internal-Secret": "change-me-in-production"})
    assert resp.status_code == 200
    assert "Mocked" in resp.json()["message"]["content"]
