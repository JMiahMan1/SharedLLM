"""BUG-16 (P2-T7): /api/media/audiobookshelf/status — auth + per-user creds + real ping route.

The endpoint used to take no auth, read the GLOBAL identity settings list
(`abs_url` unbound when settings fetch != 200 → UnboundLocalError) and ping
`/api/books?limit=1`, which is not a real ABS route. Fix: require the caller's
identity, use their `audiobookshelf_url`, ping `GET /ping` (verified in ABS
server.js:385 — root router, pre-auth).
"""
from unittest.mock import AsyncMock, patch

import aiohttp
from fastapi import HTTPException

from services.gateway import main as gateway_main

ABS_URL = "http://abs.local:13378"


class _Resp:
    def __init__(self, status, data=None):
        self.status = status
        self._data = data if data is not None else {"success": True}

    async def json(self):
        return self._data


class _PingClient:
    """Yielded by shared_http_client; records requested URLs."""

    def __init__(self, status=200, error=None):
        self.status = status
        self.error = error
        self.urls = []

    async def get(self, url, **kwargs):
        self.urls.append(url)
        if self.error is not None:
            raise self.error
        return _Resp(self.status)


def _ctx(client):
    class _Ctx:
        async def __aenter__(self):
            return client

        async def __aexit__(self, *exc):
            return False

    return _Ctx()


def test_status_requires_auth(client):
    """Unauthenticated callers get 401 — the endpoint resolves identity first."""
    with patch.object(
        gateway_main,
        "_resolve_identity_from_request",
        new=AsyncMock(side_effect=HTTPException(status_code=401, detail="Authentication required")),
    ):
        resp = client.get("/api/media/audiobookshelf/status")

    assert resp.status_code == 401


def test_status_available_pings_user_creds_via_ping(client):
    """AVAILABLE comes from pinging the caller's ABS URL at GET /ping."""
    ping = _PingClient(status=200)
    with patch.object(gateway_main, "shared_http_client", new=lambda: _ctx(ping)):
        resp = client.get("/api/media/audiobookshelf/status")

    assert resp.status_code == 200
    data = resp.json()
    assert data == {"status": "AVAILABLE", "url": ABS_URL, "reachable": True}
    # Exactly one upstream request: the /ping liveness check, no settings read.
    assert ping.urls == [f"{ABS_URL}/ping"]


def test_status_unconfigured_returns_unavailable_without_ping(client):
    """No audiobookshelf_url in the caller's creds → UNAVAILABLE, nothing pinged."""
    ping = _PingClient(status=200)
    with patch.object(
        gateway_main,
        "_resolve_identity_from_request",
        new=AsyncMock(return_value={"user": "testuser"}),
    ), patch.object(gateway_main, "shared_http_client", new=lambda: _ctx(ping)):
        resp = client.get("/api/media/audiobookshelf/status")

    assert resp.status_code == 200
    assert resp.json() == {"status": "UNAVAILABLE", "error": "ABS URL not configured", "reachable": False}
    assert ping.urls == []


def test_status_ping_http_error_reports_code(client):
    ping = _PingClient(status=404)
    with patch.object(gateway_main, "shared_http_client", new=lambda: _ctx(ping)):
        resp = client.get("/api/media/audiobookshelf/status")

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ERROR"
    assert data["reachable"] is False
    assert data["code"] == 404


def test_status_connection_error_is_unreachable(client):
    ping = _PingClient(error=aiohttp.ClientConnectionError("boom"))
    with patch.object(gateway_main, "shared_http_client", new=lambda: _ctx(ping)):
        resp = client.get("/api/media/audiobookshelf/status")

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "UNREACHABLE"
    assert data["reachable"] is False
