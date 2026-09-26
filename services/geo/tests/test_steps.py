"""Step-bucket attribution tests.

These cover the midnight/rollover rules that determine which local day a
pedometer reading belongs to, including the case that caused an inflated count
after a day with no syncs.
"""
import os
import time
from datetime import datetime
from zoneinfo import ZoneInfo

os.environ.setdefault("INTERNAL_SECRET", "test-secret")
os.environ.setdefault("FERNET_KEY", "bW9ja2VkLWtleS1mb3ItdGVzdGluZy1wdXJwb3NlcyE=")

import pytest

TZ = ZoneInfo("America/Phoenix")


class FakeRedis:
    def __init__(self):
        self.hashes: dict[str, dict[str, str]] = {}
        self.strings: dict[str, str] = {}

    async def hget(self, key, field):
        return self.hashes.get(key, {}).get(field)

    async def hset(self, key, field, value):
        self.hashes.setdefault(key, {})[field] = str(value)
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


@pytest.fixture
def rc():
    return FakeRedis()


def ts_for(day: str) -> float:
    """Midday (local) epoch for a YYYY-MM-DD day, avoiding edge cases."""
    dt = datetime.fromisoformat(f"{day}T12:00:00").replace(tzinfo=TZ)
    return dt.timestamp()


async def test_reading_is_bucketed_by_its_own_timestamp(rc, monkeypatch):
    """A late-arriving reading must land on the day it was taken, not today.

    The plugin used to hand the server a value that could span an unreported
    day; as long as the timestamp is right, late posts cannot seed today.
    """
    import services.geo.main as geo

    yesterday = "2026-09-25"
    await geo._record_daily_steps(rc, "jeremiah", steps=1234, timestamp=ts_for(yesterday))

    buckets = rc.hashes["geo:steps:jeremiah"]
    assert buckets[yesterday] == "1234"
    today = datetime.now(TZ).strftime("%Y-%m-%d")
    assert today not in buckets


async def test_cumulative_readings_keep_the_max_per_day(rc):
    import services.geo.main as geo

    day = "2026-09-25"
    await geo._record_daily_steps(rc, "jeremiah", steps=800, timestamp=ts_for(day))
    await geo._record_daily_steps(rc, "jeremiah", steps=1200, timestamp=ts_for(day))
    # A reboot makes the raw counter smaller; it must not reduce the day
    await geo._record_daily_steps(rc, "jeremiah", steps=40, timestamp=ts_for(day))

    assert rc.hashes["geo:steps:jeremiah"][day] == "1200"


async def test_a_bad_reading_is_ignored(rc):
    import services.geo.main as geo

    day = "2026-09-25"
    await geo._record_daily_steps(rc, "jeremiah", steps=-5, timestamp=ts_for(day))
    await geo._record_daily_steps(rc, "jeremiah", steps=500000, timestamp=ts_for(day))
    assert not rc.hashes.get("geo:steps:jeremiah")


async def test_step_goal_round_trip(rc, monkeypatch):
    import services.geo.main as geo

    # Default when unset
    assert await geo._get_step_goal(rc, "jeremiah") == geo.DEFAULT_STEP_GOAL

    rc.strings["geo:steps_goal:jeremiah"] = "12000"
    assert await geo._get_step_goal(rc, "jeremiah") == 12000
