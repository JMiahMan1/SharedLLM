"""A device's old-day counter is not filed under today."""
from datetime import datetime
from zoneinfo import ZoneInfo

from services.geo import main as geo_main
from services.geo.tests.test_step_hourly import HEADERS, client, fake  # noqa: F401

TZ = ZoneInfo("America/Phoenix")


def at(day: str, hour: int, minute: int = 0) -> float:
    return datetime.fromisoformat(f"{day}T{hour:02d}:{minute:02d}:00").replace(tzinfo=TZ).timestamp()


def report(steps, ts, hourly=None, source="watch"):
    body = {"steps": steps, "source": source, "timestamp": ts, "timezone": "America/Phoenix"}
    if hourly is not None:
        body["hourly"] = hourly
    return body


NOW = at("2026-10-16", 9, 30)


def test_steps_in_hours_that_have_not_come_yet_are_from_another_day():
    old = report(2429, NOW, [{"hour": 8, "steps": 400}, {"hour": 15, "steps": 900}])
    assert "15:00" in geo_main.stale_device_report(old, NOW)


def test_more_steps_than_possible_since_midnight():
    just_after_midnight = at("2026-10-16", 0, 5)
    assert geo_main.stale_device_report(report(2429, just_after_midnight), just_after_midnight)
    assert geo_main.stale_device_report(report(600, just_after_midnight), just_after_midnight) is None


def test_a_normal_morning_and_earlier_days_pass():
    assert geo_main.stale_device_report(report(1500, NOW, [{"hour": 8, "steps": 1500}]), NOW) is None
    # An earlier day is filed under that day, as before.
    assert geo_main.stale_device_report(report(9000, at("2026-10-11", 12), [{"hour": 20, "steps": 9000}]), NOW) is None


def test_the_phone_and_future_timestamps():
    assert geo_main.stale_device_report(report(2429, NOW, [{"hour": 15, "steps": 1}], source="phone"), NOW) is None
    assert "future" in geo_main.stale_device_report(report(10, NOW + 3600), NOW)


def test_an_ignored_report_stores_nothing_and_is_not_an_error(client, fake, monkeypatch):
    monkeypatch.setattr(geo_main.time, "time", lambda: NOW)
    body = {**report(2429, NOW, [{"hour": 15, "steps": 900}]), "user_id": "jeremiah"}
    resp = client.post("/steps", headers=HEADERS, json=body)
    assert resp.status_code == 200 and resp.json()["status"] == "ignored"
    assert not fake.hashes.get("geo:steps_src:jeremiah:watch")
