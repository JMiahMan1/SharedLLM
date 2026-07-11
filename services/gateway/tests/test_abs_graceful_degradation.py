"""Tests for ABS graceful degradation when server is unreachable."""
import os
import sys

os.environ["INTERNAL_SECRET"] = "test-secret"

from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest
from fastapi.testclient import TestClient


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

    from services.gateway import main
    from services.gateway.main import app
    main.background_tasks = None  # pyright: ignore[reportAttributeAccessIssue]

    return TestClient(app)


class MockAioResponse:
    def __init__(self, status=200, json_data=None):
        self.status = status
        self._json_data = json_data or {"status": "SUCCESS"}

    async def json(self):
        return self._json_data

    async def text(self):
        return ""


# ─── ABS Graceful Degradation ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_abs_libraries_timeout_returns_empty(client):
    """ABS libraries endpoint returns empty list (not 500) on timeout."""
    from services.gateway import main as gateway_main

    mock_creds = {"user": "testuser"}

    with patch.object(gateway_main, '_resolve_identity_from_request', new=AsyncMock(return_value=mock_creds)), patch('aiohttp.ClientSession') as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(side_effect=aiohttp.ClientConnectionError("timeout"))
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=None)

            resp = client.get("/api/media/audiobookshelf/libraries")

            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "SUCCESS"
            assert data["libraries"] == []
            assert data.get("notice") == "ABS unavailable"


@pytest.mark.asyncio
async def test_abs_last_played_timeout_returns_empty(client):
    """ABS last-played endpoint returns empty list (not 500) on timeout."""
    from services.gateway import main as gateway_main

    mock_creds = {"user": "testuser"}

    with patch.object(gateway_main, '_resolve_identity_from_request', new=AsyncMock(return_value=mock_creds)), patch('aiohttp.ClientSession') as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(side_effect=aiohttp.ClientConnectionError("timeout"))
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=None)

            resp = client.get("/api/media/audiobookshelf/last-played")

            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "SUCCESS"
            assert data["books"] == []
            assert data.get("notice") == "ABS unavailable"


@pytest.mark.asyncio
async def test_abs_search_timeout_returns_empty(client):
    """ABS search endpoint returns empty list (not 500) on timeout."""
    from services.gateway import main as gateway_main

    mock_creds = {"user": "testuser"}

    with patch.object(gateway_main, '_resolve_identity_from_request', new=AsyncMock(return_value=mock_creds)), patch('aiohttp.ClientSession') as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(side_effect=aiohttp.ClientConnectionError("timeout"))
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=None)

            resp = client.get("/api/media/audiobookshelf/search?q=test")

            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "SUCCESS"
            assert data["books"] == []
            assert data.get("notice") == "ABS unavailable"


@pytest.mark.asyncio
async def test_abs_library_items_timeout_returns_empty(client):
    """ABS library items endpoint returns empty list (not 500) on timeout."""
    from services.gateway import main as gateway_main

    mock_creds = {"user": "testuser"}

    with patch.object(gateway_main, '_resolve_identity_from_request', new=AsyncMock(return_value=mock_creds)), patch('aiohttp.ClientSession') as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(side_effect=aiohttp.ClientConnectionError("timeout"))
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=None)

            resp = client.get("/api/media/audiobookshelf/library/lib123")

            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "SUCCESS"
            assert data["books"] == []
            assert data.get("notice") == "ABS unavailable"


@pytest.mark.asyncio
async def test_abs_connectivity_status_unreachable(client):
    """ABS status endpoint reports UNREACHABLE when server times out."""
    from services import config as services_config

    # Mock identity settings
    mock_settings_resp = MagicMock()
    mock_settings_resp.status = 200
    mock_settings_resp.json = AsyncMock(return_value=[
        {"key": "audiobookshelf_url", "value": "https://abs.sumemail.com/"}
    ])

    with patch.object(services_config, 'IDENTITY_SVC_URL', 'http://identity:8001'), patch.object(services_config, 'INTERNAL_SECRET', 'test-secret'), patch('aiohttp.ClientSession') as mock_client_cls:
                mock_client = AsyncMock()
                mock_client.get = AsyncMock(side_effect=aiohttp.ClientConnectionError("timeout"))
                mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
                mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=None)

                resp = client.get("/api/media/audiobookshelf/status")

                assert resp.status_code == 200
                data = resp.json()
                assert data["status"] == "UNREACHABLE"
                assert data["reachable"] is False


@pytest.mark.asyncio
async def test_abs_libraries_success_with_data(client):
    """ABS libraries endpoint returns data when server responds."""
    from services.gateway import main as gateway_main

    mock_creds = {"user": "testuser"}
    mock_response_data = {
        "status": "SUCCESS",
        "detail": {
            "libraries": [
                {"id": "1", "name": "Audiobooks", "mediaType": "audio"}
            ]
        }
    }

    with patch.object(gateway_main, '_resolve_identity_from_request', new=AsyncMock(return_value=mock_creds)), patch('aiohttp.ClientSession') as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=MockAioResponse(json_data=mock_response_data))
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=None)

            resp = client.get("/api/media/audiobookshelf/libraries")

            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "SUCCESS"
            assert len(data["libraries"]) == 1
            assert data["libraries"][0]["name"] == "Audiobooks"
            assert "notice" not in data


@pytest.mark.asyncio
async def test_abs_libraries_normalizes_media_type(client):
    """ABS libraries endpoint normalizes type to media_type."""
    from services.gateway import main as gateway_main

    mock_creds = {"user": "testuser"}
    mock_response_data = {
        "status": "SUCCESS",
        "detail": {
            "libraries": [
                {"id": "1", "name": "Podcasts", "type": "podcast"}
            ]
        }
    }

    with patch.object(gateway_main, '_resolve_identity_from_request', new=AsyncMock(return_value=mock_creds)), patch('aiohttp.ClientSession') as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=MockAioResponse(json_data=mock_response_data))
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=None)

            resp = client.get("/api/media/audiobookshelf/libraries")

            assert resp.status_code == 200
            data = resp.json()
            assert data["libraries"][0]["media_type"] == "podcast"


@pytest.mark.asyncio
async def test_abs_libraries_identity_failure(client):
    """ABS libraries returns empty when identity resolution fails."""
    from fastapi import HTTPException

    from services.gateway import main as gateway_main

    with patch.object(gateway_main, '_resolve_identity_from_request', new=AsyncMock(side_effect=HTTPException(401, "unauthorized"))):
        resp = client.get("/api/media/audiobookshelf/libraries")

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "SUCCESS"
        assert data["libraries"] == []


@pytest.mark.asyncio
async def test_abs_last_played_with_data(client):
    """ABS last-played endpoint returns data when server responds."""
    from services.gateway import main as gateway_main

    mock_creds = {"user": "testuser"}
    mock_response_data = {
        "status": "SUCCESS",
        "detail": {
            "books": [
                {"id": "1", "title": "Test Book", "author": "Test Author"}
            ]
        }
    }

    with patch.object(gateway_main, '_resolve_identity_from_request', new=AsyncMock(return_value=mock_creds)), patch('aiohttp.ClientSession') as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=MockAioResponse(json_data=mock_response_data))
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=None)

            resp = client.get("/api/media/audiobookshelf/last-played")

            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "SUCCESS"
            assert len(data["books"]) == 1
            assert data["books"][0]["title"] == "Test Book"


@pytest.mark.asyncio
async def test_abs_search_with_data(client):
    """ABS search endpoint returns data when server responds."""
    from services.gateway import main as gateway_main

    mock_creds = {"user": "testuser"}
    mock_response_data = {
        "status": "SUCCESS",
        "detail": {
            "books": [
                {"id": "1", "title": "Matching Book", "author": "Author"}
            ]
        }
    }

    with patch.object(gateway_main, '_resolve_identity_from_request', new=AsyncMock(return_value=mock_creds)), patch('aiohttp.ClientSession') as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=MockAioResponse(json_data=mock_response_data))
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=None)

            resp = client.get("/api/media/audiobookshelf/search?q=matching")

            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "SUCCESS"
            assert len(data["books"]) == 1
            assert data["books"][0]["title"] == "Matching Book"


@pytest.mark.asyncio
async def test_abs_connectivity_status_available(client):
    """ABS status endpoint reports AVAILABLE when server responds."""
    from services import config as services_config

    mock_settings_resp = MagicMock()
    mock_settings_resp.status = 200
    mock_settings_resp.json = AsyncMock(return_value=[
        {"key": "audiobookshelf_url", "value": "https://abs.sumemail.com/"}
    ])

    with patch.object(services_config, 'IDENTITY_SVC_URL', 'http://identity:8001'), patch.object(services_config, 'INTERNAL_SECRET', 'test-secret'), patch('aiohttp.ClientSession') as mock_client_cls:
                mock_client = AsyncMock()
                # First call returns settings, second call pings ABS
                mock_client.get = AsyncMock(side_effect=[
                    mock_settings_resp,
                    MockAioResponse(status=200)
                ])
                mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
                mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=None)

                resp = client.get("/api/media/audiobookshelf/status")

                assert resp.status_code == 200
                data = resp.json()
                assert data["status"] == "AVAILABLE"
                assert data["reachable"] is True


@pytest.mark.asyncio
async def test_abs_connectivity_status_no_config(client):
    """ABS status endpoint reports error when ABS URL not configured."""
    from services import config as services_config

    mock_settings_resp = MagicMock()
    mock_settings_resp.status = 200
    mock_settings_resp.json = AsyncMock(return_value=[
        {"key": "other_setting", "value": "value"}
    ])

    with patch.object(services_config, 'IDENTITY_SVC_URL', 'http://identity:8001'), patch.object(services_config, 'INTERNAL_SECRET', 'test-secret'), patch('aiohttp.ClientSession') as mock_client_cls:
                mock_client = AsyncMock()
                mock_client.get = AsyncMock(return_value=mock_settings_resp)
                mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
                mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=None)

                resp = client.get("/api/media/audiobookshelf/status")

                assert resp.status_code == 200
                data = resp.json()
                assert data["status"] == "UNAVAILABLE"
                assert data["reachable"] is False


@pytest.mark.asyncio
async def test_abs_library_items_with_data(client):
    """ABS library items endpoint returns data when server responds."""
    from services.gateway import main as gateway_main

    mock_creds = {"user": "testuser"}
    mock_response_data = {
        "status": "SUCCESS",
        "detail": {
            "books": [
                {"id": "1", "title": "Book 1", "author": "Author 1"},
                {"id": "2", "title": "Book 2", "author": "Author 2"}
            ]
        }
    }

    with patch.object(gateway_main, '_resolve_identity_from_request', new=AsyncMock(return_value=mock_creds)), patch('aiohttp.ClientSession') as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=MockAioResponse(json_data=mock_response_data))
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=None)

            resp = client.get("/api/media/audiobookshelf/library/lib123")

            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "SUCCESS"
            assert len(data["books"]) == 2
