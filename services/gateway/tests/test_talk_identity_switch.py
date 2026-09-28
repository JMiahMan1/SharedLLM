import sys
from unittest.mock import MagicMock

import pytest


# Mock dependencies before importing main (same preamble as test_calendar_proxy).
mock_redis = MagicMock()
sys.modules["redis"] = mock_redis
sys.modules["redis.asyncio"] = mock_redis
sys.modules["fastembed"] = MagicMock()
sys.modules["intent_engine"] = MagicMock()
sys.modules["background_worker"] = MagicMock()

ADMIN_CREDS = {"user": "default", "id": 1, "is_admin": True, "nextcloud_user": "admin"}


def _request():
    from fastapi import Request

    return MagicMock(spec=Request)


def _patch_resolver(monkeypatch, main, creds):
    async def fake(request, body=None):
        return creds

    monkeypatch.setattr(main, "_resolve_identity_from_request", fake)


@pytest.mark.asyncio
async def test_default_keeps_callers_own_identity(monkeypatch):
    from services.gateway import main

    mine = {"user": "jeremiah", "is_admin": True}
    _patch_resolver(monkeypatch, main, mine)

    creds = await main._resolve_acting_identity(_request(), None)

    assert creds == mine


@pytest.mark.asyncio
async def test_admin_flag_switches_to_system_default(monkeypatch):
    from services.gateway import main

    _patch_resolver(monkeypatch, main, {"user": "jeremiah", "is_admin": True})

    async def fake_first_user():
        return ADMIN_CREDS

    monkeypatch.setattr(main, "resolve_first_user", fake_first_user)

    creds = await main._resolve_acting_identity(_request(), "admin")

    assert creds["user"] == "default"
    assert creds["is_admin"] is True


@pytest.mark.asyncio
async def test_non_admin_cannot_send_as_admin(monkeypatch):
    from fastapi import HTTPException

    from services.gateway import main

    _patch_resolver(monkeypatch, main, {"user": "kiddo", "is_admin": False})

    with pytest.raises(HTTPException) as exc:
        await main._resolve_acting_identity(_request(), "admin")

    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_arbitrary_as_user_is_ignored(monkeypatch):
    from services.gateway import main

    mine = {"user": "jeremiah", "is_admin": True}
    _patch_resolver(monkeypatch, main, mine)

    async def fake_first_user():
        raise AssertionError("must not resolve another identity for arbitrary names")

    monkeypatch.setattr(main, "resolve_first_user", fake_first_user)

    creds = await main._resolve_acting_identity(_request(), "someone-else")

    assert creds == mine


@pytest.mark.asyncio
async def test_missing_admin_identity_gets_503(monkeypatch):
    from fastapi import HTTPException

    from services.gateway import main

    _patch_resolver(monkeypatch, main, {"user": "jeremiah", "is_admin": True})

    async def fake_first_user():
        return {}

    monkeypatch.setattr(main, "resolve_first_user", fake_first_user)

    with pytest.raises(HTTPException) as exc:
        await main._resolve_acting_identity(_request(), "admin")

    assert exc.value.status_code == 503


@pytest.mark.asyncio
async def test_talk_send_forwards_as_user_flag(monkeypatch):
    from services.gateway import main

    captured = {}

    async def fake_proxy(request, endpoint, payload, *, method="POST", as_user=None):
        captured.update({"endpoint": endpoint, "as_user": as_user, "payload": payload})
        return MagicMock(status_code=200)

    monkeypatch.setattr(main, "_proxy_execution_with_identity", fake_proxy)

    request = MagicMock()

    async def fake_json():
        return {"token": "tok", "message": "hello", "as_user": "admin"}

    request.json = fake_json

    await main.proxy_send_talk_message(request)

    assert captured["endpoint"] == "/execute/talk"
    assert captured["as_user"] == "admin"
    assert captured["payload"]["message"] == "hello"


@pytest.mark.asyncio
async def test_talk_reads_do_not_forward_as_user(monkeypatch):
    from services.gateway import main

    captured = {}

    async def fake_proxy(request, endpoint, payload, *, method="POST", as_user=None):
        captured.update({"as_user": as_user, "payload": payload})
        return MagicMock(status_code=200)

    monkeypatch.setattr(main, "_proxy_execution_with_identity", fake_proxy)

    request = MagicMock()
    request.query_params = {"token": "tok", "limit": "50"}

    await main.proxy_get_talk_messages(request)

    assert captured["as_user"] is None
