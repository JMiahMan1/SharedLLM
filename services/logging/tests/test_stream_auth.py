"""The live log stream is the whole system's logs: admins (or the internal
secret) only, and an unknown token is refused rather than resolved to a
default account."""
import asyncio

import pytest

from services.logging import main as logging_main


class _Resp:
    def __init__(self, status, body=None):
        self.status = status
        self._body = body or {}

    async def json(self):
        return self._body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def _identity(monkeypatch, answers):
    calls = []

    class _Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, url, params=None, **kw):
            calls.append((url, params))
            return answers.get(params["api_key"], _Resp(401))

    monkeypatch.setattr(logging_main.aiohttp, "ClientSession", lambda *a, **k: _Session())
    return calls


class _Socket:
    def __init__(self, token):
        self.query_params = {"token": token} if token is not None else {}


def _authorized(token):
    return asyncio.run(logging_main._ws_authorized(_Socket(token)))


def test_an_unknown_token_is_refused_not_resolved_to_a_default_account(monkeypatch):
    calls = _identity(monkeypatch, {})
    assert _authorized("anything") is False
    assert calls and calls[0][0].endswith("/api/internal/validate-api-key")


def test_a_family_members_key_is_not_enough(monkeypatch):
    _identity(monkeypatch, {"kid-key": _Resp(200, {"user": "leo", "is_admin": False})})
    assert _authorized("kid-key") is False


def test_an_admins_key_opens_the_stream(monkeypatch):
    _identity(monkeypatch, {"admin-key": _Resp(200, {"user": "jeremiah", "is_admin": True})})
    assert _authorized("admin-key") is True


@pytest.mark.parametrize("token", [None, ""])
def test_no_token_is_refused_without_asking_identity(monkeypatch, token):
    calls = _identity(monkeypatch, {})
    assert _authorized(token) is False
    assert calls == []


def test_the_internal_secret_still_works(monkeypatch):
    calls = _identity(monkeypatch, {})
    assert _authorized(logging_main.INTERNAL_SECRET) is True
    assert calls == []
