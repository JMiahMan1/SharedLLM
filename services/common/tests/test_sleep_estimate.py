"""Sleep estimated from screen-on stretches across a person's devices."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from services.common.sleep_estimate import estimate_night, night_window

TZ = "America/Phoenix"  # no DST, so the arithmetic below stays readable


def ms(text: str) -> int:
    return int(datetime.fromisoformat(text).replace(tzinfo=ZoneInfo(TZ)).timestamp() * 1000)


def span(a: str, b: str) -> list[int]:
    return [ms(a), ms(b)]


def test_window_is_six_pm_to_noon():
    lo, hi = night_window("2026-10-21", TZ)
    assert lo == ms("2026-10-20T18:00") and hi == ms("2026-10-21T12:00")


def test_longest_quiet_stretch_is_the_night():
    phone = {"intervals": [
        span("2026-10-20T19:00", "2026-10-20T19:30"),
        span("2026-10-20T22:10", "2026-10-20T22:40"),
        span("2026-10-21T06:30", "2026-10-21T06:45"),
        span("2026-10-21T08:00", "2026-10-21T08:20"),
    ]}
    out = estimate_night([phone], "2026-10-21", TZ)
    assert out["bedtime"] == "2026-10-20T22:40-07:00"
    assert out["wake_time"] == "2026-10-21T06:30-07:00"
    assert out["duration_minutes"] == 470
    assert out["wakeups"] == 0 and out["devices"] == 1


def test_a_brief_check_at_night_is_a_wakeup_not_the_end():
    phone = {"intervals": [
        span("2026-10-20T22:00", "2026-10-20T22:30"),
        span("2026-10-21T03:00", "2026-10-21T03:04"),
        span("2026-10-21T06:30", "2026-10-21T07:00"),
    ]}
    out = estimate_night([phone], "2026-10-21", TZ)
    assert out["bedtime"] == "2026-10-20T22:30-07:00"
    assert out["wake_time"] == "2026-10-21T06:30-07:00"
    assert out["wakeups"] == 1 and out["awake_minutes"] == 4
    assert out["duration_minutes"] == 480 - 4


def test_devices_are_joined():
    phone = {"intervals": [span("2026-10-20T21:00", "2026-10-20T21:30"), span("2026-10-21T06:00", "2026-10-21T06:10")]}
    tablet = {"intervals": [span("2026-10-20T23:00", "2026-10-20T23:45")]}
    out = estimate_night([phone, tablet], "2026-10-21", TZ)
    assert out["bedtime"] == "2026-10-20T23:45-07:00"
    assert out["wake_time"] == "2026-10-21T06:00-07:00"
    assert out["devices"] == 2


def test_use_outside_the_window_is_ignored():
    phone = {"intervals": [
        span("2026-10-20T10:00", "2026-10-20T11:00"),  # the morning before
        span("2026-10-20T22:00", "2026-10-20T22:30"),
        span("2026-10-21T06:30", "2026-10-21T07:00"),
        span("2026-10-21T15:00", "2026-10-21T16:00"),  # the afternoon after
    ]}
    out = estimate_night([phone], "2026-10-21", TZ)
    assert out["bedtime"] == "2026-10-20T22:30-07:00"


def test_not_enough_data_returns_none():
    assert estimate_night([], "2026-10-21", TZ) is None
    one_block = {"intervals": [span("2026-10-20T22:00", "2026-10-20T22:30")]}
    assert estimate_night([one_block], "2026-10-21", TZ) is None
    busy = {"intervals": [span(f"2026-10-21T0{h}:00", f"2026-10-21T0{h}:30") for h in range(0, 9)]}
    assert estimate_night([busy], "2026-10-21", TZ) is None


def test_bad_intervals_are_skipped():
    phone = {"intervals": [
        ["x", 1], [5], None,
        span("2026-10-20T22:00", "2026-10-20T22:30"),
        span("2026-10-21T06:30", "2026-10-21T07:00"),
    ]}
    assert estimate_night([phone], "2026-10-21", TZ)["duration_minutes"] == 480
