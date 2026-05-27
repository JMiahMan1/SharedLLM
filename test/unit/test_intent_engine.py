import pytest
from unittest.mock import MagicMock, patch, AsyncMock
from services.gateway.intent_engine import IntentEngine

@pytest.fixture
def engine():
    # Patch the model loading to avoid heavy downloads during tests
    with patch("services.gateway.intent_engine.json.load", return_value={"turn_on": ["turn on light"]}):
        with patch("builtins.open", MagicMock()):
            # We don't need to patch TextEmbedding if we manually set the model
            engine = IntentEngine()
            engine.model = MagicMock()
            engine.intent_labels = ["turn_on"]
            engine.intent_embeddings = [ [0.1] * 384 ] # Mock embedding vector
            return engine

def test_semantic_router_fast_path_logic(engine):
    assert engine.should_bypass_llm(0.95) is True
    assert engine.should_bypass_llm(0.80) is False

@pytest.mark.asyncio
async def test_gateway_skips_llm_on_high_confidence(mocker):
    from services.gateway.main import chat_handler
    from fastapi import Request
    from fastapi.responses import JSONResponse
    
    mocker.patch("services.gateway.main.resolve_identity", new_callable=AsyncMock, return_value={
        "user": "admin",
        "ha_url": "http://ha",
        "ha_token": "token"
    })
    mocker.patch("services.gateway.main.get_history", new_callable=AsyncMock, return_value=[])
    mocker.patch("services.gateway.main.get_long_term_memory", new_callable=AsyncMock, return_value="")
    
    # Mock the global engine object in main.py
    mocker.patch("services.gateway.main.engine.classify", return_value=("turn_on", 0.98))
    mocker.patch("services.gateway.main.engine.should_bypass_llm", return_value=True)
    
    mock_exec = mocker.patch("services.gateway.main.execute_command", new_callable=AsyncMock, return_value={"status": "SUCCESS", "message": "Lights on"})
    mock_ollama = mocker.patch("services.gateway.main.call_ollama", new_callable=AsyncMock)

    request = MagicMock(spec=Request)
    request.json = AsyncMock(return_value={"query": "turn on lights"})
    request.url = MagicMock()
    request.url.path = "/api/chat"
    request.headers = {}

    response = await chat_handler(request)
    assert isinstance(response, JSONResponse)
    assert response.status_code == 200
    mock_ollama.assert_not_called()
    mock_exec.assert_called_once()
