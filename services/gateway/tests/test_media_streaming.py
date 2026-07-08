"""Tests for media streaming endpoints (stream_audiobookshelf, stream_music_assistant).

These tests specifically verify that the streaming endpoints correctly access
credential fields from the dict returned by _resolve_identity_from_request and
that the aiohttp streaming API (client.get / resp.content.iter_chunked /
resp.release) is used correctly after the httpx -> aiohttp migration.
"""
import os
import sys
from contextlib import asynccontextmanager

os.environ["INTERNAL_SECRET"] = "test-secret"

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient


class MockAioResp:
    """Minimal aiohttp.ClientResponse mock for streaming assertions."""

    def __init__(self, status=200, headers=None, chunks=None, text="", json_data=None):
        self.status = status
        self.headers = headers or {}
        self._chunks = chunks or []
        self._text = text
        self._json = json_data

    async def release(self):
        pass

    async def close(self):
        pass

    async def read(self):
        return b"".join(self._chunks)

    async def text(self):
        return self._text

    async def json(self):
        return self._json if self._json is not None else {}

    @property
    def content(self):
        # Streaming reads r.content.iter_chunked(...) — return self which provides it.
        return self

    async def iter_chunked(self, n):
        for chunk in self._chunks:
            yield chunk


class MockAioSession:
    """aiohttp.ClientSession mock supporting both `async with` and direct .get/.post.

    Configure per-test via constructor kwargs:
      - login_resp: response returned for POST .../login
      - players_resp: response returned for POST .../api (MA players/all)
      - stream_resp: response returned for GET of the stream/flow URL
    """

    def __init__(self, *, login_resp=None, players_resp=None, stream_resp=None, **kwargs):
        self._login_resp = login_resp
        self._players_resp = players_resp
        self._stream_resp = stream_resp

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def close(self):
        pass

    async def post(self, url, **kwargs):
        url = str(url)
        if self._login_resp is not None and "login" in url:
            return self._login_resp
        return self._players_resp

    async def get(self, url, **kwargs):
        return self._stream_resp


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

    @asynccontextmanager
    async def noop_lifespan(_app):
        yield

    monkeypatch.setattr(app.router, "lifespan_context", noop_lifespan)
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

    stream_resp = MockAioResp(
        status=200,
        headers={"content-type": "audio/mpeg", "Content-Length": "10"},
        chunks=[b"test", b"stream"],
    )
    session = MockAioSession(stream_resp=stream_resp)

    with patch.object(gateway_main, '_resolve_identity_from_request', new=AsyncMock(return_value=mock_creds)):
        with patch('services.gateway.main.aiohttp.ClientSession', return_value=session):
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

    players_resp = MockAioResp(
        status=200,
        json_data={"result": {"players": []}},
    )
    session = MockAioSession(players_resp=players_resp)

    with patch.object(gateway_main, '_resolve_identity_from_request', new=AsyncMock(return_value=mock_creds)):
        with patch('services.gateway.main.aiohttp.ClientSession', return_value=session):
            resp = client.get("/api/media/stream/music-assistant?uri=https://www.youtube.com/watch?v=test123")

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
        resp = client.get("/api/media/stream/music-assistant?uri=https://www.youtube.com/watch?v=test")

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

    players_resp = MockAioResp(
        status=200,
        json_data={"result": {"players": []}},
    )
    session = MockAioSession(players_resp=players_resp)

    with patch.object(gateway_main, '_resolve_identity_from_request', new=AsyncMock(return_value=mock_creds)):
        with patch('services.gateway.main.aiohttp.ClientSession', return_value=session):
            resp = client.get("/api/media/stream/music-assistant?uri=https://www.youtube.com/watch?v=test123")

            assert resp.status_code == 404
            assert "No Music Assistant players" in resp.json().get("detail", "")


@pytest.mark.asyncio
async def test_stream_abs_identity_failure_returns_401(monkeypatch, client):
    """Test that ABS streaming returns 401 when identity resolution fails."""
    from fastapi import HTTPException

    from services.gateway import main as gateway_main

    with patch.object(gateway_main, '_resolve_identity_from_request', new=AsyncMock(
        side_effect=HTTPException(status_code=401, detail="Unauthorized")
    )):
        resp = client.get("/api/media/stream/audiobookshelf/book-123")

        assert resp.status_code == 401
        assert "Authentication" in resp.json().get("detail", "")


@pytest.mark.asyncio
async def test_stream_ma_identity_failure_returns_401(monkeypatch, client):
    """Test that MA streaming returns 401 when identity resolution fails."""
    from fastapi import HTTPException

    from services.gateway import main as gateway_main

    with patch.object(gateway_main, '_resolve_identity_from_request', new=AsyncMock(
        side_effect=HTTPException(status_code=401, detail="Unauthorized")
    )):
        resp = client.get("/api/media/stream/music-assistant?uri=https://www.youtube.com/watch?v=test")

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

    login_resp = MockAioResp(status=200, json_data={"user": {"token": "new-token"}})
    stream_resp = MockAioResp(
        status=200,
        headers={"content-type": "audio/mpeg", "Content-Length": "15"},
        chunks=[b"streaming", b" audio"],
    )
    session = MockAioSession(login_resp=login_resp, stream_resp=stream_resp)

    with patch.object(gateway_main, '_resolve_identity_from_request', new=AsyncMock(return_value=mock_creds)):
        with patch('services.gateway.main.aiohttp.ClientSession', return_value=session):
            resp = client.get("/api/media/stream/audiobookshelf/book-456")

            # Should get 200 (streaming), not 500 (crash from dot notation)
            assert resp.status_code != 500, (
                f"stream_audiobookshelf crashed with {resp.status_code} - "
                "credential field access is broken"
            )


@pytest.mark.asyncio
async def test_stream_ma_targets_browser_player_without_muting(monkeypatch, client):
    """Verify stream_music_assistant targets the browser player and does not mute/pause it."""
    from services.gateway import main as gateway_main

    mock_creds = {
        "user": "testuser",
        "mass_url": "http://ma.local:8095",
        "mass_token": "test-mass-token",
    }

    # Track all commands sent to MA WebSocket
    sent_commands: list[tuple[str, dict]] = []

    async def mock_send_command_no_wait(command, args=None):
        sent_commands.append((command, args or {}))

    async def mock_send_command(command, args=None, timeout=10.0):
        sent_commands.append((command, args or {}))
        if command == "player_queues/play_media":
            return {"success": True}
        return None

    # Mock MAWebSocketClient
    mock_ma_client = AsyncMock()
    mock_ma_client.connect = AsyncMock()
    mock_ma_client.disconnect = AsyncMock()
    mock_ma_client.connected = True
    mock_ma_client.send_command = AsyncMock(side_effect=mock_send_command)
    mock_ma_client.send_command_no_wait = AsyncMock(side_effect=mock_send_command_no_wait)
    mock_ma_client.get_ma_error = MagicMock(return_value=None)
    mock_ma_client.get_stream_url = MagicMock(return_value=None)
    mock_ma_client.get_queue_state = MagicMock(return_value={
        "current_item": {"queue_item_id": "item-123"},
        "queue_id": "browser-player",
        "player_id": "browser-player",
    })
    mock_ma_client.get_queue_state_description = MagicMock(return_value="playing")

    # Mock players/all response
    players_resp = MockAioResp(
        status=200,
        json_data=[
            {"player_id": "office-tv", "name": "Office TV"},
            {"player_id": "browser-player", "name": "Sendspin JS Client (test)"},
        ],
    )

    # Mock /flow/ stream response
    flow_resp = MockAioResp(
        status=200,
        headers={"content-type": "audio/mpeg", "Content-Length": "11"},
        chunks=[b"audio_bytes"],
    )

    session = MockAioSession(players_resp=players_resp, stream_resp=flow_resp)

    with patch.object(gateway_main, '_resolve_identity_from_request', new=AsyncMock(return_value=mock_creds)):
        with patch('services.gateway.main.aiohttp.ClientSession', return_value=session):
            with patch('services.gateway.main.MAWebSocketClient', return_value=mock_ma_client):
                resp = client.get("/api/media/stream/music-assistant?uri=library://track/123")

                # Verify the response is successful (streaming audio)
                assert resp.status_code == 200, (
                    f"Expected 200 for streaming, got {resp.status_code}: {resp.text}"
                )

                # Extract command names from sent commands
                command_names = [cmd[0] for cmd in sent_commands]

                # Verify the browser queue was targeted directly and no physical-device
                # mute/pause calls were emitted.
                assert "player_queues/play_media" in command_names, (
                    f"player_queues/play_media not sent! Commands: {command_names}"
                )
                assert "players/mute" not in command_names, f"Unexpected mute command: {command_names}"
                assert "player_queues/pause" not in command_names, f"Unexpected pause command: {command_names}"
                assert sent_commands[0][1].get("queue_id") == "browser-player"
