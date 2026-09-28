"""BUG-20: the execution proxy must not retry non-idempotent commands.

A transient ``aiohttp.ClientError`` can surface AFTER the execution service
already performed the command (e.g. while reading the response), so retrying
a non-idempotent command like ``next`` would run it twice on the player.
Only known-idempotent operations may go through the retry path.

Note: the gateway's request logging middleware (``emit_log``) also posts
through the same HTTP client, so the doubles below count only calls whose
URL targets the execution service.
"""
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest
from fastapi.testclient import TestClient

os.environ["INTERNAL_SECRET"] = "test-secret"


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


_MOCK_CREDS = {"user": "testuser", "ha_url": "http://ha.local", "ha_token": "secret"}


class ExecPost:
    """POST double that only counts/trips execution-service calls."""

    def __init__(self, fail_first=False):
        self.fail_first = fail_first
        self.count = 0

    async def __call__(self, url, *args, **kwargs):
        if "/execute/" not in str(url):
            # Log emission or other side traffic — irrelevant to the assertions.
            return MockAioResponse(json_data={"status": "SUCCESS"})
        self.count += 1
        if self.fail_first and self.count == 1:
            raise aiohttp.ClientError("connection reset")
        return MockAioResponse()


def _patched(post):
    """Patch identity resolution + the gateway HTTP client."""
    mock_session = MagicMock()
    mock_session.post = post
    from services.gateway import main as gateway_main
    return (
        patch.object(gateway_main, "_resolve_identity_from_request", new=AsyncMock(return_value=_MOCK_CREDS)),
        patch.object(gateway_main, "get_http_client", return_value=mock_session),
    )


@pytest.mark.asyncio
async def test_transport_next_not_retried_on_client_error(client):
    """BUG-20 acceptance: `next` with a failing first attempt is called once."""
    post = ExecPost(fail_first=True)
    p1, p2 = _patched(post)

    with p1, p2:
        resp = client.post(
            "/execute/media/transport",
            json={"entity_id": "media_player.tv", "command": "next"},
        )

    assert resp.status_code == 503
    assert post.count == 1


@pytest.mark.asyncio
async def test_transport_play_not_retried_on_client_error(client):
    """`play` is non-idempotent too: one attempt only."""
    post = ExecPost(fail_first=True)
    p1, p2 = _patched(post)

    with p1, p2:
        resp = client.post(
            "/execute/media/transport",
            json={"entity_id": "media_player.tv", "command": "play"},
        )

    assert resp.status_code == 503
    assert post.count == 1


@pytest.mark.asyncio
async def test_media_play_endpoint_not_retried_on_client_error(client):
    """POST /execute/media/play starts playback: one attempt only."""
    post = ExecPost(fail_first=True)
    p1, p2 = _patched(post)

    with p1, p2:
        resp = client.post(
            "/execute/media/play",
            json={"entity_id": "media_player.tv", "media_type": "music", "query": "test"},
        )

    assert resp.status_code == 503
    assert post.count == 1


@pytest.mark.asyncio
async def test_transport_pause_is_retried_on_client_error(client):
    """`pause` is idempotent: the transient failure is retried and recovers."""
    post = ExecPost(fail_first=True)
    p1, p2 = _patched(post)

    with p1, p2:
        resp = client.post(
            "/execute/media/transport",
            json={"entity_id": "media_player.tv", "command": "pause"},
        )

    assert resp.status_code == 200
    assert post.count == 2


@pytest.mark.asyncio
async def test_media_status_is_retried_on_client_error(client):
    """The status endpoint is a read: the transient failure is retried."""
    post = ExecPost(fail_first=True)
    p1, p2 = _patched(post)

    with p1, p2:
        resp = client.post("/execute/media/status", json={})

    assert resp.status_code == 200
    assert post.count == 2
