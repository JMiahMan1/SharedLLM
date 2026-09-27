"""Opt-in activity sharing: strict enforcement on summary and feed.

Nothing is visible cross-user unless the target opted in and the viewer is
inside the chosen audience. Failure paths return empty, never other people's
data.
"""
import os
from datetime import datetime
from zoneinfo import ZoneInfo

os.environ.setdefault("INTERNAL_SECRET", "test-secret")
os.environ.setdefault("FERNET_KEY", "bW9ja2VkLWtleS1mb3ItdGVzdGluZy1wdXJwb3NlcyE=")

import pytest
from fastapi import HTTPException

from services.config import INTERNAL_SECRET

TZ = ZoneInfo("America/Phoenix")


class FakeRedis:
    def __init__(self):
        self.hashes: dict[str, dict[str, str]] = {}
        self.strings: dict[str, str] = {}

    async def hget(self, key, field):
        return self.hashes.get(key, {}).get(field)

    async def hset(self, key, field=None, value=None, mapping=None):
        bucket = self.hashes.setdefault(key, {})
        if mapping:
            for k, v in mapping.items():
                bucket[str(k)] = str(v)
        if field is not None:
            bucket[str(field)] = str(value)
        return 1

    async def hgetall(self, key):
        return dict(self.hashes.get(key, {}))

    async def expire(self, key, seconds):
        return True

    async def get(self, key):
        return self.strings.get(key)

    async def set(self, key, value, ex=None):
        self.strings[key] = str(value)
        return True

    async def zrevrange(self, key, start, end):
        return []

    async def zrevrangebyscore(self, key, max_score, min_score):
        return []


@pytest.fixture
def rc():
    return FakeRedis()


def seed_day(rc: FakeRedis, user: str, steps: int) -> str:
    today = datetime.now(TZ).strftime("%Y-%m-%d")
    rc.hashes.setdefault(f"geo:steps:{user}", {})[today] = str(steps)
    return today


def share_row(user, enabled=True, audience="circle", user_ids=None, share=("totals",)):
    return {
        "username": user,
        "enabled": enabled,
        "audience": audience,
        "user_ids": list(user_ids or []),
        "share": list(share),
    }


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    import services.geo.main as geo

    async def fake_sharing():
        return {"users": []}

    monkeypatch.setattr(geo, "_fetch_activity_sharing", fake_sharing)


async def test_viewer_may_see_own_data_without_sharing(monkeypatch):
    import services.geo.main as geo

    assert await geo._viewer_may_see("jeremiah", "jeremiah") is True


async def test_viewer_blocked_when_target_not_opted_in(monkeypatch):
    import services.geo.main as geo

    async def fake_sharing():
        return {"users": []}

    monkeypatch.setattr(geo, "_fetch_activity_sharing", fake_sharing)
    assert await geo._viewer_may_see("jeremiah", "alice") is False


async def test_viewer_blocked_when_disabled_or_not_in_audience(monkeypatch):
    import services.geo.main as geo

    async def fake_sharing():
        return {
            "users": [
                share_row("alice", enabled=False),
                share_row("bob", audience="users", user_ids=["sam"]),
            ]
        }

    monkeypatch.setattr(geo, "_fetch_activity_sharing", fake_sharing)
    assert await geo._viewer_may_see("jeremiah", "alice") is False
    assert await geo._viewer_may_see("jeremiah", "bob") is False
    assert await geo._viewer_may_see("sam", "bob") is True
    assert await geo._viewer_may_see("jeremiah", "bob") is False


async def test_viewer_allowed_for_circle_audience(monkeypatch):
    import services.geo.main as geo

    async def fake_sharing():
        return {"users": [share_row("alice", audience="circle")]}

    monkeypatch.setattr(geo, "_fetch_activity_sharing", fake_sharing)
    assert await geo._viewer_may_see("anyone", "alice") is True


async def test_summary_own_data_always_allowed(rc, monkeypatch):
    import services.geo.main as geo

    seed_day(rc, "jeremiah", 4321)
    rc.hashes["geo:points:jeremiah"] = {"first_steps": "2026-09-20"}

    async def fake_get_redis():
        return rc

    monkeypatch.setattr(geo, "get_redis", fake_get_redis)
    out = await geo.get_activity_summary(
        user_id="jeremiah",
        window="today",
        viewer="jeremiah",
        x_internal_secret=INTERNAL_SECRET,
        query_secret=None,
    )
    assert out["status"] == "SUCCESS"
    assert out["steps_today"] == 4321
    assert out["steps_total"] == 4321
    assert out["points"] >= 1
    assert out["achievements_earned"] == 1


async def test_summary_cross_user_requires_opt_in(rc, monkeypatch):
    import services.geo.main as geo

    seed_day(rc, "alice", 1000)

    async def fake_get_redis():
        return rc

    monkeypatch.setattr(geo, "get_redis", fake_get_redis)
    with pytest.raises(HTTPException) as exc:
        await geo.get_activity_summary(
            user_id="alice",
            window="week",
            viewer="jeremiah",
            x_internal_secret=INTERNAL_SECRET,
            query_secret=None,
        )
    assert exc.value.status_code == 404


async def test_summary_cross_user_allowed_when_shared(rc, monkeypatch):
    import services.geo.main as geo

    seed_day(rc, "alice", 2500)

    async def fake_sharing():
        return {"users": [share_row("alice", audience="users", user_ids=["jeremiah"])]}

    async def fake_get_redis():
        return rc

    monkeypatch.setattr(geo, "_fetch_activity_sharing", fake_sharing)
    monkeypatch.setattr(geo, "get_redis", fake_get_redis)
    out = await geo.get_activity_summary(
        user_id="alice",
        window="week",
        viewer="jeremiah",
        x_internal_secret=INTERNAL_SECRET,
        query_secret=None,
    )
    assert out["steps_total"] == 2500


async def test_summary_requires_internal_secret(rc, monkeypatch):
    import services.geo.main as geo

    async def fake_get_redis():
        return rc

    monkeypatch.setattr(geo, "get_redis", fake_get_redis)
    with pytest.raises(HTTPException) as exc:
        await geo.get_activity_summary(
            user_id="jeremiah",
            window="week",
            viewer="jeremiah",
            x_internal_secret="wrong",
            query_secret=None,
        )
    assert exc.value.status_code == 403


async def test_feed_filters_disabled_and_non_audience_users(rc, monkeypatch):
    import services.geo.main as geo

    seed_day(rc, "alice", 1111)
    seed_day(rc, "bob", 2222)
    seed_day(rc, "carol", 3333)
    rc.hashes["geo:points:bob"] = {"first_steps": "2026-09-20", "goal_1": "2026-09-21"}

    async def fake_sharing():
        return {
            "users": [
                share_row("alice", audience="circle", share=("totals",)),
                share_row(
                    "bob",
                    audience="users",
                    user_ids=["jeremiah"],
                    share=("totals", "achievements"),
                ),
                share_row("carol", enabled=False, audience="circle"),
                share_row("dave", audience="users", user_ids=["sam"]),
            ]
        }

    async def fake_get_redis():
        return rc

    monkeypatch.setattr(geo, "_fetch_activity_sharing", fake_sharing)
    monkeypatch.setattr(geo, "get_redis", fake_get_redis)
    out = await geo.get_activity_feed(
        viewer="jeremiah",
        window="week",
        x_internal_secret=INTERNAL_SECRET,
        query_secret=None,
    )
    names = [u["username"] for u in out["users"]]
    assert names == ["alice", "bob"]  # carol disabled, dave different audience
    alice, bob = out["users"]
    assert alice["steps_total"] == 1111
    assert "points" not in alice  # achievements scope not shared
    assert bob["points"] == 4  # First Steps (1) + Goal Day (3)
    assert bob["achievements_earned"] == 2
    assert "workout_count" not in bob  # workouts scope not shared


async def test_feed_never_includes_the_viewer(rc, monkeypatch):
    import services.geo.main as geo

    seed_day(rc, "jeremiah", 999)

    async def fake_sharing():
        return {"users": [share_row("jeremiah", audience="circle")]}

    async def fake_get_redis():
        return rc

    monkeypatch.setattr(geo, "_fetch_activity_sharing", fake_sharing)
    monkeypatch.setattr(geo, "get_redis", fake_get_redis)
    out = await geo.get_activity_feed(
        viewer="jeremiah",
        window="week",
        x_internal_secret=INTERNAL_SECRET,
        query_secret=None,
    )
    assert out["users"] == []
