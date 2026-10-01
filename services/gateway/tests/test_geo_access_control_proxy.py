"""Gateway access control for the geo proxy block.

Before this rewrite the geo block resolved identity in three different ways,
none of them safe:

* ``GET /api/geo/trips`` and ``GET /api/geo/workouts`` forwarded **no identity
  at all**, so geo served its ``geo:trips:all`` / ``geo:workouts:all`` indexes
  -- every user's history to any authenticated caller.
* ``_user_id_from_request`` trusted an ``X-User-Id`` header **verbatim**, and
  preferred it over the Bearer token, so any caller could assert any identity.
* a caller-supplied ``?user_id=`` always beat the authenticated identity.

These tests pin the corrected behaviour. Consent itself (whose data) is
enforced in geo, which owns ``_viewer_may_see``; the gateway's job is to send a
trustworthy viewer and never invent one.
"""
import json
from contextlib import asynccontextmanager

import pytest
from fastapi.testclient import TestClient

from services.gateway import main as gateway_main
from services.gateway.main import app

SECRET = {"X-Internal-Secret": gateway_main.INTERNAL_SECRET}

# A representative read from each shape of route in the block.
READ_ROUTES = [
    "/api/geo/trips",
    "/api/geo/workouts",
    "/api/geo/steps",
    "/api/geo/goals",
    "/api/geo/achievements",
    "/api/geo/points",
    "/api/geo/steps/goal",
    "/api/geo/trends/activity",
]


def _patch_geo(monkeypatch, status=200, payload=None, captured=None):
    """Capture whatever the gateway forwards to geo.

    ``shared_http_client`` is *also* how the gateway ships request logs, and
    that POST lands after the geo call, so a naive capture is overwritten by
    ``http://logging:8006/log``. Only geo-bound calls are recorded.
    """
    captured = captured if captured is not None else {}
    captured["calls"] = []
    geo_svc = str(gateway_main.GEO_SVC)

    class _Resp:
        def __init__(self):
            self.status = status
            self.text = json.dumps(payload or {})

        async def json(self):
            return payload if payload is not None else {}

        async def read(self):
            return json.dumps(payload or {}).encode()

    class _Client:
        async def _record(self, verb, url, **kwargs):
            if url.startswith(geo_svc):
                captured["calls"].append(
                    {
                        "verb": verb,
                        "url": url,
                        "params": kwargs.get("params"),
                        "json": kwargs.get("json"),
                        "headers": kwargs.get("headers") or {},
                    }
                )
            return _Resp()

        async def get(self, url, **kw):
            return await self._record("get", url, **kw)

        async def post(self, url, **kw):
            return await self._record("post", url, **kw)

        async def put(self, url, **kw):
            return await self._record("put", url, **kw)

        async def patch(self, url, **kw):
            return await self._record("patch", url, **kw)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    @asynccontextmanager
    async def fake():
        yield _Client()

    monkeypatch.setattr(gateway_main, "shared_http_client", fake)
    return captured


@pytest.fixture
def anon_client():
    """No credentials at all."""
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def make_client(monkeypatch):
    """Build a TestClient whose presented key resolves to a chosen identity.

    Patching ``_resolve_strict_identity`` is deliberate: it is the strict,
    no-fallback identity call the gateway now uses. Using ``resolve_identity``
    here would test the old, unsafe behaviour -- that one resolves *any* junk
    string to the default admin.
    """

    def _make(user="michele", is_admin=False, key="test-token"):
        async def _resolve(api_key):
            if api_key != key:
                return None
            return {"user": user, "user_id": 7, "is_admin": is_admin}

        monkeypatch.setattr(gateway_main, "_resolve_strict_identity", _resolve)
        return TestClient(app, headers={"Authorization": f"Bearer {key}"})

    return _make


# ── authentication ──────────────────────────────────────────────────────────
@pytest.mark.parametrize("path", READ_ROUTES)
def test_anonymous_readers_are_rejected(anon_client, monkeypatch, path):
    _patch_geo(monkeypatch)
    assert anon_client.get(path).status_code == 401, f"{path} served an anonymous caller"


def test_a_junk_key_is_rejected_and_never_becomes_the_default_admin(make_client, monkeypatch):
    """The dangerous failure is not 'no user' -- it is 'the admin'.

    ``POST /api/resolve`` falls back to the default admin for unmatched
    credentials, so a validation built on it would hand every anonymous caller
    an administrator. Strict resolution must simply refuse.
    """
    client = make_client(user="nobody", key="valid-key")
    _patch_geo(monkeypatch)
    # A key that does not match the faked identity resolves to nothing.
    assert TestClient(app, headers={"Authorization": "Bearer wrong"}).get("/api/geo/trips").status_code == 401
    assert client.get("/api/geo/trips").status_code in (200, 502)


def test_trips_forwards_the_authenticated_viewer(make_client, monkeypatch):
    captured = _patch_geo(monkeypatch, payload={"trips": []})
    make_client(user="michele").get("/api/geo/trips", params={"user_id": "jeremiah"})
    sent = captured["calls"][-1]["params"]
    assert sent["viewer"] == "michele"
    assert sent["user_id"] == "jeremiah", "geo lost the requested target"


def test_trips_defaults_to_the_caller_when_no_user_id(make_client, monkeypatch):
    captured = _patch_geo(monkeypatch, payload={"trips": []})
    make_client(user="michele").get("/api/geo/trips")
    assert captured["calls"][-1]["params"]["user_id"] == "michele", (
        "omitting user_id must not mean 'everyone'"
    )


def test_admin_status_is_forwarded_to_geo(make_client, monkeypatch):
    captured = _patch_geo(monkeypatch, payload={"trips": []})
    make_client(user="jeremiah", is_admin=True).get("/api/geo/trips")
    assert str(captured["calls"][-1]["params"].get("is_admin", "")).lower() in {"1", "true", "yes"}


def test_non_admin_is_not_marked_admin(make_client, monkeypatch):
    captured = _patch_geo(monkeypatch, payload={"trips": []})
    make_client(user="michele", is_admin=False).get("/api/geo/trips")
    assert str(captured["calls"][-1]["params"].get("is_admin", "")).lower() not in {"1", "true", "yes"}


# ── the impersonation hole ──────────────────────────────────────────────────
def test_a_client_supplied_user_id_header_cannot_impersonate(anon_client, monkeypatch):
    """``X-User-Id`` is a gateway->geo transport header, not a client credential.

    It used to be trusted verbatim and preferred over the Bearer token, so
    anyone could simply declare themselves. Nothing in the UI ever sends it.
    """
    _patch_geo(monkeypatch, status=200, payload={"trips": []})
    r = anon_client.get("/api/geo/trips", headers={"X-User-Id": "jeremiah"})
    assert r.status_code == 401, "a bare X-User-Id header was trusted as an identity"


def test_internal_secret_may_still_assert_an_identity(anon_client, monkeypatch):
    """Service-to-service callers legitimately set both headers."""
    captured = _patch_geo(monkeypatch, payload={"trips": []})
    r = anon_client.get("/api/geo/trips", headers={"X-User-Id": "jeremiah", **SECRET})
    assert r.status_code == 200
    assert captured["calls"][-1]["params"]["user_id"] == "jeremiah"


# ── writes ──────────────────────────────────────────────────────────────────
def test_steps_cannot_be_written_for_another_user(make_client, monkeypatch):
    """POST /steps took the body's user_id unchecked -- that is the orphan
    ``geo:steps_meta:me`` writer, and it let anyone inflate anyone's total."""
    captured = _patch_geo(monkeypatch, payload={"status": "ok"})
    r = make_client(user="michele").post("/api/geo/steps", json={"user_id": "jeremiah", "steps": 99999})
    assert r.status_code in (401, 403), f"cross-user step write accepted ({r.status_code})"
    assert not captured["calls"], "a rejected write was still forwarded to geo"


def test_steps_write_for_self_is_allowed(make_client, monkeypatch):
    _patch_geo(monkeypatch, payload={"status": "ok", "user_id": "michele", "steps": 100})
    r = make_client(user="michele").post("/api/geo/steps", json={"user_id": "michele", "steps": 100})
    assert r.status_code == 200


def test_anonymous_step_writes_are_rejected(anon_client, monkeypatch):
    captured = _patch_geo(monkeypatch, payload={"status": "ok"})
    assert anon_client.post("/api/geo/steps", json={"user_id": "michele", "steps": 1}).status_code == 401
    assert not captured["calls"], "an unauthenticated write reached geo"


def test_goals_cannot_be_rewritten_for_another_user(make_client, monkeypatch):
    captured = _patch_geo(monkeypatch, payload={"status": "ok"})
    r = make_client(user="michele").put("/api/geo/goals", json={"user_id": "jeremiah", "goals": {"daily_steps": 1}})
    assert r.status_code in (401, 403), f"cross-user goal write accepted ({r.status_code})"
    assert not captured["calls"]


def test_step_goal_cannot_be_rewritten_for_another_user(make_client, monkeypatch):
    captured = _patch_geo(monkeypatch, payload={"status": "ok"})
    r = make_client(user="michele").put("/api/geo/steps/goal", json={"user_id": "jeremiah", "goal": 1})
    assert r.status_code in (401, 403), f"cross-user goal write accepted ({r.status_code})"
    assert not captured["calls"]


def test_granting_stars_requires_admin(make_client, monkeypatch):
    """The route docstring claimed Identity enforced this. Nothing did."""
    captured = _patch_geo(monkeypatch, payload={"status": "ok"})
    r = make_client(user="michele").post("/api/geo/stars", json={"user_id": "michele", "amount": 5})
    assert r.status_code == 403, f"a non-admin granted stars ({r.status_code})"
    assert not captured["calls"]


def test_an_admin_may_grant_stars(make_client, monkeypatch):
    _patch_geo(monkeypatch, payload={"status": "ok"})
    r = make_client(user="jeremiah", is_admin=True).post("/api/geo/stars", json={"user_id": "michele", "amount": 5})
    assert r.status_code == 200


def test_reading_your_own_stars_needs_no_admin_rights(make_client, monkeypatch):
    """Star *balances* are personal; only granting them is privileged."""
    _patch_geo(monkeypatch, payload={"stars": 3})
    assert make_client(user="michele").get("/api/geo/stars").status_code == 200


# ── activity sharing keeps its explicit viewer contract ─────────────────────
def test_activity_feed_requires_a_viewer(anon_client, monkeypatch):
    _patch_geo(monkeypatch, payload={"users": []})
    assert anon_client.get("/api/geo/activity/feed").status_code == 401


def test_activity_feed_forwards_the_viewer(make_client, monkeypatch):
    captured = _patch_geo(monkeypatch, payload={"users": []})
    make_client(user="michele").get("/api/geo/activity/feed")
    assert captured["calls"][-1]["params"]["viewer"] == "michele"

# ── the active-workout route ─────────────────────────────────────────────────
# Added alongside `GET /workouts/active`. An in-progress session is deliberately
# absent from the history index, so this is the only way a client can learn that
# one is running; without it, "Stop & Save" was unreachable after a reload.

def test_active_workout_requires_authentication(anon_client, monkeypatch):
    _patch_geo(monkeypatch)
    assert anon_client.get("/api/geo/workouts/active").status_code == 401


def test_active_workout_forwards_the_viewer_and_admin_flag(make_client, monkeypatch):
    captured = _patch_geo(monkeypatch, payload={"workout": {"status": "in_progress"}})
    make_client(user="michele", is_admin=True).get("/api/geo/workouts/active")
    call = captured["calls"][-1]
    assert call["url"].endswith("/workouts/active")
    assert call["params"]["viewer"] == "michele"
    assert call["params"]["is_admin"] == "true"
    # X-User-Id is the gateway's own transport header; geo needs it to know
    # whose session to read.
    assert call["headers"].get("X-User-Id") == "michele"


def test_active_workout_passes_the_real_status_through(make_client, monkeypatch):
    """The value the old client-side scan was looking for.

    Geo writes `in_progress`, never `active`, which is exactly why
    ``find(w => w.status === 'active')`` could never match.
    """
    _patch_geo(monkeypatch, payload={"workout": {"status": "in_progress"}})
    resp = make_client(user="michele").get("/api/geo/workouts/active")
    assert resp.status_code == 200
    assert resp.json()["workout"]["status"] == "in_progress"


def test_no_active_workout_is_reported_as_absent_not_an_error(make_client, monkeypatch):
    """Geo answers 404 for 'nothing running'; the client gets null."""
    _patch_geo(monkeypatch, status=404)
    resp = make_client(user="michele").get("/api/geo/workouts/active")
    assert resp.status_code == 200
    assert resp.json() == {"workout": None}


def test_active_workout_refuses_a_cross_user_request_from_a_non_admin(make_client, monkeypatch):
    """A non-admin asking about someone else's live session is refused.

    The gateway still asks geo (it owns consent); the assertion is that the
    request is scoped to the *viewer*, never an `all` bucket.
    """
    captured = _patch_geo(monkeypatch, status=404)
    make_client(user="michele").get(
        "/api/geo/workouts/active", params={"user_id": "jeremiah"}
    )
    call = captured["calls"][-1]
    assert call["headers"].get("X-User-Id") == "jeremiah"
    assert call["params"]["viewer"] == "michele", "consent would be decided against the wrong person"
    assert call["params"]["is_admin"] != "true"


def test_active_workout_does_not_leak_another_users_session_to_a_non_admin(make_client, monkeypatch):
    """End-to-end refusal: geo 404s, so the client is told nothing is running."""
    _patch_geo(monkeypatch, status=404)
    resp = make_client(user="michele").get(
        "/api/geo/workouts/active", params={"user_id": "jeremiah"}
    )
    assert resp.json() == {"workout": None}
