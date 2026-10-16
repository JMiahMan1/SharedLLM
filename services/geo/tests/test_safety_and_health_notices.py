"""Geo's time-driven notices: long stops somewhere unknown, the daily step
goal, and the Sunday evening step summary."""
import fnmatch
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

import services.geo.main as geo
from services.geo import health_notices, safety

TZ = ZoneInfo(geo.APP_TIMEZONE)
HOME = {"entity_id": "zone.home", "name": "Home", "latitude": 33.1667, "longitude": -111.5646, "radius": 100.0}


class R:
    def __init__(self):
        self.kv, self.h, self.s = {}, {}, {}

    async def get(self, k):
        return self.kv.get(k)

    async def set(self, k, v, ex=None, nx=False):
        if nx and k in self.kv:
            return None
        self.kv[k] = v
        return True

    async def hget(self, k, f):
        return self.h.get(k, {}).get(f)

    async def hset(self, k, f, v):
        self.h.setdefault(k, {})[f] = v

    async def hgetall(self, k):
        return dict(self.h.get(k, {}))

    async def scan_iter(self, match):
        for k in list(self.kv) + list(self.h):
            if fnmatch.fnmatch(k, match):
                yield k


def at(y, m, d, hour):
    return datetime(y, m, d, hour, 0, tzinfo=TZ).timestamp()


@pytest.fixture
def sent(monkeypatch):
    out = []

    async def send(payload):
        out.append(payload)
        return True

    async def tracked(r):
        return ["jeremiah", "kate", "michele"]

    async def may_see(viewer, target):
        return viewer != "kate"

    async def place(lat, lon):
        return "Desert Ridge Mall"

    monkeypatch.setattr(geo, "_send_notice", send)
    monkeypatch.setattr(geo, "_tracked_users", tracked)
    monkeypatch.setattr(geo, "_viewer_may_see", may_see)
    monkeypatch.setattr(geo, "_resolve_place_name_cached", place)
    geo._zone_cache.update({"at": time.time() + 3600, "zones": [HOME]})
    yield out
    geo._zone_cache.update({"at": 0.0, "zones": []})


# ------------------------------------------------------------------ long stops


async def _stay(r, user, lat, lon, start, hours):
    for minutes in range(0, int(hours * 60) + 1, 15):
        await safety.note_fix(r, user, lat, lon, start + minutes * 60, geo._haversine_distance)


@pytest.mark.asyncio
async def test_two_hours_somewhere_unknown_tells_the_family_once(sent):
    r = R()
    start = at(2026, 10, 14, 10)
    await _stay(r, "michele", 33.6, -111.9, start, 2.1)
    assert await geo._sweep_long_stops(r, start + 2.2 * 3600) == 1
    assert [n["user"] for n in sent] == ["jeremiah"]  # not kate (no consent), not herself
    notice = sent[0]
    assert notice["category"] == "family_safety"
    assert notice["title"] == "Michele has been stopped for 2 h"
    assert "Desert Ridge Mall" in notice["body"]
    assert notice["data"]["url"] == "/wander?person=michele"
    assert await geo._sweep_long_stops(r, start + 3 * 3600) == 0, "at most once per stop"


@pytest.mark.asyncio
async def test_a_short_stop_or_a_known_place_says_nothing(sent):
    r = R()
    start = at(2026, 10, 14, 10)
    await _stay(r, "michele", 33.6, -111.9, start, 1)
    assert await geo._sweep_long_stops(r, start + 1.1 * 3600) == 0
    r = R()
    await _stay(r, "jeremiah", 33.1667, -111.5646, start, 5)  # home
    assert await geo._sweep_long_stops(r, start + 5.1 * 3600) == 0
    assert sent == []


@pytest.mark.asyncio
async def test_moving_on_starts_a_new_stop(sent):
    r = R()
    start = at(2026, 10, 14, 9)
    await _stay(r, "michele", 33.6, -111.9, start, 1.5)
    await _stay(r, "michele", 33.7, -111.9, start + 1.6 * 3600, 1)  # 11 km away
    assert await geo._sweep_long_stops(r, start + 2.7 * 3600) == 0


@pytest.mark.asyncio
async def test_long_stops_are_quiet_at_night(sent):
    r = R()
    start = at(2026, 10, 14, 21)
    await _stay(r, "michele", 33.6, -111.9, start, 3)
    assert await geo._sweep_long_stops(r, at(2026, 10, 14, 23) + 1800) == 0
    assert sent == []


def test_active_hours_can_be_all_day():
    assert safety.in_active_hours(23, "0-24")
    assert not safety.in_active_hours(23, "8-21")
    assert safety.in_active_hours(8, "8-21") and not safety.in_active_hours(21, "8-21")


# ------------------------------------------------------------ important places


@pytest.mark.asyncio
async def test_place_settings_are_stored_and_shown_on_the_zones():
    r = R()
    await safety.save_place_setting(r, "zone.school", True, ["Leo", "michele", " "])
    settings = await safety.place_settings(r)
    assert settings == {"zone.school": {"important": True, "notify": ["leo", "michele"]}}
    features = [{"properties": {"entity_id": "zone.school"}}, {"properties": {"entity_id": "zone.home"}}]
    safety.annotate_zone_features(features, settings)
    assert features[0]["properties"]["important"] is True
    assert features[1]["properties"] == {"entity_id": "zone.home", "important": False, "notify": []}


def test_place_settings_route_rejects_what_is_not_a_zone():
    from fastapi.testclient import TestClient

    client = TestClient(geo.app)
    resp = client.put("/place-settings/person.leo", json={"important": True},
                      headers={"X-Internal-Secret": geo.INTERNAL_SECRET})
    assert resp.status_code == 422


# ------------------------------------------------------------------- steps


@pytest.mark.asyncio
async def test_the_step_goal_is_announced_once_a_day(sent, monkeypatch):
    r = R()
    today = geo._today_date().isoformat()
    await r.set("geo:steps_goal:leo", "8000")
    await r.hset("geo:steps_src:leo:phone", today, 7000)
    assert await geo._store_fused_day(r, "leo", today) == 7000
    assert sent == []
    await r.hset("geo:steps_src:leo:phone", today, 8200)
    await geo._store_fused_day(r, "leo", today)
    await r.hset("geo:steps_src:leo:phone", today, 9000)
    await geo._store_fused_day(r, "leo", today)
    assert len(sent) == 1
    notice = sent[0]
    assert notice["user"] == "leo" and notice["category"] == "health"
    assert notice["body"] == "8,200 steps today, past your goal of 8,000."
    assert notice["data"]["url"] == "/fitness"


@pytest.mark.asyncio
async def test_a_past_day_reaching_the_goal_is_not_announced(sent):
    r = R()
    await r.hset("geo:steps_src:leo:phone", "2020-01-01", 20000)
    await geo._store_fused_day(r, "leo", "2020-01-01")
    assert sent == []


@pytest.mark.asyncio
async def test_the_weekly_summary_goes_out_on_sunday_evening_once(sent):
    r = R()
    # Sunday 18 Oct 2026; the week is Mon 12 to Sun 18.
    for day, steps in {"2026-10-12": 8000, "2026-10-14": 10000, "2026-10-18": 6000,
                       "2026-10-05": 9000, "2026-10-11": 3000, "2026-10-04": 50000}.items():
        await r.hset("geo:steps:leo", day, steps)
    await r.hset("geo:steps:kate", "2026-09-01", 4000)  # nothing in either week
    assert await geo._sweep_weekly_summaries(r, at(2026, 10, 18, 18)) == 0, "not before evening"
    assert await geo._sweep_weekly_summaries(r, at(2026, 10, 17, 20)) == 0, "Saturday"
    assert await geo._sweep_weekly_summaries(r, at(2026, 10, 18, 19)) == 1
    assert await geo._sweep_weekly_summaries(r, at(2026, 10, 18, 21)) == 0, "once a week"
    notice = sent[0]
    assert notice["user"] == "leo" and notice["category"] == "health"
    assert notice["body"] == "24,000 steps this week, 8,000 a day. Up 100% on last week (12,000)."


def test_weekly_text_without_a_last_week():
    assert health_notices.weekly_text(5000, 0, 1) == ("Your week in steps", "5,000 steps this week, 5,000 a day.")
    assert health_notices.weekly_text(900, 1000, 3)[1].endswith("Down 10% on last week (1,000).")
