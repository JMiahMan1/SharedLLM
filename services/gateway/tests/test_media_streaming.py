"""Tests for media streaming endpoints (stream_audiobookshelf, stream_music_assistant).

These tests specifically verify that the streaming endpoints correctly access
credential fields from the dict returned by _resolve_identity_from_request.
"""
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
async def test_stream_abs_uses_dict_get_not_dot_notation(monkeypatch, client):
    """Verify stream_audiobookshelf correctly accesses credential fields from dict.

    Regression test: _resolve_identity_from_request returns a dict, not a Pydantic model.
    Using dot notation (creds.audiobookshelf_url) would raise AttributeError.
    Must use .get() for dict access.
    """
    from services.gateway import main as gateway_main

    mock_creds = {
        "user": "testuser",
        "audiobookshelf_url": "http://abs.local",
        "audiobookshelf_api_key": "test-api-key",
    }

    async def async_byte_iterator():
        for chunk in [b"test", b"stream"]:
            yield chunk

    with patch.object(gateway_main, '_resolve_identity_from_request', new=AsyncMock(return_value=mock_creds)):
        # Mock the httpx client and its send method
        mock_httpx_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {"content-type": "audio/mpeg", "Content-Length": "100"}
        mock_response.aiter_bytes = MagicMock(return_value=async_byte_iterator())
        mock_response.aclose = AsyncMock()
        mock_httpx_client.send = AsyncMock(return_value=mock_response)
        mock_httpx_client.aclose = AsyncMock()
        mock_httpx_client.build_request = MagicMock(return_value=MagicMock())

        with patch('services.gateway.main.httpx.AsyncClient', return_value=mock_httpx_client):
            resp = client.get("/api/media/stream/audiobookshelf/book-123")

            # If dot notation was used, this would 500 with AttributeError
            # We expect 200 (streaming) or at least no 500 from credential access
            assert resp.status_code != 500, (
                f"stream_audiobookshelf failed with {resp.status_code}. "
                "This likely means dot notation was used on a dict credential object."
            )


@pytest.mark.asyncio
async def test_stream_ma_uses_dict_get_not_dot_notation(monkeypatch, client):
    """Verify stream_music_assistant correctly accesses credential fields from dict.

    Regression test: same issue as the ABS stream endpoint.
    """
    from services.gateway import main as gateway_main

    mock_creds = {
        "user": "testuser",
        "mass_url": "http://ma.local:8095",
        "mass_token": "test-mass-token",
    }

    with patch.object(gateway_main, '_resolve_identity_from_request', new=AsyncMock(return_value=mock_creds)):
        # Mock httpx.AsyncClient (used for player/list and player/status calls)
        mock_httpx_client = AsyncMock()
        mock_httpx_client.post = AsyncMock(return_value=MagicMock(
            status_code=200,
            json=lambda: {"result": {"players": []}}
        ))

        with patch('services.gateway.main.httpx.AsyncClient', return_value=mock_httpx_client):
            resp = client.get("/api/media/stream/music-assistant?uri=spotify:track:test123")

            # If dot notation was used, this would 500 with AttributeError
            # We expect 404 (no players) or some other error, but not 500 from credential access
            assert resp.status_code != 500, (
                f"stream_music_assistant failed with {resp.status_code}. "
                "This likely means dot notation was used on a dict credential object."
            )


@pytest.mark.asyncio
async def test_stream_abs_missing_credentials_returns_400(monkeypatch, client):
    """Test that ABS streaming returns 400 when credentials are missing (not 500)."""
    from services.gateway import main as gateway_main

    mock_creds = {
        "user": "testuser",
        # No audiobookshelf_url, no api_key
    }

    with patch.object(gateway_main, '_resolve_identity_from_request', new=AsyncMock(return_value=mock_creds)):
        resp = client.get("/api/media/stream/audiobookshelf/book-123")

        # Should return 400 for missing credentials, not crash
        assert resp.status_code == 400
        assert "not configured" in resp.json().get("detail", "")


@pytest.mark.asyncio
async def test_stream_ma_missing_credentials_returns_400(monkeypatch, client):
    """Test that MA streaming returns 400 when mass_url is missing."""
    from services.gateway import main as gateway_main

    mock_creds = {
        "user": "testuser",
        # No mass_url
    }

    with patch.object(gateway_main, '_resolve_identity_from_request', new=AsyncMock(return_value=mock_creds)):
        resp = client.get("/api/media/stream/music-assistant?uri=spotify:track:test")

        assert resp.status_code == 400
        assert "not configured" in resp.json().get("detail", "")


@pytest.mark.asyncio
async def test_stream_ma_no_players_returns_404(monkeypatch, client):
    """Test that MA streaming returns 404 when no players are available."""
    from services.gateway import main as gateway_main

    mock_creds = {
        "user": "testuser",
        "mass_url": "http://ma.local:8095",
        "mass_token": "test-token",
    }

    with patch.object(gateway_main, '_resolve_identity_from_request', new=AsyncMock(return_value=mock_creds)):
        with patch('services.gateway.main.httpx.AsyncClient') as mock_httpx:
            mock_httpx_instance = AsyncMock()
            mock_httpx_instance.post = AsyncMock(return_value=MagicMock(
                status_code=200,
                json=lambda: {"result": {"players": []}}
            ))
            mock_httpx.AsyncClient.return_value = mock_httpx_instance

            resp = client.get("/api/media/stream/music-assistant?uri=spotify:track:test123")

            assert resp.status_code == 404
            assert "No Music Assistant players" in resp.json().get("detail", "")


@pytest.mark.asyncio
async def test_stream_abs_identity_failure_returns_401(monkeypatch, client):
    """Test that ABS streaming returns 401 when identity resolution fails."""
    from services.gateway import main as gateway_main
    from fastapi import HTTPException

    with patch.object(gateway_main, '_resolve_identity_from_request', new=AsyncMock(
        side_effect=HTTPException(status_code=401, detail="Unauthorized")
    )):
        resp = client.get("/api/media/stream/audiobookshelf/book-123")

        assert resp.status_code == 401
        assert "Authentication" in resp.json().get("detail", "")


@pytest.mark.asyncio
async def test_stream_ma_identity_failure_returns_401(monkeypatch, client):
    """Test that MA streaming returns 401 when identity resolution fails."""
    from services.gateway import main as gateway_main
    from fastapi import HTTPException

    with patch.object(gateway_main, '_resolve_identity_from_request', new=AsyncMock(
        side_effect=HTTPException(status_code=401, detail="Unauthorized")
    )):
        resp = client.get("/api/media/stream/music-assistant?uri=spotify:track:test")

        assert resp.status_code == 401
        assert "Authentication" in resp.json().get("detail", "")


@pytest.mark.asyncio
async def test_stream_abs_credential_fields_accessed_correctly(monkeypatch, client):
    """Verify all credential fields are accessed via .get() on the dict.

    This test ensures the streaming code doesn't crash when credentials
    use any of the expected field names: audiobookshelf_url, audiobookshelf_api_key,
    audiobookshelf_user, audiobookshelf_pass.
    """
    from services.gateway import main as gateway_main

    # Credential dict with ALL fields (including user/pass fallback)
    mock_creds = {
        "user": "testuser",
        "audiobookshelf_url": "http://abs.local",
        "audiobookshelf_api_key": "",  # Empty, will try username/password login
        "audiobookshelf_user": "absadmin",
        "audiobookshelf_pass": "secretpass",
    }

    async def async_stream_iterator():
        for chunk in [b"streaming", b" audio"]:
            yield chunk

    with patch.object(gateway_main, '_resolve_identity_from_request', new=AsyncMock(return_value=mock_creds)):
        with patch('services.gateway.main.httpx.AsyncClient') as mock_httpx:
            mock_httpx_instance = AsyncMock()

            # First call: ABS login, second call: stream
            mock_stream_resp = MagicMock()
            mock_stream_resp.status_code = 200
            mock_stream_resp.headers = {"content-type": "audio/mpeg", "Content-Length": "26"}
            mock_stream_resp.aiter_bytes = MagicMock(return_value=async_stream_iterator())
            mock_stream_resp.aclose = AsyncMock()

            mock_httpx_instance.send = AsyncMock(side_effect=[
                MagicMock(status_code=200, json=lambda: {"user": {"token": "new-token"}}),
                mock_stream_resp,
            ])
            mock_httpx_instance.aclose = AsyncMock()
            mock_httpx_instance.build_request = MagicMock(return_value=MagicMock())
            mock_httpx.AsyncClient.return_value = mock_httpx_instance

            resp = client.get("/api/media/stream/audiobookshelf/book-456")

            # Should get 200 (streaming), not 500 (crash from dot notation)
            assert resp.status_code != 500, (
                f"stream_audiobookshelf crashed with {resp.status_code} - "
                "credential field access is broken"
            )
