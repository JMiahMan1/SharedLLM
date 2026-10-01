"""Every per-user geo route must refuse another user's data without opt-in.

This file is the regression net for the access-control rewrite. It is
deliberately table-driven across every route that calls ``_require_may_view``
so that a future edit which drops the call from one route cannot silently
re-open that single route while the others stay protected.

Each route is checked three ways:

* no consent            -> 404 (not 403: a probe must not be able to tell
                           "no such user" from "not shared with you")
* target opted in       -> 200
* caller is an admin    -> 200 regardless of consent (deliberate carve-out)

The edge middleware and the previously-unauthenticated writes are covered too.
"""

import pytest
from fastapi.testclient import TestClient

import services.geo.main as geo
from services.config import INTERNAL_SECRET

SECRET = {"X-Internal-Secret": INTERNAL_SECRET}


# ── Redis stand-in ──────────────────────────────────────────────────────────
# The consent check runs before any storage access, so the "no consent" cases
# never touch Redis at all. The "allowed" cases do, and only need empty answers.
class _EmptyRedis:
    _LISTS = {"zrevrange", "zrange", "zrevrangebyscore", "zrangebyscore", "keys", "smembers"}
    _MAPS = {"hgetall"}
    _INTS = {"zcard", "exists", "ttl", "llen", "scard"}

    def __getattr__(self, name):
        async def _empty(*a, **k):
            if name in self._LISTS:
                return []
            if name in self._MAPS:
                return {}
            if name in self._INTS:
                return 0
            return None

        return _empty


@pytest.fixture
def redis(monkeypatch):
    async def _get():
        return _EmptyRedis()

    monkeypatch.setattr(geo, "get_redis", _get)
    return _EmptyRedis()


@pytest.fixture
def client():
    """Authenticated service-to-service client (carries the internal secret)."""
    return TestClient(geo.app, headers=SECRET)


def _sharing(entries):
    """`_fetch_activity_sharing` stub: {username: {**overrides}}."""

    async def _fetch():
        return {
            "users": [
                {"username": n, "enabled": False, "audience": "circle", "user_ids": [], **o}
                for n, o in entries.items()
            ]
        }

    return _fetch


# Every route that gates a per-user read, with the params each needs to reach
# the authorisation decision. Kept as data so a missing route is conspicuous.
READ_ROUTES = [
    ("/steps", {}),
    ("/goals", {}),
    ("/points", {}),
    ("/achievements", {}),
    ("/steps/goal", {}),
    ("/trends/activity", {"days": 7}),
    ("/trips", {}),
    ("/workouts", {}),
]

# `/android_auto` is checked for refusal only: past the consent gate it calls
# Home Assistant for the companion sensors, which is out of scope here.
REFUSAL_ONLY_ROUTES = [("/android_auto", {})]


@pytest.mark.parametrize("path,extra", READ_ROUTES + REFUSAL_ONLY_ROUTES)
def test_another_users_data_is_refused_without_opt_in(
    client, redis, monkeypatch, path, extra
):
    monkeypatch.setattr(geo, "_fetch_activity_sharing", _sharing({}))
    r = client.get(path, params={**extra, "user_id": "jeremiah", "viewer": "michele"})
    assert r.status_code == 404, f"{path} leaked another user's data ({r.status_code})"
    assert "not shared" in r.text


@pytest.mark.parametrize("path,extra", READ_ROUTES)
def test_opted_in_target_is_readable(client, redis, monkeypatch, path, extra):
    monkeypatch.setattr(
        geo,
        "_fetch_activity_sharing",
        _sharing({"jeremiah": {"enabled": True, "audience": "circle"}}),
    )
    r = client.get(path, params={**extra, "user_id": "jeremiah", "viewer": "michele"})
    assert r.status_code == 200, f"{path} refused a legitimately shared read: {r.text}"


@pytest.mark.parametrize("path,extra", READ_ROUTES)
def test_admin_reads_anything_without_opt_in(client, redis, monkeypatch, path, extra):
    monkeypatch.setattr(geo, "_fetch_activity_sharing", _sharing({}))
    r = client.get(
        path, params={**extra, "user_id": "jeremiah", "viewer": "michele", "is_admin": "true"}
    )
    assert r.status_code == 200, f"{path} denied the admin carve-out: {r.text}"


def test_own_data_needs_no_opt_in(client, redis, monkeypatch):
    """You always see yourself, with no sharing row configured at all."""
    monkeypatch.setattr(geo, "_fetch_activity_sharing", _sharing({}))
    for path in ("/steps", "/goals", "/points", "/achievements", "/steps/goal", "/trips", "/workouts"):
        r = client.get(path, params={"user_id": "michele", "viewer": "michele"})
        assert r.status_code == 200, f"{path} refused self-read: {r.text}"


def test_explicit_audience_list_is_honoured(client, redis, monkeypatch):
    """"Specific people" means exactly that -- not the whole household."""
    monkeypatch.setattr(
        geo,
        "_fetch_activity_sharing",
        _sharing({"jeremiah": {"enabled": True, "audience": "users", "user_ids": ["sam"]}}),
    )
    named = client.get("/steps", params={"user_id": "jeremiah", "viewer": "sam"})
    assert named.status_code == 200
    other = client.get("/steps", params={"user_id": "jeremiah", "viewer": "michele"})
    assert other.status_code == 404


def test_disabled_sharing_is_not_shared(client, redis, monkeypatch):
    monkeypatch.setattr(
        geo,
        "_fetch_activity_sharing",
        _sharing({"jeremiah": {"enabled": False, "audience": "circle"}}),
    )
    r = client.get("/steps", params={"user_id": "jeremiah", "viewer": "michele"})
    assert r.status_code == 404


def test_identity_outage_fails_closed(client, redis, monkeypatch):
    """If consent cannot be established, nobody else's data is served.

    Exercises the *real* `_fetch_activity_sharing` -- including its own
    try/except -- against a dead Identity. Stubbing the fetcher out instead
    would bypass the exact failure handling under test.
    """

    def _dead():
        raise RuntimeError("identity unreachable")

    monkeypatch.setattr(geo, "get_client_insecure", _dead)
    r = client.get("/steps", params={"user_id": "jeremiah", "viewer": "michele"})
    assert r.status_code == 404, f"identity outage served data ({r.status_code})"


# ── the "all users" buckets ─────────────────────────────────────────────────
def test_the_all_bucket_is_admin_only(client, redis, monkeypatch):
    """Omitting user_id must not hand back everyone's rows.

    The refusal *shape* differs by route (400 "user_id is required" where the
    route normalises through `_require_may_view`, 401 where it explicitly
    guards the `all` bucket), so the invariant asserted is the one that
    actually matters: a non-admin never gets a 200 payload here.
    """
    monkeypatch.setattr(geo, "_fetch_activity_sharing", _sharing({}))
    for path in (
        "/steps",
        "/goals",
        "/achievements",
        "/points",
        "/trips",
        "/workouts",
        "/trends/activity",
    ):
        r = client.get(path, params={"viewer": "michele"})
        assert r.status_code != 200, f"{path} served the all-bucket to a non-admin"


# ── /people filters rather than 404s ────────────────────────────────────────
def _ha_state(entity_id, friendly):
    """One HA state entry, as `/api/states` returns them: a flat list."""
    return {
        "entity_id": entity_id,
        "state": "home",
        "attributes": {"friendly_name": friendly, "latitude": 33.1, "longitude": -111.5},
    }


def _stub_ha_states(monkeypatch, states):
    """Make `_ha_get_states()` return `states` without touching Home Assistant."""

    async def _get():
        return states

    monkeypatch.setattr(geo, "_ha_get_states", _get)


def _person_ids(response):
    return [f["properties"]["entity_id"] for f in response.json()["features"]]


def test_people_hides_entities_with_no_attributable_user(client, redis, monkeypatch):
    """`person.michelle_phone` must not match a user named `mich`.

    Substring matching is the tempting shortcut and it is wrong: it would make
    one person's phone visible to another.
    """
    monkeypatch.setattr(geo, "_fetch_activity_sharing", _sharing({}))
    _stub_ha_states(
        monkeypatch,
        [
            _ha_state("person.mich", "Mich"),
            _ha_state("person.michele_phone", "Michele's phone"),
            _ha_state("person.jeremiah", "Jeremiah"),
        ],
    )
    r = client.get("/people", params={"viewer": "sam", "is_admin": "false"})
    assert r.status_code == 200
    assert _person_ids(r) == [], f"/people leaked unattributable entities: {_person_ids(r)}"


def test_people_shows_only_opted_in_users(client, redis, monkeypatch):
    monkeypatch.setattr(
        geo,
        "_fetch_activity_sharing",
        _sharing({"michele": {"enabled": True, "audience": "circle"}}),
    )
    _stub_ha_states(
        monkeypatch,
        [_ha_state("person.michele", "Michele"), _ha_state("person.jeremiah", "Jeremiah")],
    )
    ids = _person_ids(client.get("/people", params={"viewer": "sam"}))
    assert ids == ["person.michele"], f"unexpected visibility: {ids}"


def test_people_admin_sees_everyone(client, redis, monkeypatch):
    monkeypatch.setattr(geo, "_fetch_activity_sharing", _sharing({}))
    _stub_ha_states(
        monkeypatch,
        [_ha_state("person.jeremiah", "Jeremiah"), _ha_state("person.mich", "Mich")],
    )
    ids = _person_ids(client.get("/people", params={"viewer": "jeremiah", "is_admin": "true"}))
    assert sorted(ids) == ["person.jeremiah", "person.mich"]

# ── edge: no internal secret ────────────────────────────────────────────────
def test_geo_is_unreachable_without_the_internal_secret(redis):
    """Geo is published directly on the host, so the edge must reject callers.

    Covers every route that used to verify nothing at all.
    """
    anon = TestClient(geo.app, raise_server_exceptions=False)
    for method, path, extra in [
        ("get", "/people", {}),
        ("get", "/zones", {}),
        ("get", "/trips", {}),
        ("get", "/workouts", {}),
        ("get", "/vehicles", {}),
        ("get", "/android_auto", {}),
        ("get", "/steps", {}),
        ("get", "/goals", {}),
        ("get", "/achievements", {}),
        ("get", "/points", {}),
        ("get", "/steps/goal", {}),
        ("get", "/trends/activity", {}),
    ]:
        r = getattr(anon, method)(path, params=extra)
        assert r.status_code in (401, 403), f"{path} answered an unauthenticated caller ({r.status_code})"


def test_health_stays_reachable_without_the_secret(redis):
    """The container healthcheck carries no data and must keep working."""
    assert TestClient(geo.app).get("/health").status_code == 200


# ── previously-unauthenticated WRITES ───────────────────────────────────────
def test_goal_writes_require_the_internal_secret(redis):
    """PUT /goals and PUT /steps/goal accepted the header and ignored it."""
    anon = TestClient(geo.app, raise_server_exceptions=False)
    assert anon.put("/goals", json={"user_id": "jeremiah", "goals": {"daily_steps": 1}}).status_code in (401, 403)
    assert anon.put("/steps/goal", json={"user_id": "jeremiah", "goal": 1}).status_code in (401, 403)