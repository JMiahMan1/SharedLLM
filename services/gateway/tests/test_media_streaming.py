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

    with patch.object(gateway_main, '_resolve_identity_from_request', new=AsyncMock(return_value=mock_creds)):
        with patch('services.gateway.main.httpx.AsyncClient') as mock_httpx:
            mock_httpx_instance = AsyncMock()
            mock_httpx_instance.post = AsyncMock(return_value=MagicMock(
                status_code=200,
                json=lambda: {"result": {"players": []}}
            ))
            mock_httpx.AsyncClient.return_value = mock_httpx_instance

            resp = client.get("/api/media/stream/music-assistant?uri=https://www.youtube.com/watch?v=test123")

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


@pytest.mark.asyncio
async def test_stream_ma_mutes_and_pauses_player_for_browser_only(monkeypatch, client):
    """Verify stream_music_assistant mutes, plays, pauses, and unmutes the MA player.

    The Web Player must play audio through the BROWSER, not the physical MA device.
    This test verifies that:
    1. The player is MUTED before play_media (no audio leaks to physical device)
    2. play_media is called to populate the queue (needed for stream URL)
    3. The player is PAUSED after stream URL is resolved
    4. The player is UNMUTED after pause (restores original state)
    """
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
    mock_ma_client.get_queue_state = MagicMock(return_value={
        "current_item": {"queue_item_id": "item-123"},
        "queue_id": "player-1",
        "player_id": "player-1",
    })
    mock_ma_client.get_queue_state_description = MagicMock(return_value="playing")

    # Mock players/all response
    mock_players_resp = MagicMock()
    mock_players_resp.status_code = 200
    mock_players_resp.json.return_value = [{"player_id": "player-1"}]

    # Mock player_queues/all response
    mock_queues_resp = MagicMock()
    mock_queues_resp.status_code = 200
    mock_queues_resp.json.return_value = [{"queue_id": "player-1", "state": "idle"}]

    # Mock /flow/ stream response
    async def async_byte_iterator():
        yield b"audio_bytes"

    mock_flow_resp = MagicMock()
    mock_flow_resp.status_code = 200
    mock_flow_resp.headers = {"content-type": "audio/mpeg", "Content-Length": "11"}
    mock_flow_resp.aiter_bytes = MagicMock(return_value=async_byte_iterator())
    mock_flow_resp.aclose = AsyncMock()

    # Mock proxy client for the /flow/ stream
    mock_proxy_client = AsyncMock()
    mock_proxy_client.send = AsyncMock(return_value=mock_flow_resp)
    mock_proxy_client.aclose = AsyncMock()
    mock_proxy_client.build_request = MagicMock(return_value=MagicMock())

    # All httpx.AsyncClient instances use a unified mock
    unified_httpx = AsyncMock()
    unified_httpx.post = AsyncMock(side_effect=[mock_players_resp, mock_queues_resp])
    unified_httpx.send = AsyncMock(return_value=mock_flow_resp)
    unified_httpx.aclose = AsyncMock()
    unified_httpx.build_request = MagicMock(return_value=MagicMock())

    class MockAsyncClient:
        async def __aenter__(self):
            return unified_httpx

        async def __aexit__(self, *args):
            return None

        async def aclose(self):
            await unified_httpx.aclose()

    with patch.object(gateway_main, '_resolve_identity_from_request', new=AsyncMock(return_value=mock_creds)):
        with patch('services.gateway.main.httpx.AsyncClient', MockAsyncClient):
            with patch('services.gateway.main.MAWebSocketClient', return_value=mock_ma_client):
                resp = client.get("/api/media/stream/music-assistant?uri=library://track/123")

                # Verify the response is successful (streaming audio)
                assert resp.status_code == 200, (
                    f"Expected 200 for streaming, got {resp.status_code}: {resp.text}"
                )

                # Extract command names from sent commands
                command_names = [cmd[0] for cmd in sent_commands]

                # Verify the mute→play→pause→unmute sequence
                assert "players/mute" in command_names, (
                    f"players/mute not sent! Commands: {command_names}"
                )
                assert "player_queues/play_media" in command_names, (
                    f"player_queues/play_media not sent! Commands: {command_names}"
                )
                assert "player_queues/pause" in command_names, (
                    f"player_queues/pause not sent! Commands: {command_names}"
                )

                # Verify mute was sent BEFORE play_media
                mute_idx = command_names.index("players/mute")
                play_idx = command_names.index("player_queues/play_media")
                pause_idx = command_names.index("player_queues/pause")
                assert mute_idx < play_idx, (
                    f"Mute must come before play_media! Order: {command_names}"
                )
                assert play_idx < pause_idx, (
                    f"play_media must come before pause! Order: {command_names}"
                )

                # Verify there are at least 2 mute calls (mute before, unmute after)
                mute_calls = [cmd for cmd in sent_commands if cmd[0] == "players/mute"]
                assert len(mute_calls) >= 2, (
                    f"Expected at least 2 mute calls (mute + unmute), got {len(mute_calls)}: {mute_calls}"
                )

                # First mute should be muted=True, last should be muted=False
                assert mute_calls[0][1].get("muted") is True, (
                    f"First mute call should set muted=True, got: {mute_calls[0][1]}"
                )
                assert mute_calls[-1][1].get("muted") is False, (
                    f"Last mute call should set muted=False (unmute), got: {mute_calls[-1][1]}"
                )
