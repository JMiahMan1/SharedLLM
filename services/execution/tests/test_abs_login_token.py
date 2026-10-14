"""ABS login: prefer the JWT access token, and cache it no longer than it lives."""
import base64
import json
import time

import pytest

from services.execution import abs_client


def _jwt(exp: float) -> str:
    body = base64.urlsafe_b64encode(json.dumps({"exp": exp}).encode()).decode().rstrip("=")
    return f"h.{body}.s"


def test_a_jwt_is_cached_until_a_minute_before_it_expires():
    assert 0 < abs_client._token_ttl(_jwt(time.time() + 600)) <= 540
    assert abs_client._token_ttl(_jwt(time.time() + 86400)) == abs_client._CACHE_TTL
    assert abs_client._token_ttl(_jwt(time.time() - 10)) == 0


def test_a_legacy_token_gets_the_usual_ttl():
    assert abs_client._token_ttl("opaque-legacy-token") == abs_client._CACHE_TTL


class _Resp:
    def __init__(self, user):
        self._user = user

    def raise_for_status(self):
        pass

    async def json(self):
        return {"user": self._user}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class _Client:
    def __init__(self, user):
        self.user = user
        self.urls = []

    def post(self, url, json=None, timeout=None):
        self.urls.append(url)
        return _Resp(self.user)


@pytest.fixture
def fake_abs(monkeypatch):
    from contextlib import asynccontextmanager

    holder = {}

    @asynccontextmanager
    async def session(_url):
        yield holder["client"]

    monkeypatch.setattr(abs_client, "_abs_session", session)
    abs_client._CACHE.clear()
    return holder


async def test_login_prefers_the_access_token_at_the_root_login(fake_abs):
    fake_abs["client"] = _Client({"accessToken": _jwt(time.time() + 3600), "token": "legacy"})
    token = await abs_client.abs_login("https://abs.example.com", "u", "p", force=True)
    assert token.startswith("h.") and fake_abs["client"].urls == ["https://abs.example.com/login"]


async def test_an_older_server_without_access_tokens_still_logs_in(fake_abs):
    fake_abs["client"] = _Client({"token": "legacy"})
    assert await abs_client.abs_login("https://abs.example.com/", "u", "p", force=True) == "legacy"
