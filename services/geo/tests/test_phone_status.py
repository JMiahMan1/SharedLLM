"""The phone's charging state and next alarm, from the Jarvis app's location uploads.

The Jarvis app replaced Home Assistant's companion app as every phone's sensor
source, so homehub's battery_state, charger_type, is_charging and next_alarm
sensors are filled from what geo's /people says. These check that the new
fields are stored, exposed next to battery_level, survive a fix that is not a
breadcrumb, and that an older app (which sends none of them) changes nothing.
"""
import json
import time

import pytest
from fastapi.testclient import TestClient

import services.geo.main as geo
from services.config import INTERNAL_SECRET
from services.geo.tests.test_trips import FakeRedis

SECRET = {"X-Internal-Secret": INTERNAL_SECRET}
ALARM_MS = 1_900_000_000_000


class _Redis(FakeRedis):
    async def scan_iter(self, match):
        for k in await self.keys(match):
            yield k


@pytest.fixture
def r(monkeypatch):
    fake = _Redis()

    async def _get():
        return fake

    trips: list = []

    async def _trip(*a, **kw):
        trips.append(a)

    async def _no_presence(*a, **kw):
        return None

    async def _no_sharing():
        return {}

    monkeypatch.setattr(geo, "get_redis", _get)
    monkeypatch.setattr(geo, "process_trip_point", _trip)
    monkeypatch.setattr(geo, "_safe_presence_transition", _no_presence)
    monkeypatch.setattr(geo, "_fetch_activity_sharing", _no_sharing)
    fake.trips = trips
    return fake


@pytest.fixture
def client():
    return TestClient(geo.app)


def _record(client, t, **fields):
    body = {"latitude": 10.0, "longitude": 20.0, "gps_accuracy": 8, "battery": 64, "timestamp": t, **fields}
    resp = client.post("/people/sam/record", headers=SECRET, json=body)
    assert resp.status_code == 200, resp.text
    return resp


def _people(client):
    resp = client.get("/people", params={"source": "jarvis"}, headers=SECRET)
    assert resp.status_code == 200
    return {f["properties"]["entity_id"]: f["properties"] for f in resp.json()["features"]}


def _points(r):
    return [json.loads(v) for v, _ in r.zsets.get("geo:history:sam", [])]


def test_status_is_stored_on_the_point_and_shown_next_to_battery(client, r):
    now = time.time()
    _record(client, now, charging=True, battery_state="charging", charger_type="wireless",
            next_alarm_ms=ALARM_MS, next_alarm_package="com.example.clock")
    (point,) = _points(r)
    assert point["bst"] == "charging" and point["plg"] == "wireless" and point["ich"] is True
    assert point["alm"] == ALARM_MS and point["alp"] == "com.example.clock"
    sam = _people(client)["person.sam"]
    assert sam["battery"] == 64
    assert sam["battery_state"] == "charging"
    assert sam["charger_type"] == "wireless"
    assert sam["is_charging"] is True
    assert sam["next_alarm_ms"] == ALARM_MS
    assert sam["next_alarm"] == "2030-03-17T17:46:40+00:00"
    assert sam["next_alarm_package"] == "com.example.clock"


def test_an_older_app_sends_none_and_nothing_is_guessed(client, r):
    _record(client, time.time())
    (point,) = _points(r)
    assert not {"bst", "plg", "ich", "alm", "alp"} & set(point)
    sam = _people(client)["person.sam"]
    for key in geo.PHONE_STATUS_ATTRS:
        assert sam[key] is None


def test_no_alarm_set_is_zero_not_unknown(client, r):
    _record(client, time.time(), battery_state="discharging", charger_type="none", next_alarm_ms=0,
            next_alarm_package="com.example.clock")
    sam = _people(client)["person.sam"]
    assert sam["next_alarm_ms"] == 0 and sam["next_alarm"] is None
    assert sam["next_alarm_package"] is None, "no alarm, no app that set it"
    assert sam["is_charging"] is False


def test_full_on_the_charger_is_charging_and_unknown_values_are_dropped(client, r):
    _record(client, time.time(), battery_state="FULL", charger_type="ac")
    assert _people(client)["person.sam"]["is_charging"] is True
    _record(client, time.time() + 1, battery_state="sparkling", charger_type="solar", charging=False)
    sam = _people(client)["person.sam"]
    assert sam["battery_state"] is None and sam["charger_type"] is None
    assert sam["is_charging"] is False, "an older build's charging flag still counts"


def test_a_resent_fix_updates_the_breadcrumb_in_place(client, r):
    t = time.time()
    _record(client, t, battery_state="discharging", charger_type="none")
    _record(client, t, battery_state="charging", charger_type="usb")  # plugged in: the held fix again
    points = _points(r)
    assert len(points) == 1, "the same fix is not a second breadcrumb"
    assert points[0]["bst"] == "charging"
    assert len(r.trips) == 1, "and not a second trip point"
    assert _people(client)["person.sam"]["charger_type"] == "usb"


def test_a_coarse_fix_still_reports_the_charger(client, r):
    t = time.time()
    _record(client, t, battery_state="discharging", charger_type="none")
    _record(client, t + 60, gps_accuracy=900, battery_state="charging", charger_type="ac")
    assert len(_points(r)) == 1, "a 900 m fix is not a breadcrumb"
    sam = _people(client)["person.sam"]
    assert sam["battery_state"] == "charging" and sam["charger_type"] == "ac"


def test_an_older_queued_report_does_not_overwrite_a_newer_status(client, r):
    t = time.time()
    _record(client, t, battery_state="charging", charger_type="ac")
    _record(client, t - 600, battery_state="discharging", charger_type="none")  # from the offline queue
    sam = _people(client)["person.sam"]
    assert sam["battery_state"] == "charging"


def test_malformed_fields_are_logged_by_name_not_silently_dropped(caplog):
    with caplog.at_level("WARNING"):
        assert geo.phone_status(battery_state="sparkling", charger_type="solar", next_alarm_ms=-5) == {}
    text = caplog.text
    assert "battery_state 'sparkling'" in text and "charger_type 'solar'" in text and "next_alarm_ms -5" in text


def test_phone_status_helper():
    assert geo.phone_status() == {}
    assert geo.phone_status(charging=True) == {"ich": True}
    assert geo.phone_status(battery_state="not_charging") == {"bst": "not_charging", "ich": False}
