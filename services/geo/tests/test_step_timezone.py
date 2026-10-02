"""Step readings must be filed under the *phone's* day, not the server's.

The bug: the day bucket was computed in a hardcoded server zone, so a user in
another zone had their late-evening walk filed under the wrong day. Because a
stored day keeps the maximum of everything filed under it, that wrong day could
never be corrected -- a user who travelled permanently saw a permanently wrong
history. The max guard is right against a stale upload and wrong against a
re-bucketing, so the zone is now recorded per day to tell the two apart.
"""
import pytest

import services.geo.main as geo_main


def app_timezone() -> str:
    """Read the fallback live.

    Captured at import time it goes stale: another test rebinds
    APP_TIMEZONE to prove the day boundary is configurable, and then this
    module's module-level constant describes a value nobody is using.
    """
    return geo_main.APP_TIMEZONE


class FakeRedis:
    """Just the hash operations _record_daily_steps and its helpers use."""

    def __init__(self, initial=None):
        self.hashes = {}
        for key, mapping in (initial or {}).items():
            self.hashes[key] = dict(mapping)

    async def hget(self, key, field):
        return self.hashes.get(key, {}).get(field)

    async def hset(self, key, field, value):
        self.hashes.setdefault(key, {})[field] = str(value)

    async def hgetall(self, key):
        return dict(self.hashes.get(key, {}))


@pytest.fixture(name="redis")
def redis_fixture(monkeypatch):
    fake = FakeRedis()

    async def get_redis():
        return fake

    monkeypatch.setattr(geo_main, "get_redis", get_redis)
    return fake


def test_resolve_reporting_tz_uses_the_reported_zone():
    assert geo_main._resolve_reporting_tz("Europe/London") == "Europe/London"


def test_resolve_reporting_tz_falls_back_when_nothing_was_sent():
    """An older client sends no timezone, so this must not raise."""
    assert geo_main._resolve_reporting_tz(None) == app_timezone()
    assert geo_main._resolve_reporting_tz("") == app_timezone()
    assert geo_main._resolve_reporting_tz("   ") == app_timezone()


def test_resolve_reporting_tz_rejects_an_unusable_zone():
    """An arbitrary string would raise from ZoneInfo and 500 the upload."""
    assert geo_main._resolve_reporting_tz("Not/AZone") == app_timezone()
    assert geo_main._resolve_reporting_tz("Mars/Olympus") == app_timezone()


@pytest.mark.anyio
async def test_a_late_evening_read_is_filed_under_the_phones_day(redis):
    """07:00 in Kiritimati (UTC+14) is still the previous day in America/Phoenix.

    Filing it under the server's day is the whole bug: the user sees an empty
    "today" and a day that looks impossibly large. Kiritimati is used because
    Tokyo (UTC+9) is only 16h from Phoenix, so a 23:30 walk there still lands
    on the same Phoenix day, so a *morning* walk is what crosses the boundary.
    """
    from datetime import datetime
    from zoneinfo import ZoneInfo

    ts = datetime(2026, 10, 1, 7, 0, tzinfo=ZoneInfo("Pacific/Kiritimati")).timestamp()
    await geo_main._record_daily_steps(redis, "u1", 4321, ts, "phone", "Pacific/Kiritimati")

    local_day = datetime.fromtimestamp(ts, ZoneInfo("Pacific/Kiritimati")).strftime("%Y-%m-%d")
    phx_day = datetime.fromtimestamp(ts, ZoneInfo(app_timezone())).strftime("%Y-%m-%d")
    assert local_day != phx_day, "fixture must straddle the server's day boundary"

    stored = await redis.hget("geo:steps_src:u1:phone", local_day)
    assert stored == "4321", f"expected the step count under {local_day}"
    assert await redis.hget("geo:steps_src:u1:phone", phx_day) is None


@pytest.mark.anyio
async def test_the_zone_is_recorded_alongside_the_day(redis):
    await geo_main._record_daily_steps(redis, "u1", 100, 1_700_000_000, "phone", "Europe/London")
    day = list(redis.hashes["geo:steps_src:u1:phone"].keys())[0]
    assert await redis.hget("geo:steps_tz:u1:phone", day) == "Europe/London"


@pytest.mark.anyio
async def test_a_stale_lower_reading_cannot_lower_a_stored_day(redis):
    """The max guard must still hold within one zone -- it is what stops a
    late, stale upload from wiping out a real total."""
    await geo_main._record_daily_steps(redis, "u1", 5000, 1_700_000_000, "phone", "Europe/London")
    await geo_main._record_daily_steps(redis, "u1", 10, 1_700_000_000, "phone", "Europe/London")
    day = list(redis.hashes["geo:steps_src:u1:phone"].keys())[0]
    assert await redis.hget("geo:steps_src:u1:phone", day) == "5000"


@pytest.mark.anyio
async def test_a_day_filed_in_the_wrong_zone_can_be_corrected(redis):
    """This is the case the max guard alone made permanent.

    A reading arrives already bucketed by the server, then the client starts
    sending its own zone. The day has to be re-bucketed and the corrected
    value accepted even though it is lower -- otherwise the wrong-day total
    sticks forever.
    """
    from datetime import datetime
    from zoneinfo import ZoneInfo

    ts = datetime(2026, 10, 1, 7, 0, tzinfo=ZoneInfo("Pacific/Kiritimati")).timestamp()
    phx_day = datetime.fromtimestamp(ts, ZoneInfo(app_timezone())).strftime("%Y-%m-%d")

    # Pretend an earlier client already filed a large total under the server day.
    await redis.hset("geo:steps_src:u1:phone", phx_day, "99999")
    await redis.hset("geo:steps_tz:u1:phone", phx_day, app_timezone())

    # Now the phone reports its own zone; its reading lands on its own day.
    await geo_main._record_daily_steps(redis, "u1", 4321, ts, "phone", "Pacific/Kiritimati")
    jst_day = datetime.fromtimestamp(ts, ZoneInfo("Pacific/Kiritimati")).strftime("%Y-%m-%d")
    assert await redis.hget("geo:steps_src:u1:phone", jst_day) == "4321"


@pytest.mark.anyio
async def test_a_same_zone_rerun_still_cannot_lower_the_day(redis):
    """Distinguishing a re-bucketing from a stale duplicate is the whole point
    of recording the zone: same zone means the max guard still applies."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    ts = datetime(2026, 10, 1, 12, 0, tzinfo=ZoneInfo("Europe/London")).timestamp()
    await geo_main._record_daily_steps(redis, "u1", 8000, ts, "phone", "Europe/London")
    await geo_main._record_daily_steps(redis, "u1", 5, ts, "phone", "Europe/London")
    day = list(redis.hashes["geo:steps_src:u1:phone"].keys())[0]
    assert await redis.hget("geo:steps_src:u1:phone", day) == "8000"


@pytest.fixture
def anyio_backend():
    return "asyncio"
