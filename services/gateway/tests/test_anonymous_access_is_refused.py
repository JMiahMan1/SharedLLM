"""No route that reads family data may serve an unproven caller.

Identity's ``POST /api/resolve`` upgrades anything it does not recognise to the
system default user -- an administrator -- and most of these routes check
``is_admin`` afterwards, which made that check decoration. Verified live against
production before ``_resolve_identity_from_request`` was made strict: all of
the GETs below answered 200 with no credentials at all.

The matrix is pinned per route so a future "just use resolve_identity here"
cannot quietly reopen one.
"""
import pytest
from fastapi.testclient import TestClient

from services.gateway import main as gateway_main
from services.gateway.main import app

# Routes that were confirmed open to anonymous callers, with a body where the
# verb needs one. Kept explicit rather than discovered: a route added later is
# not automatically covered, which is the point.
LEAKED = [
    ("GET", "/api/integrations/skylight/chores?date=today", None),
    ("GET", "/api/integrations/skylight/rewards", None),
    ("GET", "/api/media/music-assistant/playlists", None),
    ("GET", "/api/media/music-assistant/recent", None),
    ("GET", "/api/media/music-assistant/browse", None),
    ("GET", "/api/media/home", None),
    ("GET", "/api/media/search?q=x", None),
    ("GET", "/api/media/item?uri=library://track/1", None),
    ("GET", "/api/media/library/tracks", None),
    ("GET", "/api/media/favorites", None),
    ("GET", "/api/media/audiobookshelf/libraries", None),
    ("GET", "/api/media/audiobookshelf/last-played", None),
    ("GET", "/api/media/audiobookshelf/status", None),
    ("GET", "/api/ma-jsonrpc/debug/players", None),
    ("GET", "/api/ma-jsonrpc/debug/queues", None),
    ("GET", "/api/workspaces", None),
    ("GET", "/api/admin/services", None),
    ("GET", "/api/admin/logs", None),
    ("GET", "/api/raven/missions", None),
    ("GET", "/api/entities", None),
    ("GET", "/api/storage/stats", None),
    ("GET", "/api/search?q=anything", None),
]

# Upstream services would be contacted on the happy path; these tests only care
# about the gate, so every upstream answer is a harmless empty JSON body.
LEAKED_POST = [
    ("POST", "/api/storage/list", {"path": "/"}),
    ("POST", "/api/storage/index", {"path": "/"}),
    ("POST", "/api/media/abs/progress", {"item_id": "b1", "current_time": 1, "duration": 10}),
]


@pytest.fixture
def anon_client(monkeypatch):
    """A client with no credentials, and no Identity to ask."""

    async def _strict(api_key: str):
        return None  # every key is junk: nothing may resolve

    async def _resolve(body):
        # If anything reaches the permissive resolver during these tests, the
        # default-admin payload below is what it would hand back -- i.e. the bug.
        return {"user": "default", "is_admin": True, "mass_url": "", "mass_token": ""}

    monkeypatch.setattr(gateway_main, "_resolve_strict_identity", _strict)
    monkeypatch.setattr(gateway_main, "resolve_identity", _resolve)
    return TestClient(app, raise_server_exceptions=False)


def _call(client, verb, path, body):
    return getattr(client, verb.lower())(path, json=body) if body is not None else getattr(
        client, verb.lower()
    )(path)


@pytest.mark.parametrize("verb,path,body", LEAKED, ids=[p for _, p, _ in LEAKED])
def test_anonymous_callers_are_refused(anon_client, verb, path, body):
    resp = _call(anon_client, verb, path, body)
    assert resp.status_code == 401, f"{verb} {path} served an anonymous caller: {resp.text[:200]}"


@pytest.mark.parametrize("verb,path,body", LEAKED, ids=[p for _, p, _ in LEAKED])
def test_a_made_up_key_is_refused(anon_client, verb, path, body):
    """The exact hole: a string that is nobody's key used to become the admin."""
    client = TestClient(
        app, headers={"Authorization": "Bearer not-a-real-key"}, raise_server_exceptions=False
    )
    monkey = None  # noqa: F841 - documented below
    resp = _call(client, verb, path, body)
    assert resp.status_code == 401, f"{verb} {path} accepted a bogus API key: {resp.text[:200]}"


@pytest.mark.parametrize("verb,path,body", LEAKED_POST, ids=[p for _, p, _ in LEAKED_POST])
def test_anonymous_writes_are_refused(anon_client, verb, path, body):
    resp = _call(anon_client, verb, path, body)
    assert resp.status_code == 401, f"{verb} {path} accepted an anonymous write: {resp.text[:200]}"


def test_the_error_says_what_is_missing(anon_client):
    resp = anon_client.get("/api/integrations/skylight/chores")
    assert resp.status_code == 401
    detail = resp.json()["detail"].lower()
    assert "api key" in detail, "a caller cannot tell what to send"
    assert "bearer" in detail, "the error does not say how to send it"


def test_a_real_key_still_gets_through(client):
    """The gate must not become a wall: a valid key still serves the route."""
    resp = client.get("/api/integrations/skylight/chores")
    assert resp.status_code != 401