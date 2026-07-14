import os
import sys
from contextlib import asynccontextmanager

os.environ["INTERNAL_SECRET"] = "test-secret"

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient


def _aio_resp(status=200, json_data=None, text=""):
    """aiohttp-compatible mock response (code does `await resp.json()`/`resp.status`)."""
    m = MagicMock()
    m.status = status
    m.json = AsyncMock(return_value=json_data if json_data is not None else {"status": "SUCCESS"})
    m.text = AsyncMock(return_value=text)
    return m




@pytest.fixture(name="client")
def client_fixture(monkeypatch):
    """Setup gateway test client with mocked dependencies."""
    sys.modules["fastembed"] = MagicMock()
    mock_intent_engine = MagicMock()
    mock_intent_engine.engine = MagicMock()
    mock_intent_engine.engine.classify.return_value = ("unknown", 0.0)
    mock_intent_engine.engine.should_bypass_llm.return_value = False
    sys.modules["intent_engine"] = mock_intent_engine
    sys.modules["background_worker"] = MagicMock()

    from services.gateway import main
    from services.gateway.main import app

    @asynccontextmanager
    async def noop_lifespan(_app):
        yield

    monkeypatch.setattr(app.router, "lifespan_context", noop_lifespan)
    main.background_tasks = None  # pyright: ignore[reportAttributeAccessIssue]

    return TestClient(app)


@pytest.mark.asyncio
async def test_gateway_search_ma_library_only_default_not_found(monkeypatch, client):
    """Verify gateway search with library_only=True (default) returns empty results for Miles Davis."""
    from services.gateway import main as gateway_main

    mock_creds = {"user": "testuser"}
    mock_search_results = {"status": "SUCCESS", "results": []}

    with patch.object(gateway_main, '_resolve_identity_from_request', new=AsyncMock(return_value=mock_creds)):
        mock_response = _aio_resp(200, mock_search_results)

        mock_get = AsyncMock(return_value=mock_response)

        with patch('services.gateway.main.aiohttp.ClientSession.get', mock_get) as mock_http_get:
            resp = client.get("/api/media/music-assistant/search?query=Miles+Davis")

            assert resp.status_code == 200
            assert resp.json() == mock_search_results

            _called_url, called_kwargs = mock_http_get.call_args
            called_params = called_kwargs.get("params")
            assert called_params["query"] == "Miles Davis"
            assert called_params["library_only"] == "True"


@pytest.mark.asyncio
async def test_gateway_search_ma_not_library_only_found(monkeypatch, client):
    """Verify gateway search with library_only=False returns Miles Davis results."""
    from services.gateway import main as gateway_main

    mock_creds = {"user": "testuser"}
    mock_search_results = {
        "status": "SUCCESS",
        "results": [{"name": "Miles Davis - So What", "uri": "spotify://track/miles123"}]
    }

    with patch.object(gateway_main, '_resolve_identity_from_request', new=AsyncMock(return_value=mock_creds)):
        mock_response = _aio_resp(200, mock_search_results)

        mock_get = AsyncMock(return_value=mock_response)

        with patch('services.gateway.main.aiohttp.ClientSession.get', mock_get) as mock_http_get:
            resp = client.get("/api/media/music-assistant/search?query=Miles+Davis&library_only=false")

            assert resp.status_code == 200
            assert resp.json() == mock_search_results

            _called_url, called_kwargs = mock_http_get.call_args
            called_params = called_kwargs.get("params")
            assert called_params["library_only"] == "False"


@pytest.mark.asyncio
async def test_gateway_search_ma_failure_propagation(monkeypatch, client):
    """Verify gateway search endpoint propagates status=FAILURE response from execution service."""
    from services.gateway import main as gateway_main

    mock_creds = {"user": "testuser"}
    mock_failure_results = {
        "status": "FAILURE",
        "message": "Home Assistant Music Assistant service call failed",
        "results": []
    }

    with patch.object(gateway_main, '_resolve_identity_from_request', new=AsyncMock(return_value=mock_creds)):
        mock_response = _aio_resp(200, mock_failure_results)

        mock_get = AsyncMock(return_value=mock_response)

        with patch('services.gateway.main.aiohttp.ClientSession.get', mock_get):
            resp = client.get("/api/media/music-assistant/search?query=Miles+Davis")

            assert resp.status_code == 200
            assert resp.json() == mock_failure_results
