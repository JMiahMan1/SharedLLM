from datetime import datetime
from zoneinfo import ZoneInfo

from services.common.activity_recommendations import recommend_activity

TZ = "America/Phoenix"


def _at(hour, minute=0, day=23):
    return int(datetime(2026, 10, day, hour, minute, tzinfo=ZoneInfo(TZ)).timestamp() * 1000)


def _day(day, last_hour, hours_on=2):
    return {"day": f"2026-10-{day}", "millis": hours_on * 3600000, "last_use": _at(last_hour, day=day)}


def test_late_nights_suggest_a_wind_down_in_the_household_time():
    rec = recommend_activity([_day(20, 23), _day(21, 22), _day(22, 20)], TZ)
    assert rec["theme"] == "late nights" and rec["kind"] == "activity"
    assert rec["matched_on"] == "late nights on the phone this week"  # no numbers repeated back


def test_after_midnight_is_still_a_late_night():
    rec = recommend_activity([_day(20, 1), _day(21, 0)], TZ)
    assert rec["theme"] == "late nights"


def test_long_days_suggest_time_outside():
    rec = recommend_activity([_day(d, 19, hours_on=5) for d in (19, 20, 21)], TZ)
    assert rec["theme"] == "long days"


def test_an_ordinary_week_gets_nothing():
    assert recommend_activity([_day(d, 20, hours_on=2) for d in (19, 20, 21, 22)], TZ) is None
    assert recommend_activity([], TZ) is None
