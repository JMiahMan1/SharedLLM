"""P2-T10 / BUG-19: MA stream start (POST) is separate from bytes (GET).

Plan: every GET (incl. Range/seek) used to re-run
``player_queues/play_media(option=replace)``, restarting the queue on each
byte-range request. Fix: POST starts playback (and caches the resolved stream
URL), GET only ever fetches bytes — it never starts playback.

pytest: two Range GETs after a start -> ``play_media`` called 0 times.
"""
import os
import sys
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

os.environ["INTERNAL_SECRET"] = "test-secret"

import pytest
from fastapi.testclient import TestClient


class MockAioResp:
    """Minimal aiohttp.ClientResponse mock for streaming assertions."""

    def __init__(self, status=200, headers=None, chunks=None, json_data=None):
        self.status = status
        self.headers = headers or {}
        self._chunks = chunks or []
        self._json = json_data

    async def release(self):
        pass

    async def close(self):
        pass

    async def read(self):
        return b"".join(self._chunks)

    async def json(self):
        return self._json if self._json is not None else {}

    @property
    def content(self):
        return self

    async def iter_chunked(self, n):
        for chunk in self._chunks:
            yield chunk


class MockAioSession:
    """aiohttp.ClientSession mock: post -> players/all, get -> flow bytes."""

    def __init__(self, *, players_resp=None, stream_resp=None):
        self._players_resp = players_resp
        self._stream_resp = stream_resp

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def close(self):
        pass

    async def post(self, url, **kwargs):
        return self._players_resp

    async def get(self, url, **kwargs):
        return self._stream_resp


CREDS = {
    "user": "testuser",
    "mass_url": "http://ma.local:8095",
    "mass_token": "test-mass-token",
}

PLAYERS_RESP = MockAioResp(
    status=200,
    json_data=[
        {"player_id": "office-tv", "name": "Office TV"},
        {"player_id": "browser-player", "name": "Sendspin JS Client (test)"},
    ],
)

FLOW_RESP = MockAioResp(
    status=200,
    headers={"content-type": "audio/mpeg", "Content-Length": "11"},
    chunks=[b"audio_bytes"],
)

FLOW_URL = "http://ma.local:8095/flow/queue-1/item-1/browser-player.mp3"


def _make_ma_client(*, send_results=None, stream_url=None, queue_state=None):
    """Build a mock MAWebSocketClient.

    send_results: optional dict command -> result (player_queues/get etc.).
    """
    sent: list[tuple[str, dict]] = []

    async def send_command(command, args=None, timeout=10.0):
        sent.append((command, args or {}))
        if send_results and command in send_results:
            return send_results[command]
        return None

    ma = AsyncMock()
    ma.connect = AsyncMock()
    ma.disconnect = AsyncMock()
    ma.connected = True
    ma.send_command = AsyncMock(side_effect=send_command)
    ma.send_command_no_wait = AsyncMock()
    ma.get_ma_error = MagicMock(return_value=None)
    ma.get_stream_url = MagicMock(return_value=stream_url)
    ma.get_queue_state = MagicMock(return_value=queue_state or {})
    ma.get_queue_state_description = MagicMock(return_value="idle")
    ma.ingest_queue_state = MagicMock()
    ma._sent = sent
    return ma


@pytest.fixture(name="client")
def client_fixture(monkeypatch):
    """Gateway test client with lifespan/background stubs (test_media_streaming style)."""
    sys.modules.setdefault("fastembed", MagicMock())
    if "intent_engine" not in sys.modules:
        mock_engine = MagicMock()
        mock_engine.engine = MagicMock()
        mock_engine.engine.classify.return_value = ("unknown", 0.0)
        mock_engine.engine.should_bypass_llm.return_value = False
        sys.modules["intent_engine"] = mock_engine
    sys.modules.setdefault("background_worker", MagicMock())

    from services.gateway import main
    from services.gateway.main import app

    @asynccontextmanager
    async def noop_lifespan(_app):
        yield

    monkeypatch.setattr(app.router, "lifespan_context", noop_lifespan)
    main.background_tasks = None  # pyright: ignore[reportAttributeAccessIssue]
    # Isolate the stream-session cache between tests.
    main._MA_STREAM_CACHE.clear()
    return TestClient(app)


def _patch_stack(gateway_main, ma_client, session):
    return (
        patch.object(
            gateway_main,
            "_resolve_identity_from_request",
            new=AsyncMock(return_value=dict(CREDS)),
        ),
        patch("services.gateway.main.aiohttp.ClientSession", return_value=session),
        patch("services.gateway.main.MAWebSocketClient", return_value=ma_client),
        # Discovery goes through shared_http_client() -> get_http_client();
        # the real one caches per-loop sessions (real HTTP). Route it to the mock.
        patch.object(gateway_main, "get_http_client", return_value=session),
    )


def test_two_range_gets_after_start_send_no_play_media(client):
    """BUG-19 contract: start via POST; Range GETs never re-run play_media."""
    from services.gateway import main as gateway_main

    ma = _make_ma_client(stream_url=FLOW_URL)
    session = MockAioSession(players_resp=PLAYERS_RESP, stream_resp=FLOW_RESP)

    p1, p2, p3, p4 = _patch_stack(gateway_main, ma, session)
    with p1, p2, p3, p4:
        # 1. Start playback (POST) — this is the only place play_media runs.
        start = client.post(
            "/api/media/stream/music-assistant?uri=library://track/123"
        )
        assert start.status_code == 200, start.text
        assert start.json()["status"] == "SUCCESS"
        play_media_calls = [
            c for c in ma._sent if c[0] == "player_queues/play_media"
        ]
        assert len(play_media_calls) == 1, ma._sent
        assert play_media_calls[0][1].get("queue_id") == "browser-player"

        # Cache-hit GETs must not even open a new MA WebSocket.
        gateway_main.MAWebSocketClient.reset_mock()
        ws_client_mock = gateway_main.MAWebSocketClient  # patched mock (valid inside `with`)

        # 2. Two Range GETs (seek/reconnect pattern) fetch bytes only.
        before = len(ma._sent)
        r1 = client.get(
            "/api/media/stream/music-assistant?uri=library://track/123",
            headers={"Range": "bytes=0-1023"},
        )
        r2 = client.get(
            "/api/media/stream/music-assistant?uri=library://track/123",
            headers={"Range": "bytes=1024-2047"},
        )

    assert r1.status_code == 200, r1.text
    assert r2.status_code == 200, r2.text
    assert r1.content == b"audio_bytes"
    # The BUG-19 acceptance test: zero play_media during byte fetches.
    new_commands = ma._sent[before:]
    play_during_gets = [c for c in new_commands if c[0] == "player_queues/play_media"]
    assert play_during_gets == [], f"GET restarted playback: {new_commands}"
    # No new MA WebSocket connections for cache-hit GETs.
    ws_client_mock.assert_not_called()


def test_get_without_cache_resolves_queue_without_play_media(client):
    """Cold GET resolves the stream from queue state — never starts playback."""
    from services.gateway import main as gateway_main

    queue_state = {
        "queue_id": "browser-player",
        "player_id": "browser-player",
        "current_item": {"queue_item_id": "qi-9", "stream_url": FLOW_URL},
    }
    ma = _make_ma_client(
        send_results={"player_queues/get": queue_state},
        stream_url=None,
        queue_state=queue_state,
    )
    session = MockAioSession(players_resp=PLAYERS_RESP, stream_resp=FLOW_RESP)

    p1, p2, p3, p4 = _patch_stack(gateway_main, ma, session)
    with p1, p2, p3, p4:
        resp = client.get(
            "/api/media/stream/music-assistant?uri=library://track/999",
            headers={"Range": "bytes=0-511"},
        )

    assert resp.status_code == 200, resp.text
    assert resp.content == b"audio_bytes"
    command_names = [c[0] for c in ma._sent]
    assert "player_queues/play_media" not in command_names, command_names
    assert "player_queues/get" in command_names, command_names
    # Queue state fetched over the wire was adopted before resolving bytes.
    ma.ingest_queue_state.assert_called_once_with(queue_state)


def test_get_without_resolvable_session_returns_409(client):
    """No started session and no queue state -> clear 409, never play_media."""
    from services.gateway import main as gateway_main

    ma = _make_ma_client(
        send_results={"player_queues/get": {}},
        stream_url=None,
        queue_state={},
    )
    session = MockAioSession(players_resp=PLAYERS_RESP, stream_resp=FLOW_RESP)

    p1, p2, p3, p4 = _patch_stack(gateway_main, ma, session)
    with p1, p2, p3, p4:
        resp = client.get(
            "/api/media/stream/music-assistant?uri=library://track/404"
        )

    assert resp.status_code == 409, resp.text
    detail = resp.json().get("detail", "")
    assert "start" in detail.lower(), detail
    assert not [c for c in ma._sent if c[0] == "player_queues/play_media"], ma._sent
