"""Tests for media detail and favorite endpoints."""
import os
import sys
os.environ["INTERNAL_SECRET"] = "test-secret"

import pytest
from fastapi.testclient import TestClient
from unittest.mock import MagicMock, AsyncMock, patch


@pytest.fixture(name="client")
def client_fixture(monkeypatch):
    """Setup gateway test client with mocked dependencies."""
    sys.modules["fastembed"] = MagicMock()
    mock_engine = MagicMock()
    mock_engine.engine = MagicMock()
    mock_engine.engine.classify.return_value = ("unknown", 0.0)
    mock_engine.engine.should_bypass_llm.return_value = False
    sys.modules["intent_engine"] = mock_engine
    sys.modules["background_worker"] = MagicMock()

    from services.gateway.main import app
    from services.gateway import main
    main.background_tasks = None  # pyright: ignore[reportAttributeAccessIssue]

    return TestClient(app)


@pytest.mark.asyncio
async def test_get_media_detail_success(client):
    """Verify that GET /api/media/detail queries music/item_by_uri on Music Assistant."""
    from services.gateway import main as gateway_main

    mock_creds = {
        "user": "testuser",
        "mass_url": "http://ma.local:8095",
        "mass_token": "test-mass-token",
    }

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"item_id": "123", "name": "Test Song", "favorite": True}

    async def post_side_effect(url, **kwargs):
        if "log" in url:
            log_resp = MagicMock()
            log_resp.status_code = 200
            log_resp.json.return_value = {}
            return log_resp
        return mock_response

    with patch.object(gateway_main, '_resolve_identity_from_request', new=AsyncMock(return_value=mock_creds)):
        mock_httpx_client = AsyncMock()
        mock_httpx_client.__aenter__.return_value = mock_httpx_client
        mock_httpx_client.post.side_effect = post_side_effect

        with patch('services.gateway.main.httpx.AsyncClient', return_value=mock_httpx_client):
            resp = client.get("/api/media/detail?uri=library://track/123")

            assert resp.status_code == 200
            assert resp.json() == {"item_id": "123", "name": "Test Song", "favorite": True}
            
            ma_calls = [
                c for c in mock_httpx_client.post.call_args_list
                if "log" not in c[0][0]
            ]
            assert len(ma_calls) == 1
            assert ma_calls[0][0][0] == "http://ma.local:8095/api"
            assert ma_calls[0][1]["json"] == {"command": "music/item_by_uri", "args": {"uri": "library://track/123"}}
            assert ma_calls[0][1]["headers"] == {"Content-Type": "application/json", "Authorization": "Bearer test-mass-token"}


@pytest.mark.asyncio
async def test_toggle_favorite_add(client):
    """Verify that POST /api/media/favorite with favorite=True calls music/favorites/add_item."""
    from services.gateway import main as gateway_main

    mock_creds = {
        "user": "testuser",
        "mass_url": "http://ma.local:8095",
        "mass_token": "test-mass-token",
    }

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {}

    async def post_side_effect(url, **kwargs):
        if "log" in url:
            log_resp = MagicMock()
            log_resp.status_code = 200
            log_resp.json.return_value = {}
            return log_resp
        return mock_response

    with patch.object(gateway_main, '_resolve_identity_from_request', new=AsyncMock(return_value=mock_creds)):
        mock_httpx_client = AsyncMock()
        mock_httpx_client.__aenter__.return_value = mock_httpx_client
        mock_httpx_client.post.side_effect = post_side_effect

        with patch('services.gateway.main.httpx.AsyncClient', return_value=mock_httpx_client):
            resp = client.post("/api/media/favorite", json={"uri": "library://track/123", "favorite": True})

            assert resp.status_code == 200
            assert resp.json() == {"status": "SUCCESS", "favorite": True}
            
            ma_calls = [
                c for c in mock_httpx_client.post.call_args_list
                if "log" not in c[0][0]
            ]
            assert len(ma_calls) == 1
            assert ma_calls[0][0][0] == "http://ma.local:8095/api"
            assert ma_calls[0][1]["json"] == {"command": "music/favorites/add_item", "args": {"item": "library://track/123"}}
            assert ma_calls[0][1]["headers"] == {"Content-Type": "application/json", "Authorization": "Bearer test-mass-token"}


@pytest.mark.asyncio
async def test_toggle_favorite_remove(client):
    """Verify that POST /api/media/favorite with favorite=False calls music/item_by_uri then remove_item."""
    from services.gateway import main as gateway_main

    mock_creds = {
        "user": "testuser",
        "mass_url": "http://ma.local:8095",
        "mass_token": "test-mass-token",
    }

    # First call: music/item_by_uri, Second call: music/favorites/remove_item
    mock_resolve_resp = MagicMock()
    mock_resolve_resp.status_code = 200
    mock_resolve_resp.json.return_value = {"item_id": "123", "media_type": "track"}

    mock_remove_resp = MagicMock()
    mock_remove_resp.status_code = 200
    mock_remove_resp.json.return_value = {}

    ma_responses = [mock_resolve_resp, mock_remove_resp]
    ma_response_iter = iter(ma_responses)

    async def post_side_effect(url, **kwargs):
        if "log" in url:
            log_resp = MagicMock()
            log_resp.status_code = 200
            log_resp.json.return_value = {}
            return log_resp
        return next(ma_response_iter)

    with patch.object(gateway_main, '_resolve_identity_from_request', new=AsyncMock(return_value=mock_creds)):
        mock_httpx_client = AsyncMock()
        mock_httpx_client.__aenter__.return_value = mock_httpx_client
        mock_httpx_client.post.side_effect = post_side_effect

        with patch('services.gateway.main.httpx.AsyncClient', return_value=mock_httpx_client):
            resp = client.post("/api/media/favorite", json={"uri": "library://track/123", "favorite": False})

            assert resp.status_code == 200
            assert resp.json() == {"status": "SUCCESS", "favorite": False}
            
            ma_calls = [
                c for c in mock_httpx_client.post.call_args_list
                if "log" not in c[0][0]
            ]
            assert len(ma_calls) == 2
            assert ma_calls[0][0][0] == "http://ma.local:8095/api"
            assert ma_calls[0][1]["json"] == {"command": "music/item_by_uri", "args": {"uri": "library://track/123"}}
            assert ma_calls[0][1]["headers"] == {"Content-Type": "application/json", "Authorization": "Bearer test-mass-token"}

            assert ma_calls[1][0][0] == "http://ma.local:8095/api"
            assert ma_calls[1][1]["json"] == {
                "command": "music/favorites/remove_item",
                "args": {"library_item_id": "123", "media_type": "track"}
            }
            assert ma_calls[1][1]["headers"] == {"Content-Type": "application/json", "Authorization": "Bearer test-mass-token"}


@pytest.mark.asyncio
async def test_toggle_favorite_non_ma_uri_skipped(client):
    """Verify that POST /api/media/favorite skips non-MA URIs gracefully."""
    resp = client.post("/api/media/favorite", json={"uri": "book-123", "favorite": True})
    assert resp.status_code == 200
    assert resp.json() == {"status": "SKIPPED", "favorite": True, "reason": "Not a Music Assistant URI"}
