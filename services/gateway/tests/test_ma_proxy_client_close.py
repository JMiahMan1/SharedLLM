"""BUG-22 / P2-T13: _ma_proxy_bytes must close its ClientSession whenever it
raises instead of returning a StreamingResponse.

Previously only the generic ``except Exception`` path closed ``proxy_client``;
an ``HTTPException`` (upstream 404/5xx readiness failure) leaked the session.
"""
import asyncio
import re

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from conftest_media import mock_upstream  # noqa: F401  (fixtures come via conftest


STREAM_URL = "http://audio.test:8082/stream.mp3"


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/media/stream/music-assistant",
            "headers": [],
            "query_string": b"",
        }
    )


def _track_sessions(monkeypatch):
    """Record close() on the proxy's pooled-session view.

    The proxy now borrows one pooled session; ``close`` is what releases the
    upstream response back to the pool, so a path that skips it still leaks.
    """
    from services.gateway import main

    created = []
    real_init = main._PooledMediaSession.__init__
    real_close = main._PooledMediaSession.close

    def init(self, *args, **kwargs):
        real_init(self, *args, **kwargs)
        self.close_calls = {"closed": 0}
        created.append(self)

    async def close(self):
        self.close_calls["closed"] += 1
        await real_close(self)

    monkeypatch.setattr(main._PooledMediaSession, "__init__", init)
    monkeypatch.setattr(main._PooledMediaSession, "close", close)
    return created


@pytest.mark.asyncio
async def test_http_exception_path_closes_proxy_client(monkeypatch, upstream):
    from services.gateway import main

    upstream.get(re.compile(re.escape(STREAM_URL)), status=500)
    created = _track_sessions(monkeypatch)

    with pytest.raises(HTTPException) as exc_info:
        await main._ma_proxy_bytes(_request(), STREAM_URL)

    assert exc_info.value.status_code == 502
    assert created, "proxy_client session was never created"
    assert created[0].close_calls["closed"] == 1, (
        f"proxy_client leaked: close() called {created[0].close_calls['closed']} times"
    )
    assert created[0]._responses == []  # the upstream response went back to the pool


@pytest.mark.asyncio
async def test_media_proxies_share_one_pooled_session(upstream):
    """A new track or HLS segment must not open a new connection pool."""
    from services.gateway import main

    upstream.get(re.compile(re.escape(STREAM_URL)), status=200, body=b"x", repeat=True)
    loop = asyncio.get_running_loop()
    first, second = main._PooledMediaSession(None), main._PooledMediaSession(None)
    await first.get(STREAM_URL)
    pooled = main._MEDIA_SESSIONS[loop]
    await second.get(STREAM_URL)
    await first.close()
    await second.close()
    assert main._MEDIA_SESSIONS[loop] is pooled and not pooled.closed
