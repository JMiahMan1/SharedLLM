from unittest.mock import AsyncMock

import pytest
from starlette.requests import Request

from services.gateway import main as gateway_main


def _request() -> Request:
    return Request({
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": [],
        "query_string": b"",
    })


@pytest.mark.asyncio
async def test_anonymous_identity_defaults_to_non_admin(monkeypatch):
    seen = {}

    async def fake_resolve(payload):
        seen.update(payload)
        return {"user": "anonymous", "is_admin": False}

    monkeypatch.setattr(gateway_main, "resolve_identity", fake_resolve)

    result = await gateway_main._resolve_identity_allow_anonymous(_request())

    assert seen["anonymous"] is True
    assert result["is_admin"] is False


@pytest.mark.asyncio
async def test_internal_system_identity_can_use_admin_fallback(monkeypatch):
    seen = {}

    async def fake_resolve(payload):
        seen.update(payload)
        return {"user": "default", "is_admin": True}

    monkeypatch.setattr(gateway_main, "resolve_identity", fake_resolve)

    result = await gateway_main._resolve_identity_allow_anonymous(
        _request(), allow_system_fallback=True
    )

    assert seen["anonymous"] is False
    assert result["is_admin"] is True
