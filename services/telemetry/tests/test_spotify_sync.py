"""Spotify's recently played onto each linked member's feed, every ten minutes."""
import asyncio

import pytest
from fakes import FakeRedis

from services.telemetry import guardian, spotify_sync, store

SETTINGS = [
    {"key": "spotify_client_id", "value": "cid"},
    {"key": "spotify_client_secret", "value": "cs"},
    {"key": "spotify_redirect_uri", "value": "https://j/cb"},
]
PLAYED = {"items": [
    {"played_at": "2026-10-24T10:05:00Z", "track": {"name": "Blessings", "artists": [{"name": "Lecrae"}], "album": {"name": "A"}}},
]}


class _Resp:
    def __init__(self, status, body):
        self.status, self._body = status, body

    async def json(self, content_type=None):
        return self._body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _Session:
    def __init__(self, settings, accounts, played=(200, PLAYED), token=(200, {"access_token": "NEW", "expires_in": 3600})):
        self.settings, self.accounts, self.played, self.token = settings, accounts, played, token
        self.calls: list[tuple] = []

    async def get(self, url, **kw):
        self.calls.append(("GET", url, kw))
        if url.endswith("/api/settings"):
            return _Resp(200, self.settings)
        if url.endswith("/api/internal/linked-accounts/spotify"):
            return _Resp(200, {"accounts": self.accounts})
        if url.endswith("/me/player/recently-played"):
            return _Resp(*self.played)
        return _Resp(404, {})

    async def post(self, url, **kw):
        self.calls.append(("POST", url, kw))
        return _Resp(*self.token)

    async def patch(self, url, **kw):
        self.calls.append(("PATCH", url, kw))
        return _Resp(200, {})


@pytest.fixture
def quiet(monkeypatch):
    async def _children(_session):
        return {"emma": {"username": "emma", "parents": ["dad"], "monitoring": "severe"}}

    async def _no_model(_session):
        return {}

    async def _identity_get(session, path, params=None):
        resp = await session.get(path)
        return resp._body

    monkeypatch.setattr(guardian, "children", _children)
    monkeypatch.setattr(guardian, "_model_settings", _no_model)
    monkeypatch.setattr(spotify_sync.notices, "_identity_get", _identity_get)


async def _noop(_body):
    return None


def test_nothing_is_read_until_the_spotify_app_is_configured(quiet):
    session = _Session(settings=[], accounts=[{"username": "emma"}])
    assert asyncio.run(spotify_sync.sync_once(FakeRedis(), session, _noop)) == 0
    assert [c[1] for c in session.calls] == [c[1] for c in session.calls if c[1].endswith("/api/settings")]


def test_new_plays_land_on_the_feed_and_the_cursor_moves(quiet):
    rc = FakeRedis()
    account = {"username": "emma", "access_token": "AT", "refresh_token": "RT", "expires_at": 10**10, "cursor_ms": 0}
    session = _Session(SETTINGS, [account])
    assert asyncio.run(spotify_sync.sync_once(rc, session, _noop)) == 1
    feed = asyncio.run(store.list_activity(rc, "emma", 10))
    assert feed[0]["item"] == "Blessings" and feed[0]["detail"]["source"] == "spotify" and feed[0]["user"] == "emma"
    patch = [c for c in session.calls if c[0] == "PATCH"][-1]
    assert patch[2]["json"]["synced"] is True and patch[2]["json"]["cursor_ms"] > 0
    assert not any(c[0] == "POST" for c in session.calls)  # the token was still good


def test_a_token_about_to_run_out_is_refreshed_and_kept(quiet):
    account = {"username": "emma", "access_token": "OLD", "refresh_token": "RT", "expires_at": 0, "cursor_ms": 0}
    session = _Session(SETTINGS, [account])
    asyncio.run(spotify_sync.sync_once(FakeRedis(), session, _noop, now=1000))
    assert any(c[0] == "POST" and c[2]["data"]["grant_type"] == "refresh_token" for c in session.calls)
    kept = next(c for c in session.calls if c[0] == "PATCH")[2]["json"]
    assert kept["access_token"] == "NEW"
    played = next(c for c in session.calls if c[1].endswith("/recently-played"))
    assert played[2]["headers"]["Authorization"] == "Bearer NEW"


def test_one_members_failure_is_kept_on_their_link(quiet):
    account = {"username": "emma", "access_token": "AT", "refresh_token": "RT", "expires_at": 10**10, "cursor_ms": 0}
    session = _Session(SETTINGS, [account], played=(401, {}))
    assert asyncio.run(spotify_sync.sync_once(FakeRedis(), session, _noop)) == 0
    patch = [c for c in session.calls if c[0] == "PATCH"][-1][2]["json"]
    assert "link Spotify again" in patch["last_error"]
