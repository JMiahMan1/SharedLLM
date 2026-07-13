from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.gateway.intent_engine import IntentEngine


@pytest.fixture
def engine():
    # Patch the model loading to avoid heavy downloads during tests
    with patch("services.gateway.intent_engine.json.load", return_value={"turn_on": ["turn on light"]}), patch("builtins.open", MagicMock()):
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
    """
    Test that the Gateway fast path executes immediately without calling Ollama
    when intent confidence exceeds the threshold.
    """
    from fastapi import Request
    from fastapi.responses import JSONResponse

    from services.gateway.main import chat_handler

    # Mock identity resolution
    mocker.patch("services.gateway.main.resolve_identity", new_callable=AsyncMock, return_value={
        "user": "admin",
        "ha_url": "http://ha",
        "ha_token": "token"
    })
    mocker.patch("services.gateway.main.get_assistant_model", new_callable=AsyncMock, return_value="qwen3:8b")
    mocker.patch("services.gateway.main.get_history", new_callable=AsyncMock, return_value=[])
    mocker.patch("services.gateway.main.get_long_term_memory", new_callable=AsyncMock, return_value="")

    # Force high confidence fast path
    mocker.patch("services.gateway.main.engine.classify", return_value=("turn_on", 0.98))
    mocker.patch("services.gateway.main.engine.should_bypass_llm", return_value=True)

    # Mock resolve_media_target to return an entity so fast path proceeds
    mocker.patch("services.gateway.main.resolve_media_target", return_value="light.office")

    # Mock fetch_ha_entities for the fast path
    mocker.patch("services.gateway.main.fetch_ha_entities", return_value=[])

    # Mock shared_http_client to simulate execution service response
    mock_resp = AsyncMock()
    mock_resp.status = 200
    mock_resp.text = AsyncMock(return_value='{"status": "SUCCESS", "message": "Lights on"}')
    mock_resp.json = AsyncMock(return_value={"status": "SUCCESS", "message": "Lights on"})
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_resp)
    mock_client.get = AsyncMock(return_value=mock_resp)

    from contextlib import asynccontextmanager
    @asynccontextmanager
    async def mock_shared_client():
        yield mock_client

    mocker.patch("services.gateway.main.shared_http_client", side_effect=mock_shared_client)

    # Mock update_history
    mocker.patch("services.gateway.main.update_history", new_callable=AsyncMock)
    mocker.patch("services.gateway.main.set_last_used_device")

    # Mock call_ollama to track if it's called
    mock_ollama = mocker.patch("services.gateway.main.call_ollama", new_callable=AsyncMock)

    request = MagicMock(spec=Request)
    request.json = AsyncMock(return_value={"query": "turn on lights"})
    request.url = MagicMock()
    request.url.path = "/api/chat"
    request.headers = {}

    response = await chat_handler(request)
    assert isinstance(response, JSONResponse)
    assert response.status_code == 200
    # LLM should NOT be called on fast path
    mock_ollama.assert_not_called()
    # HTTP call to execution service SHOULD be made
    mock_client.post.assert_called_once()
