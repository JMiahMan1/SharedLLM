"""Driving events from the phone and the driver report built from them."""
import json
import time

from fastapi.testclient import TestClient

import services.geo.main as geo
from services.geo import driving

HEADERS = {"X-Internal-Secret": "test-secret"}


class R:
    def __init__(self):
        self.z: dict[str, dict[str, float]] = {}
        self.kv: dict[str, str] = {}

    async def zadd(self, key, mapping):
        self.z.setdefault(key, {}).update(mapping)

    async def zremrangebyscore(self, key, lo, hi):
        for m, s in list(self.z.get(key, {}).items()):
            if lo <= s <= hi:
                del self.z[key][m]

    async def zrangebyscore(self, key, lo, hi):
        return [m for m, s in sorted(self.z.get(key, {}).items(), key=lambda x: x[1]) if lo <= s <= hi]

    async def get(self, key):
        return self.kv.get(key)


def test_only_known_events_with_a_sane_time_are_kept():
    now_ms = time.time() * 1000
    assert driving.clean_event("hard_brake", {"at": now_ms, "from_mph": 41.26, "to_mph": 22, "lat": 33.1234567})["from_mph"] == 41.3
    assert driving.clean_event("donut", {"at": now_ms}) is None
    assert driving.clean_event("hard_brake", {"at": now_ms + 3_600_000}) is None  # the future
    assert driving.clean_event("hard_brake", {}) is None


def test_the_report_counts_events_against_the_trips_they_fall_in():
    t0 = 1_793_000_000.0
    trips = [
        {"id": "a", "start_time": t0, "end_time": t0 + 900, "distance_miles": 8.0, "duration_seconds": 900, "top_speed_mph": 61},
        {"id": "b", "start_time": t0 + 5000, "end_time": t0 + 5600, "distance_miles": 3.5, "duration_seconds": 600, "top_speed_mph": 84},
        {"id": "w", "start_time": t0 + 9000, "end_time": t0 + 9600, "distance_miles": 1.0, "activity_type": "walking"},
    ]
    events = [
        {"event": "hard_brake", "t": t0 + 100},
        {"event": "hard_brake", "t": t0 + 400},
        {"event": "high_speed", "t": t0 + 5200},
        {"event": "phone_use", "t": t0 + 5300, "duration_s": 90},
    ]
    rep = driving.report(trips, events, 7)
    assert (rep["trips"], rep["miles"], rep["drive_minutes"], rep["top_speed_mph"]) == (2, 11.5, 25, 84.0)
    assert rep["events"]["hard_brake"] == 2 and rep["events"]["phone_use_min"] == 1.5
    by_id = {t["id"]: t["events"] for t in rep["per_trip"]}
    assert by_id["a"]["hard_brake"] == 2 and by_id["a"]["high_speed"] == 0
    assert by_id["b"]["high_speed"] == 1 and by_id["b"]["phone_use"] == 1


def test_events_are_stored_and_reported_through_the_routes(monkeypatch):
    r = R()

    async def redis():
        return r

    async def may_view(viewer, target, is_admin=None):
        return target

    monkeypatch.setattr(geo, "get_redis", redis)
    monkeypatch.setattr(geo, "_require_may_view", may_view)
    client = TestClient(geo.app)
    now = time.time()
    r.kv["geo:trip:t1"] = json.dumps({"id": "t1", "start_time": now - 600, "end_time": now - 60, "distance_miles": 4.0,
                                     "duration_seconds": 540, "top_speed_mph": 52})
    r.z["geo:trips:user:leo"] = {"t1": now - 600}
    ok = client.post("/driving-events", json={"user": "leo", "event": "rapid_accel", "detail": {"at": (now - 300) * 1000}}, headers=HEADERS)
    assert ok.status_code == 200
    assert client.post("/driving-events", json={"user": "leo", "event": "nap", "detail": {}}, headers=HEADERS).status_code == 422
    rep = client.get("/people/leo/driving", params={"viewer": "dad"}, headers=HEADERS).json()
    assert rep["trips"] == 1 and rep["events"]["rapid_accel"] == 1
    assert rep["per_trip"][0]["events"]["rapid_accel"] == 1
