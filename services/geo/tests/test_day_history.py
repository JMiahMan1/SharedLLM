"""A person's day: the path, where they stayed, and their trips in order."""
import json
from datetime import date

import pytest
from fastapi.testclient import TestClient

import services.geo.main as geo
from services.geo import day_history

HOME = {"entity_id": "zone.home", "name": "Home", "latitude": 39.7392, "longitude": -104.9903, "radius": 100.0}


def _walk(t0, lat, lon, minutes, every=60):
    return [{"t": t0 + i * every, "lat": lat + i * 1e-6, "lon": lon} for i in range(int(minutes * 60 / every) + 1)]


def test_a_long_stay_is_a_stop_and_driving_past_is_not():
    t0 = 1_700_000_000
    home = _walk(t0, 39.7392, -104.9903, 60)                       # an hour at home
    drive = [{"t": t0 + 3700 + i * 30, "lat": 39.7425 + i * 0.01, "lon": -104.9857} for i in range(10)]
    school = _walk(t0 + 4100, 39.8725, -104.9857, 25)                  # 25 min at school
    found = day_history.stops(home + drive + school)
    assert len(found) == 2
    assert found[0]["since"] == t0 and found[0]["until"] == t0 + 3600
    assert abs(found[1]["lat"] - 39.8725) < 0.001


def test_a_short_stop_is_not_on_the_timeline():
    assert day_history.stops(_walk(0, 39.5725, -104.4257, 5)) == []


def test_the_path_is_thinned_but_keeps_the_end():
    points = [{"t": i, "lat": 39.5725, "lon": -104.4257} for i in range(2001)]
    thin = day_history.thin(points, 800)
    assert len(thin) <= 801 and thin[-1] is points[-1]


def test_the_day_is_the_local_day():
    start, end = day_history.day_bounds(date(2026, 10, 31), "Etc/GMT+7")
    assert end - start == 86400
    assert start == 1793430000  # 2026-10-31 00:00 at UTC-7


class R:
    def __init__(self, history, trips):
        self.history, self.trips = history, trips

    async def zrangebyscore(self, key, lo, hi):
        if key.startswith("geo:history:"):
            return [p for t, p in self.history if lo <= t <= hi]
        return [tid for t, tid in self.trips if lo <= t <= hi]

    async def get(self, key):
        trip = {"id": "t1", "start_time": START + 4000, "end_time": START + 4600, "distance_miles": 3.2, "user_id": "leo"}
        return {"geo:trip:t1": json.dumps(trip)}.get(key)


START, _ = day_history.day_bounds(date(2026, 10, 31), "Etc/GMT+7")


@pytest.fixture
def client(monkeypatch):
    history = [(p["t"], json.dumps(p)) for p in _walk(START + 600, 39.7392, -104.9903, 50)]
    r = R(history, [(START + 4000, "t1")])

    async def redis():
        return r

    async def zones():
        return [HOME]

    async def may_view(viewer, target, is_admin=None):
        if viewer == "stranger":
            raise geo.HTTPException(status_code=404, detail="Not found")
        return target

    monkeypatch.setattr(geo, "get_redis", redis)
    monkeypatch.setattr(geo, "_zones_cached", zones)
    monkeypatch.setattr(geo, "_require_may_view", may_view)
    monkeypatch.setattr(geo, "_is_phantom_trip", lambda trip: False)
    return TestClient(geo.app)


def test_the_day_route_names_stops_by_place_and_orders_the_timeline(client):
    resp = client.get("/people/leo/day", params={"day": "2026-10-31", "viewer": "dad"}, headers={"X-Internal-Secret": "test-secret"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["stops"][0]["place"] == "Home"
    assert [e["kind"] for e in body["timeline"]] == ["stop", "trip"]
    assert body["timeline"][1]["distance_miles"] == 3.2
    assert len(body["points"]) == 51


def test_someone_who_may_not_see_the_person_gets_nothing(client):
    resp = client.get("/people/leo/day", params={"day": "2026-10-31", "viewer": "stranger"}, headers={"X-Internal-Secret": "test-secret"})
    assert resp.status_code == 404


def test_a_spot_is_named_by_the_family_place_first(client, monkeypatch):
    async def street(lat, lon):
        return "123 Main St"

    monkeypatch.setattr(geo, "_resolve_place_name_cached", street)
    headers = {"X-Internal-Secret": "test-secret"}
    assert client.get("/place-name", params={"lat": 39.7392, "lon": -104.9903}, headers=headers).json() == {"name": "Home", "zone": "zone.home"}
    assert client.get("/place-name", params={"lat": 40.0725, "lon": -105.3257}, headers=headers).json() == {"name": "123 Main St", "zone": None}
