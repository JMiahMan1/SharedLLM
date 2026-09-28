"""BUG-22 / P2-T13: _ma_proxy_bytes must close its ClientSession whenever it
raises instead of returning a StreamingResponse.

Previously only the generic ``except Exception`` path closed ``proxy_client``;
an ``HTTPException`` (upstream 404/5xx readiness failure) leaked the session.
"""
import re

import aiohttp
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
    """Replace aiohttp.ClientSession with a factory that records close() calls."""
    real_cls = aiohttp.ClientSession
    created = []

    def factory(*args, **kwargs):
        session = real_cls(*args, **kwargs)
        original_close = session.close
        state = {"closed": 0}

        async def close():
            state["closed"] += 1
            await original_close()

        session.close = close
        session.close_calls = state
        created.append(session)
        return session

    monkeypatch.setattr(aiohttp, "ClientSession", factory)
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
