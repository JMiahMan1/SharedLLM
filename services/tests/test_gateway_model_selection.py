import os
from unittest.mock import AsyncMock, patch

os.environ.setdefault("INTERNAL_SECRET", "test-secret")
os.environ.setdefault("FAST_PATH_THRESHOLD", "0.85")
os.environ.setdefault("IDENTITY_SVC_URL", "http://identity")
os.environ.setdefault("EXECUTION_SVC_URL", "http://execution")
os.environ.setdefault("OLLAMA_URL", "http://ollama")
os.environ.setdefault("ASSISTANT_MODEL", "qwen3:latest")
os.environ.setdefault("CODING_MODEL", "qwen2.5-coder:7b")

from fastapi.testclient import TestClient
import pytest

from gateway.main import app, select_model_for_query


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


class MockOllamaResponse:
    def __init__(self, content: str):
        self.status_code = 200
        self._content = content

    def json(self):
        return {"message": {"content": self._content}}


@pytest.mark.parametrize(
    "query",
    [
        "Fix this Python traceback in my FastAPI app",
        "Write a pytest for this bug",
        "Help me refactor this SQL query",
        "Why is this Docker build failing",
        "Show me the git command to squash these commits",
    ],
)
def test_select_model_for_query_uses_coding_model_for_code_requests(query):
    assert select_model_for_query(query) == "qwen2.5-coder:7b"


@pytest.mark.parametrize(
    "query",
    [
        "What is the weather like today?",
        "Summarize my notes from this week",
        "Play some jazz in the kitchen",
    ],
)
def test_select_model_for_query_uses_assistant_model_for_general_requests(query):
    assert select_model_for_query(query) == "qwen3:latest"


def test_chat_slow_path_uses_coding_model_for_code_requests(client):
    captured = {}

    async def mock_call_ollama(payload, use_chat=True):
        captured["payload"] = payload
        captured["use_chat"] = use_chat
        return MockOllamaResponse("Use the stack trace to narrow the failing module.")

    async def passthrough_query(query, history):
        return query

    with patch("gateway.main.resolve_identity", new=AsyncMock(return_value={"user": "alice"})), \
         patch("gateway.main.get_history", new=AsyncMock(return_value=[])), \
         patch("gateway.main.fetch_ha_entities", new=AsyncMock(return_value=[])), \
         patch("gateway.main.contextualize_query", new=AsyncMock(side_effect=passthrough_query)), \
         patch("gateway.main.update_history", new=AsyncMock(return_value=None)), \
         patch("gateway.main.emit_log", new=AsyncMock(return_value=None)), \
         patch("gateway.main.engine.classify", return_value=("unknown", 0.10)), \
         patch("gateway.main.call_ollama", new=AsyncMock(side_effect=mock_call_ollama)):
        response = client.post(
            "/api/chat",
            json={"query": "Help me fix this Python traceback in the gateway service", "voice_id": "alice"},
        )

    assert response.status_code == 200
    assert captured["use_chat"] is True
    assert captured["payload"]["model"] == "qwen2.5-coder:7b"


def test_chat_slow_path_uses_assistant_model_for_general_requests(client):
    captured = {}

    async def mock_call_ollama(payload, use_chat=True):
        captured["payload"] = payload
        captured["use_chat"] = use_chat
        return MockOllamaResponse("The weather looks clear.")

    async def passthrough_query(query, history):
        return query

    with patch("gateway.main.resolve_identity", new=AsyncMock(return_value={"user": "alice"})), \
         patch("gateway.main.get_history", new=AsyncMock(return_value=[])), \
         patch("gateway.main.fetch_ha_entities", new=AsyncMock(return_value=[])), \
         patch("gateway.main.contextualize_query", new=AsyncMock(side_effect=passthrough_query)), \
         patch("gateway.main.update_history", new=AsyncMock(return_value=None)), \
         patch("gateway.main.emit_log", new=AsyncMock(return_value=None)), \
         patch("gateway.main.engine.classify", return_value=("unknown", 0.10)), \
         patch("gateway.main.call_ollama", new=AsyncMock(side_effect=mock_call_ollama)):
        response = client.post(
            "/api/chat",
            json={"query": "What should I make for dinner?", "voice_id": "alice"},
        )

    assert response.status_code == 200
    assert captured["use_chat"] is True
    assert captured["payload"]["model"] == "qwen3:latest"
