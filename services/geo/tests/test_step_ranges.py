"""The range-aware step history endpoint.

`/steps` returns raw daily buckets and is capped at 30 days. This route is the
one that can serve a year, which is why aggregation moved server-side rather
than into a browser that would have to refold 365 buckets for every new range.

Consent is the thing worth pinning here: this is a *new* read path for other
people's step data, and it must not become a way around `_require_may_view`.
"""
import pytest
from fastapi.testclient import TestClient

from services.geo import main as geo_main


class _FakeRedis:
    """Just enough Redis for the aggregation path."""

    def __init__(self, daily: dict[str, int] | None = None, goal: int | None = None):
        self.daily = daily or {}
        self.goal = goal

    async def hgetall(self, key):
        return dict(self.daily) if "steps:" in key else {}

    async def hget(self, key, field):
        return None

    async def get(self, key):
        if key.startswith("geo:steps_goal:") and self.goal is not None:
            return str(self.goal).encode()
        return None


@pytest.fixture
def client(monkeypatch):
    fake = _FakeRedis()

    async def fake_redis():
        return fake

    async def fake_sharing():
        # Opted in to the whole circle, so any non-owner may read.
        return {
            "users": [
                {"username": "michele", "enabled": True, "audience": "circle", "user_ids": []},
            ]
        }

    monkeypatch.setattr(geo_main, "get_redis", fake_redis)
    monkeypatch.setattr(geo_main, "_fetch_activity_sharing", fake_sharing)
    monkeypatch.setitem(geo_main.__dict__, "_FAKE", fake)
    return TestClient(geo_main.app)


@pytest.fixture
def fake_redis_instance(client):
    return geo_main.__dict__["_FAKE"]


def _today():
    return geo_main._today_date().isoformat()


class TestRangesEndpoint:
    def test_returns_buckets_for_a_range(self, client):
        r = client.get("/steps/ranges?range=W&user_id=michele", headers={"X-Internal-Secret": geo_main.INTERNAL_SECRET})
        assert r.status_code == 200
        body = r.json()
        assert body["range"] == "W"
        assert len(body["buckets"]) == 7

    def test_carries_the_users_goal_through(self, client, fake_redis_instance):
        fake_redis_instance.goal = 7777
        r = client.get("/steps/ranges?range=D&user_id=michele", headers={"X-Internal-Secret": geo_main.INTERNAL_SECRET})
        assert r.json()["goal"] == 7777

    def test_a_year_is_twelve_monthly_buckets(self, client):
        """Not 365 daily buckets: that is unreadable on a phone."""
        r = client.get("/steps/ranges?range=Y&user_id=michele", headers={"X-Internal-Secret": geo_main.INTERNAL_SECRET})
        assert len(r.json()["buckets"]) == 12

    @pytest.mark.parametrize("rng", ["D", "W", "M", "3M", "Y"])
    def test_every_range_is_accepted(self, client, rng):
        r = client.get(f"/steps/ranges?range={rng}&user_id=michele", headers={"X-Internal-Secret": geo_main.INTERNAL_SECRET})
        assert r.status_code == 200
        assert r.json()["range"] == rng

    def test_rejects_an_unknown_range(self, client):
        """Fail loudly rather than silently defaulting to something plausible."""
        r = client.get("/steps/ranges?range=FOREVER&user_id=michele", headers={"X-Internal-Secret": geo_main.INTERNAL_SECRET})
        assert r.status_code == 422
        assert "range must be one of" in r.json()["detail"]

    def test_reports_that_history_is_too_thin_for_a_baseline(self, client):
        r = client.get("/steps/ranges?range=W&user_id=michele", headers={"X-Internal-Secret": geo_main.INTERNAL_SECRET})
        body = r.json()
        assert body["thin"] is True
        assert body["baseline"] is None
        assert body["baseline_min_days"] == 7

    def test_gaps_are_reported(self, client):
        r = client.get("/steps/ranges?range=W&user_id=michele", headers={"X-Internal-Secret": geo_main.INTERNAL_SECRET})
        assert r.json()["has_gaps"] is True

    def test_uses_the_configured_timezone_for_today(self, client):
        """A day boundary computed in the wrong zone files today's steps under
        yesterday, which is invisible until the totals look wrong."""
        r = client.get("/steps/ranges?range=D&user_id=michele", headers={"X-Internal-Secret": geo_main.INTERNAL_SECRET})
        assert r.json()["buckets"][0]["start"] == _today()

    def test_a_missing_user_is_not_confused_with_unshared(self, client):
        r = client.get("/steps/ranges?range=W&user_id=nobody&viewer=jeremiah", headers={"X-Internal-Secret": geo_main.INTERNAL_SECRET})
        assert r.status_code == 404


class TestConsent:
    def test_an_absent_viewer_is_treated_as_internal(self, client):
        """Not a loophole, and worth pinning so it is not mistaken for one.

        An absent viewer means another service calling geo directly: the
        internal-secret middleware rejects anything else, and the gateway 401s
        before forwarding. So the 30-day cap on /steps must not be read as a
        privacy control -- this route is no looser than the route beside it.
        """
        r = client.get("/steps/ranges?range=W&user_id=michele", headers={"X-Internal-Secret": geo_main.INTERNAL_SECRET})
        assert r.status_code == 200

    def test_a_known_viewer_is_still_filtered(self, client, monkeypatch):
        """The counterpart: as soon as a viewer is named, consent applies."""
        async def opted_out():
            return {"users": [{"username": "michele", "enabled": False, "audience": "circle", "user_ids": []}]}

        monkeypatch.setattr(geo_main, "_fetch_activity_sharing", opted_out)
        r = client.get("/steps/ranges?range=W&user_id=michele&viewer=jeremiah", headers={"X-Internal-Secret": geo_main.INTERNAL_SECRET})
        assert r.status_code == 404

    def test_withheld_when_the_owner_has_opted_out(self, client, monkeypatch):
        async def opted_out():
            return {"users": [{"username": "michele", "enabled": False, "audience": "circle", "user_ids": []}]}

        monkeypatch.setattr(geo_main, "_fetch_activity_sharing", opted_out)
        r = client.get("/steps/ranges?range=W&user_id=michele&viewer=jeremiah", headers={"X-Internal-Secret": geo_main.INTERNAL_SECRET})
        assert r.status_code == 404

    def test_withheld_when_the_viewer_is_not_in_the_audience(self, client, monkeypatch):
        async def restricted():
            return {
                "users": [
                    {
                        "username": "michele",
                        "enabled": True,
                        "audience": "users",
                        "user_ids": ["someone-else"],
                    }
                ]
            }

        monkeypatch.setattr(geo_main, "_fetch_activity_sharing", restricted)
        r = client.get("/steps/ranges?range=W&user_id=michele&viewer=jeremiah", headers={"X-Internal-Secret": geo_main.INTERNAL_SECRET})
        assert r.status_code == 404

    def test_a_new_route_does_not_become_a_consent_bypass(self, client, monkeypatch):
        """Explicit: the 30-day cap on /steps is not a privacy control, so the
        wider route must apply exactly the same check, not a looser one."""
        seen = {}
        real = geo_main._require_may_view

        async def spy(viewer, target, is_admin=None):
            seen["called"] = (viewer, target, is_admin)
            # Bound before the patch, or this recurses into itself.
            return await real(viewer, target, is_admin)

        monkeypatch.setattr(geo_main, "_require_may_view", spy)
        client.get("/steps/ranges?range=Y&user_id=michele&viewer=jeremiah", headers={"X-Internal-Secret": geo_main.INTERNAL_SECRET})
        assert seen.get("called") is not None


class TestTimezoneSingleSource:
    def test_the_zone_is_configurable(self, monkeypatch):
        """One constant, not seven literals, so a household that moves can fix
        it without hunting."""
        import importlib

        monkeypatch.setenv("APP_TIMEZONE", "America/New_York")
        import services.geo.main as m

        importlib.reload(m)
        assert m.APP_TIMEZONE == "America/New_York"
        importlib.reload(m)

    def test_no_bare_zone_literals_remain(self):
        import inspect

        source = inspect.getsource(geo_main)
        assert 'ZoneInfo("America/' not in source
