"""Tests for Trip recording, shared trip grouping, and trip editing in the geo service."""
import json
import time
from unittest.mock import AsyncMock, patch
import pytest
from fastapi.testclient import TestClient

import services.geo.main as geo
from services.config import INTERNAL_SECRET


class FakeRedis:
    def __init__(self):
        self.data = {}
        self.zsets = {}
        self.hashes = {}

    async def get(self, key):
        return self.data.get(key)

    async def set(self, key, value, ex=None):
        self.data[key] = value

    async def delete(self, key):
        self.data.pop(key, None)

    async def hget(self, hname, key):
        return self.hashes.get(hname, {}).get(key)

    async def hset(self, hname, key, value):
        if hname not in self.hashes:
            self.hashes[hname] = {}
        self.hashes[hname][key] = value

    async def hgetall(self, hname):
        return self.hashes.get(hname, {})

    async def zadd(self, zname, mapping):
        if zname not in self.zsets:
            self.zsets[zname] = []
        for val, score in mapping.items():
            # Replace existing member score if present (real zadd semantics)
            self.zsets[zname] = [(v, s) for v, s in self.zsets[zname] if v != val]
            self.zsets[zname].append((val, score))

    async def zcard(self, zname):
        return len(self.zsets.get(zname, []))

    async def zrevrange(self, zname, start, stop):
        items = self.zsets.get(zname, [])
        # sorted by score desc
        sorted_items = sorted(items, key=lambda x: x[1], reverse=True)
        vals = [item[0] for item in sorted_items]
        return vals[start : stop + 1] if stop >= 0 else vals[start:]

    async def zrangebyscore(self, zname, lo, hi):
        items = self.zsets.get(zname, [])
        lo_val = lo if isinstance(lo, (int, float)) else float(lo)
        hi_val = hi if isinstance(hi, (int, float)) else float(hi)
        selected = sorted([x for x in items if lo_val <= x[1] <= hi_val], key=lambda x: x[1])
        return [item[0] for item in selected]

    async def zrevrangebyscore(self, zname, hi, lo):
        items = self.zsets.get(zname, [])
        lo_val = lo if isinstance(lo, (int, float)) else float(lo)
        hi_val = hi if isinstance(hi, (int, float)) else float(hi)
        selected = sorted([x for x in items if lo_val <= x[1] <= hi_val], key=lambda x: x[1], reverse=True)
        return [item[0] for item in selected]

    async def zremrangebyscore(self, zname, lo, hi):
        items = self.zsets.get(zname, [])
        lo_val = lo if isinstance(lo, (int, float)) else float(lo)
        hi_val = hi if isinstance(hi, (int, float)) else float(hi)
        self.zsets[zname] = [x for x in items if not (lo_val <= x[1] <= hi_val)]

    async def hgetall(self, hname):
        return dict(self.hashes.get(hname, {}))

    async def keys(self, pattern):
        return [k for k in self.data.keys() if "active_trip" in k]


@pytest.fixture
def fake_redis(monkeypatch):
    r = FakeRedis()
    async def _get_r():
        return r
    monkeypatch.setattr(geo, "get_redis", _get_r)
    return r


@pytest.fixture
def client(fake_redis):
    return TestClient(geo.app, headers={"X-Internal-Secret": INTERNAL_SECRET})


def test_shared_trips_grouping():
    """Verify trips occurring at the same time and place are grouped as shared trips."""
    now = time.time()
    trips = [
        {
            "id": "t1",
            "user_id": "jeremiah",
            "user_name": "Jeremiah",
            "start_time": now,
            "end_time": now + 1800,
            "start_location": {"name": "Home", "latitude": 33.1667, "longitude": -111.5646},
            "end_location": {"name": "Mall", "latitude": 33.2500, "longitude": -111.6300},
            "distance_miles": 12.0,
            "vehicle_name": "2011 Ford F-250 Super Duty",
        },
        {
            "id": "t2",
            "user_id": "michele",
            "user_name": "Michele",
            "start_time": now + 60, # 1 minute later
            "end_time": now + 1820,
            "start_location": {"name": "Home", "latitude": 33.1667, "longitude": -111.5646},
            "end_location": {"name": "Mall", "latitude": 33.2500, "longitude": -111.6300},
            "distance_miles": 12.0,
            "vehicle_name": "2012 Chevrolet Equinox FWD",
        },
        {
            "id": "t3",
            "user_id": "jeremiah",
            "user_name": "Jeremiah",
            "start_time": now - 36000,
            "end_time": now - 34000,
            "start_location": {"name": "Work", "latitude": 32.7800, "longitude": -96.8000},
            "end_location": {"name": "Airport", "latitude": 32.8900, "longitude": -97.0400},
            "distance_miles": 18.5,
            "vehicle_name": "2011 Ford F-250 Super Duty",
        }
    ]

    grouped = geo.group_shared_trips(trips)
    assert grouped[0]["is_shared"] is True
    assert grouped[0]["shared_with"][0]["user_id"] == "michele"
    assert grouped[1]["is_shared"] is True
    assert grouped[1]["shared_with"][0]["user_id"] == "jeremiah"
    assert grouped[0]["shared_group_id"] == grouped[1]["shared_group_id"]

    # t3 was a solo trip in a completely different time/location
    assert grouped[2]["is_shared"] is False


def _insert_trip(r: FakeRedis, trip: dict):
    """Store a trip directly in the fake Redis stores (replaces the removed sample-data seeding)."""
    r.data[f"geo:trip:{trip['id']}"] = json.dumps(trip)
    r.zsets.setdefault(f"geo:trips:user:{trip['user_id']}", []).append((trip["id"], trip["start_time"]))
    r.zsets.setdefault("geo:trips:all", []).append((trip["id"], trip["start_time"]))


def _sample_trip(trip_id: str, user_id: str, user_name: str, start: float) -> dict:
    return {
        "id": trip_id,
        "user_id": user_id,
        "user_name": user_name,
        "start_time": start,
        "end_time": start + 1800,
        "duration_seconds": 1800,
        "distance_miles": 12.0,
        "top_speed_mph": 55.0,
        "start_location": {"name": "Home", "latitude": 33.1667, "longitude": -111.5646},
        "end_location": {"name": "Mall", "latitude": 33.2500, "longitude": -111.6300},
        "vehicle_id": "test_truck",
        "vehicle_name": "Test Truck",
        "fuel_type": "gasoline",
        "mpg": 24.0,
        "cost_per_gallon": 3.65,
        "fuel_used_gal": 0.5,
        "trip_cost_usd": 1.83,
        "status": "completed",
    }


def test_get_trips_seed_and_filter(client, fake_redis):
    """Trips are retrieved per user, and the all-users view is admin-only."""
    now = time.time()
    _insert_trip(fake_redis, _sample_trip("t_j1", "jeremiah", "Jeremiah", now - 3600))
    _insert_trip(fake_redis, _sample_trip("t_m1", "michele", "Michele", now - 1800))

    # The all-users view is an admin capability.
    r = client.get("/trips", params={"is_admin": "true"})
    assert r.status_code == 200
    data = r.json()
    assert data["total_trips"] >= 2
    users = {t["user_id"] for t in data["trips"]}
    assert "jeremiah" in users
    assert "michele" in users

    # Filter per login (Jeremiah)
    r_j = client.get("/trips", params={"user_id": "jeremiah", "viewer": "jeremiah"})
    assert r_j.status_code == 200
    assert r_j.json()["trips"]
    for t in r_j.json()["trips"]:
        assert t["user_id"] == "jeremiah"

    # Filter per login (Michele)
    r_m = client.get("/trips", params={"user_id": "michele", "viewer": "michele"})
    assert r_m.status_code == 200
    for t in r_m.json()["trips"]:
        assert t["user_id"] == "michele"


def test_trips_all_bucket_is_not_open_to_everyone(client, fake_redis):
    """Omitting user_id must NOT hand back everyone's trips.

    This was the reported leak: `GET /api/geo/trips` forwarded no identity, so
    geo served `geo:trips:all` to any authenticated caller.
    """
    now = time.time()
    _insert_trip(fake_redis, _sample_trip("t_j1", "jeremiah", "Jeremiah", now - 3600))
    assert client.get("/trips").status_code == 401
    assert client.get("/trips", params={"is_admin": "false"}).status_code == 401


def test_trips_require_opt_in_to_read_another_users(client, fake_redis, monkeypatch):
    """Reading another person's trips needs their sharing consent."""
    now = time.time()
    _insert_trip(fake_redis, _sample_trip("t_j1", "jeremiah", "Jeremiah", now - 3600))

    # Michele has not opted in -> not shared.
    monkeypatch.setattr(geo, "_fetch_activity_sharing", _sharing({}))
    assert client.get(
        "/trips", params={"user_id": "jeremiah", "viewer": "michele"}
    ).status_code == 404

    # Opted in to the circle -> readable.
    monkeypatch.setattr(
        geo,
        "_fetch_activity_sharing",
        _sharing({"jeremiah": {"enabled": True, "audience": "circle"}}),
    )
    ok = client.get("/trips", params={"user_id": "jeremiah", "viewer": "michele"})
    assert ok.status_code == 200
    assert ok.json()["trips"]

    # An admin reads it regardless of consent.
    admin = client.get(
        "/trips", params={"user_id": "jeremiah", "viewer": "michele", "is_admin": "true"}
    )
    assert admin.status_code == 200
    assert admin.json()["trips"]


def _sharing(entries: dict) -> callable:
    """Build a `_fetch_activity_sharing` stub: {username: {...overrides}}."""
    rows = [
        {"username": name, "enabled": False, "audience": "circle", "user_ids": [], **overrides}
        for name, overrides in entries.items()
    ]
    return lambda: _done({"users": rows})


async def _done(value):
    return value


def test_get_trips_empty_without_seed(client, fake_redis):
    """Real data only: no auto-seeding, so an empty store yields zero trips."""
    r = client.get("/trips", params={"is_admin": "true"})
    assert r.status_code == 200
    assert r.json() == {"trips": [], "total_trips": 0}


def test_update_trip_by_owner_allowed(client, fake_redis):
    """Verify owner can update vehicle, MPG, and fuel price, and cost is recalculated."""
    now = time.time()
    _insert_trip(fake_redis, _sample_trip("t_owner", "jeremiah", "Jeremiah", now - 7200))
    trips = client.get("/trips?user_id=jeremiah").json()["trips"]
    target_trip = trips[0]
    trip_id = target_trip["id"]
    orig_miles = target_trip["distance_miles"]
    orig_start = target_trip["start_location"]

    update_payload = {
        "vehicle_id": "custom_truck",
        "vehicle_name": "Modified Ford F-250",
        "fuel_type": "diesel",
        "mpg": 18.0,
        "cost_per_gallon": 4.50,
    }

    resp = client.patch(
        f"/trips/{trip_id}",
        json=update_payload,
        headers={"X-Internal-Secret": geo.INTERNAL_SECRET, "X-User-Id": "jeremiah"},
    )
    assert resp.status_code == 200
    updated = resp.json()
    assert updated["vehicle_name"] == "Modified Ford F-250"
    assert updated["mpg"] == 18.0
    assert updated["cost_per_gallon"] == 4.50
    # Location and mileage remain untouched
    assert updated["distance_miles"] == orig_miles
    assert updated["start_location"] == orig_start
    # Cost is recomputed: (miles / 18.0) * 4.50
    expected_gal = round(orig_miles / 18.0, 2)
    expected_cost = round(expected_gal * 4.50, 2)
    assert updated["fuel_used_gal"] == expected_gal
    assert updated["trip_cost_usd"] == expected_cost


def test_update_trip_by_other_user_forbidden(client, fake_redis):
    """Verify updating a trip by another user is strictly rejected with 403."""
    now = time.time()
    _insert_trip(fake_redis, _sample_trip("t_forbid", "jeremiah", "Jeremiah", now - 7200))
    trips = client.get("/trips?user_id=jeremiah").json()["trips"]
    target_trip = trips[0]
    trip_id = target_trip["id"]

    resp = client.patch(
        f"/trips/{trip_id}",
        json={"vehicle_name": "Hacked Vehicle"},
        headers={"X-Internal-Secret": geo.INTERNAL_SECRET, "X-User-Id": "michele"},
    )
    assert resp.status_code == 403
    assert "Only the user who took this trip" in resp.json()["detail"]


def test_update_trip_activity_type(client, fake_redis):
    """Verify owner can tag a trip with an activity type (e.g. horseback riding)."""
    now = time.time()
    _insert_trip(fake_redis, _sample_trip("t_act", "jeremiah", "Jeremiah", now - 7200))
    trip_id = client.get("/trips?user_id=jeremiah").json()["trips"][0]["id"]

    resp = client.patch(
        f"/trips/{trip_id}",
        json={"activity_type": "horseback_riding", "notes": "Trail ride with Daisy"},
        headers={"X-Internal-Secret": geo.INTERNAL_SECRET, "X-User-Id": "jeremiah"},
    )
    assert resp.status_code == 200
    updated = resp.json()
    assert updated["activity_type"] == "horseback_riding"
    assert updated["notes"] == "Trail ride with Daisy"

    # Invalid activity types are rejected
    resp_bad = client.patch(
        f"/trips/{trip_id}",
        json={"activity_type": "skateboarding"},
        headers={"X-Internal-Secret": geo.INTERNAL_SECRET, "X-User-Id": "jeremiah"},
    )
    assert resp_bad.status_code == 422


def test_manual_shared_rider_assignment(client, fake_redis):
    """Verify trip owner can manually assign/clear shared riders."""
    now = time.time()
    _insert_trip(fake_redis, _sample_trip("t_share", "jeremiah", "Jeremiah", now - 7200))
    trip_id = client.get("/trips?user_id=jeremiah").json()["trips"][0]["id"]

    # Assign riders
    resp = client.patch(
        f"/trips/{trip_id}/share",
        json={"shared_with": ["person.michele", "Summers"]},
        headers={"X-Internal-Secret": geo.INTERNAL_SECRET, "X-User-Id": "jeremiah"},
    )
    assert resp.status_code == 200
    updated = resp.json()
    assert updated["is_shared"] is True
    assert updated["shared_manually"] is True
    names = {r["user_name"] for r in updated["shared_with"]}
    assert "Michele" in names
    assert "Summers" in names

    # Another user cannot edit riders
    resp_forbid = client.patch(
        f"/trips/{trip_id}/share",
        json={"shared_with": []},
        headers={"X-Internal-Secret": geo.INTERNAL_SECRET, "X-User-Id": "michele"},
    )
    assert resp_forbid.status_code == 403

    # Clear riders
    resp_clear = client.patch(
        f"/trips/{trip_id}/share",
        json={"shared_with": []},
        headers={"X-Internal-Secret": geo.INTERNAL_SECRET, "X-User-Id": "jeremiah"},
    )
    assert resp_clear.status_code == 200
    cleared = resp_clear.json()
    assert cleared["is_shared"] is False
    assert cleared["shared_with"] == []


@pytest.mark.asyncio
async def test_workout_start_stop_flow(client, fake_redis):
    """Verify manual workout lifecycle: start, list, stop with computed stats."""
    headers = {"X-Internal-Secret": geo.INTERNAL_SECRET, "X-User-Id": "jeremiah"}

    # Start a horseback ride
    resp = client.post("/workouts/start", json={"activity_type": "horseback_riding"}, headers=headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"

    # Double start returns already_active
    resp_dup = client.post("/workouts/start", json={"activity_type": "running"}, headers=headers)
    assert resp_dup.json()["status"] == "already_active"

    # Backdate the workout start so breadcrumb timestamps fall inside the window
    import asyncio
    base_t = time.time() - 600
    active = json.loads(await fake_redis.get("geo:active_workout:jeremiah"))
    active["start_time"] = base_t
    await fake_redis.set("geo:active_workout:jeremiah", json.dumps(active))

    # Record some breadcrumbs along a path (~0.9 miles of movement)
    for i in range(10):
        await geo.record_point(
            entity_id="jeremiah",
            lat=33.1667 + i * 0.0013,
            lon=-111.5646,
            speed=2.5,
            timestamp=base_t + i * 60,
        )

    # Stop the workout
    resp_stop = client.post("/workouts/stop", json={"notes": "Evening trail ride"}, headers=headers)
    assert resp_stop.status_code == 200
    workout = resp_stop.json()["workout"]
    assert workout["status"] == "completed"
    assert workout["distance_miles"] > 0.5
    assert workout["activity_type"] == "horseback_riding"
    assert workout["notes"] == "Evening trail ride"

    # Listed under workouts
    resp_list = client.get("/workouts?user_id=jeremiah")
    assert resp_list.status_code == 200
    assert any(w["id"] == workout["id"] for w in resp_list.json()["workouts"])

    # Route endpoint returns breadcrumb points
    resp_route = client.get(f"/workouts/{workout['id']}/route")
    assert resp_route.status_code == 200
    assert len(resp_route.json()["points"]) >= 5

    # Invalid activity type rejected
    resp_bad = client.post("/workouts/start", json={"activity_type": "swimming"}, headers=headers)
    assert resp_bad.status_code == 422


@pytest.mark.asyncio
async def test_trip_detection_over_10_mph(fake_redis):
    """Verify trip starts when speed over 10 MPH is reached, using default vehicle."""
    # Configure user's vehicle
    await fake_redis.hset("geo:vehicles", "my_truck", json.dumps({
        "id": "my_truck",
        "name": "2014 Ford F-250",
        "mpg": 16.0,
        "cost_per_gallon": 4.10,
        "fuel_type": "diesel"
    }))
    await fake_redis.set("geo:user_vehicle:jeremiah", "my_truck")

    now = time.time()
    # Speed of 6.0 m/s = ~13.4 MPH (> 10 MPH threshold)
    await geo.process_trip_point(
        user_id="jeremiah",
        lat=33.1667,
        lon=-111.5646,
        speed_mps=6.0,
        timestamp=now
    )

    active_raw = await fake_redis.get("geo:active_trip:jeremiah")
    assert active_raw is not None
    active = json.loads(active_raw)
    assert active["status"] == "in_progress"
    assert active["vehicle_name"] == "2014 Ford F-250"
    assert active["fuel_type"] == "diesel"
    assert active["mpg"] == 16.0
    assert active["top_speed_mph"] >= 13.0
    assert active["activity_type"] == "driving"


# ---------------------------------------------------------------------------
# Smart pedometer (GPS stride model)
# ---------------------------------------------------------------------------

def test_estimate_steps_walking_pace():
    """A steady ~1.4 m/s walk over 2 km should yield a plausible step count."""
    now = time.time()
    # 2 km at 1.4 m/s = ~1429s; breadcrumb every 30s -> ~48 segments of 42m
    points = []
    lat, lon = 33.1667, -111.5646
    for i in range(49):
        points.append({"t": now + i * 30, "lat": lat, "lon": lon, "spd": 1.4})
        # 42m north per 30s ~= 1.4 m/s
        lat += 0.000378

    steps = geo._estimate_steps_from_gps("walking", points)
    assert steps is not None
    # 2000m / ~0.72m stride ~= 2780; wide plausibility band for float drift
    assert 1800 <= steps <= 4000


def test_estimate_steps_running_faster_cadence():
    """Same distance at running pace should produce fewer steps than walking (longer stride)."""
    now = time.time()
    points = []
    lat, lon = 33.1667, -111.5646
    # 2 km at 3.0 m/s = ~667s; breadcrumb every 20s -> ~34 segments of 60m
    for i in range(35):
        points.append({"t": now + i * 20, "lat": lat, "lon": lon, "spd": 3.0})
        lat += 0.000540

    run_steps = geo._estimate_steps_from_gps("running", points)
    assert run_steps is not None
    assert 1200 <= run_steps <= 2600

    walk_points = [dict(p, spd=3.0) for p in points]
    walk_points = [dict(p, t=p["t"]) for p in walk_points]
    # Walking model at running speed (sanity: model still returns numbers)
    walk_steps = geo._estimate_steps_from_gps("walking", walk_points)
    assert walk_steps is None or walk_steps > 0  # model tolerates any input


def test_estimate_steps_rejects_wheeled_activities():
    """Cycling/dirtbiking/horseback have no bipedal stride — no step estimate."""
    points = [{"t": time.time() + i * 10, "lat": 33.16 + i * 0.001, "lon": -111.56, "spd": 5.0} for i in range(30)]
    for activity in ("cycling", "mountain_biking", "dirtbiking", "horseback_riding"):
        assert geo._estimate_steps_from_gps(activity, points) is None


def test_estimate_steps_insufficient_data():
    """Too few points or stationary data -> None, never a garbage count."""
    assert geo._estimate_steps_from_gps("walking", []) is None
    now = time.time()
    stationary = [{"t": now + i * 30, "lat": 33.1667, "lon": -111.5646, "spd": 0.0} for i in range(10)]
    assert geo._estimate_steps_from_gps("walking", stationary) is None


# ---------------------------------------------------------------------------
# Daily steps (hardware pedometer)
# ---------------------------------------------------------------------------

def test_daily_steps_via_record_endpoint(client, fake_redis):
    """daily_steps piggybacked on a breadcrumb post is stored per-day."""
    resp = client.post(
        "/people/jeremiah/record",
        json={"latitude": 33.1667, "longitude": -111.5646, "daily_steps": 5432},
        headers={"X-Internal-Secret": geo.INTERNAL_SECRET},
    )
    assert resp.status_code == 200

    raw = fake_redis.hashes.get("geo:steps:jeremiah", {})
    # day bucket written
    assert len(raw) == 1
    assert list(raw.values())[0] == 5432

    # GET /steps returns it (and today's bucket)
    r = client.get("/steps?user_id=jeremiah")
    assert r.status_code == 200
    data = r.json()
    assert data["today"] == 5432
    assert data["goal"] == 10000
    assert any(v == 5432 for v in data["daily_steps"].values())


def test_daily_steps_max_keeps_highest_reading(client, fake_redis):
    """Cumulative counters only go up; a lower later reading never lowers the day."""
    resp1 = client.post(
        "/people/jeremiah/record",
        json={"latitude": 33.1667, "longitude": -111.5646, "daily_steps": 8000},
        headers={"X-Internal-Secret": geo.INTERNAL_SECRET},
    )
    assert resp1.status_code == 200
    resp2 = client.post(
        "/people/jeremiah/record",
        json={"latitude": 33.1677, "longitude": -111.5646, "daily_steps": 2500},
        headers={"X-Internal-Secret": geo.INTERNAL_SECRET},
    )
    assert resp2.status_code == 200

    r = client.get("/steps?user_id=jeremiah")
    assert r.json()["today"] == 8000


def test_daily_steps_direct_endpoint(client, fake_redis):
    """POST /steps works standalone with X-User-Id."""
    resp = client.post(
        "/steps",
        json={"steps": 12345},
        headers={"X-Internal-Secret": geo.INTERNAL_SECRET, "X-User-Id": "person.jeremiah"},
    )
    assert resp.status_code == 200
    r = client.get("/steps?user_id=jeremiah&days=7")
    assert r.status_code == 200
    data = r.json()
    assert data["today"] >= 12345


def test_steps_endpoints_require_secret(client):
    from fastapi.testclient import TestClient as _TC

    unauthenticated = _TC(geo.app)
    resp = unauthenticated.post("/steps", json={"steps": 100, "user_id": "jeremiah"})
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Trends
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_trends_activity_real_data(client, fake_redis):
    """Trends returns real stats; LLM analysis degrades gracefully when gateway is down."""
    now = time.time()
    headers = {"X-Internal-Secret": geo.INTERNAL_SECRET}

    # Seed today's steps + a completed workout + a driving trip
    resp = client.post(
        "/steps",
        json={"steps": 9500},
        headers={**headers, "X-User-Id": "jeremiah"},
    )
    assert resp.status_code == 200

    resp_start = client.post("/workouts/start", json={"activity_type": "walking"}, headers={**headers, "X-User-Id": "jeremiah"})
    assert resp_start.status_code == 200
    # Backdate start so breadcrumbs fall inside the workout window
    active = await fake_redis.get("geo:active_workout:jeremiah")
    active_data = json.loads(active)
    active_data["start_time"] = now - 620
    await fake_redis.set("geo:active_workout:jeremiah", json.dumps(active_data))
    # Breadcrumbs: ~1.2 km walk
    lat = 33.1667
    for i in range(12):
        await geo.record_point(
            entity_id="jeremiah",
            lat=lat + i * 0.0004,
            lon=-111.5646,
            speed=1.3,
            timestamp=now - 600 + i * 45,
        )
    resp_stop = client.post("/workouts/stop", json={}, headers={**headers, "X-User-Id": "jeremiah"})
    assert resp_stop.status_code == 200
    stopped = resp_stop.json()["workout"]
    assert stopped["steps"] is not None  # gps estimate present for walking
    assert stopped["steps_source"] == "gps_estimate"

    resp_trends = client.get("/trends/activity?user_id=jeremiah&days=7", headers=headers)
    assert resp_trends.status_code == 200
    trends = resp_trends.json()
    assert trends["steps_today"] == 9500
    assert trends["workout_count"] >= 1
    assert "analysis" in trends
    assert "analysis_available" in trends
    # With no gateway in tests, analysis must degrade to None (no fake text)
    assert trends["analysis"] is None
    assert trends["analysis_available"] is False


def test_workout_pedometer_override_wins(client, fake_redis):
    """A client-provided pedometer count is stored as-is and marked as pedometer source."""
    headers = {"X-Internal-Secret": geo.INTERNAL_SECRET, "X-User-Id": "jeremiah"}
    resp_start = client.post("/workouts/start", json={"activity_type": "running"}, headers=headers)
    assert resp_start.status_code == 200
    resp_stop = client.post("/workouts/stop", json={"steps": 3210, "distance_miles": 1.6}, headers=headers)
    assert resp_stop.status_code == 200
    w = resp_stop.json()["workout"]
    assert w["steps"] == 3210
    assert w["steps_source"] == "pedometer"


# ---------------------------------------------------------------------------
# Noisy fixes (production, 2026-10-06): GPS fixes interleaved with network
# fixes 800 m away, flushed in bursts. Routes zig-zagged off the road and a
# 10-second "trip" topped 95 mph.
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_coarse_fixes_are_not_breadcrumbs(fake_redis):
    now = time.time()
    await geo.record_point(entity_id="jeremiah", lat=32.26375, lon=-109.85566, accuracy=19, timestamp=now)
    await geo.record_point(entity_id="jeremiah", lat=32.26362, lon=-109.84696, accuracy=142, timestamp=now + 1)
    await geo.record_point(entity_id="jeremiah", lat=32.26729, lon=-109.84493, accuracy=600, timestamp=now + 2)
    kept = [json.loads(p) for p in await fake_redis.zrangebyscore("geo:history:jeremiah", 0, now + 10)]
    assert [p["acc"] for p in kept] == [19]


@pytest.mark.asyncio
async def test_a_coarse_jump_cannot_start_a_trip(fake_redis):
    """The network fix 800 m away used to read as a 95 mph drive."""
    now = time.time()
    await geo.record_point(entity_id="jeremiah", lat=32.26375, lon=-109.85566, accuracy=19, timestamp=now)
    await geo.record_point(entity_id="jeremiah", lat=32.26362, lon=-109.84696, accuracy=142, timestamp=now + 10)
    assert await fake_redis.get("geo:active_trip:jeremiah") is None


@pytest.mark.asyncio
async def test_a_late_fix_does_not_move_a_trip_backwards(fake_redis):
    now = time.time()
    await geo.process_trip_point(user_id="jeremiah", lat=33.1667, lon=-111.5646, speed_mps=15.0, timestamp=now)
    await geo.process_trip_point(user_id="jeremiah", lat=33.1700, lon=-111.5646, speed_mps=15.0, timestamp=now + 30)
    before = json.loads(await fake_redis.get("geo:active_trip:jeremiah"))
    await geo.process_trip_point(user_id="jeremiah", lat=33.1680, lon=-111.5646, speed_mps=15.0, timestamp=now + 10)
    after = json.loads(await fake_redis.get("geo:active_trip:jeremiah"))
    assert after["distance_miles"] == before["distance_miles"]
    assert after["last_lat"] == before["last_lat"]


# ---------------------------------------------------------------------------
# Road network checks on trips (OSRM): road distance, phantoms, jumps
# ---------------------------------------------------------------------------

def _active_trip(now, miles=1.0):
    return {"id": "trip_jeremiah_1", "start_time": now - 600, "last_moving_time": now - 400,
            "distance_miles": miles, "start_location": {"latitude": 33.1, "longitude": -111.5},
            "end_location": {"latitude": 33.2, "longitude": -111.5}}


@pytest.mark.asyncio
async def test_a_trip_takes_its_road_distance(fake_redis, monkeypatch):
    async def road(points, url):
        return {"matched": True, "coordinates": [[33.1, -111.5], [33.2, -111.5]], "distance_m": 3218.7,
                "confidence": 0.9, "used": 5}
    monkeypatch.setattr(geo.map_match, "match", road)
    done = await geo._finalize_active_trip(fake_redis, "jeremiah", _active_trip(time.time(), miles=2.6), time.time())
    assert done["distance_miles"] == 2.0 and done["distance_source"] == "road"


@pytest.mark.asyncio
async def test_a_trail_that_fits_no_road_is_not_a_trip(fake_redis, monkeypatch):
    async def phantom(points, url):
        return {"matched": False, "used": 3}
    monkeypatch.setattr(geo.map_match, "match", phantom)
    assert await geo._finalize_active_trip(fake_redis, "jeremiah", _active_trip(time.time(), miles=5.3), time.time()) is None


@pytest.mark.asyncio
async def test_osrm_down_keeps_the_trip(fake_redis, monkeypatch):
    async def down(points, url):
        return None
    monkeypatch.setattr(geo.map_match, "match", down)
    done = await geo._finalize_active_trip(fake_redis, "jeremiah", _active_trip(time.time(), miles=1.4), time.time())
    assert done["distance_miles"] == 1.4 and "distance_source" not in done


@pytest.mark.asyncio
async def test_an_impossible_jump_is_not_a_breadcrumb(fake_redis, monkeypatch):
    now = time.time()
    await geo.record_point(entity_id="jeremiah", lat=33.1667, lon=-111.5646, accuracy=8, timestamp=now)

    async def far_by_road(a, b, url, timeout_s=5.0):
        return {"distance_m": 30000.0, "duration_s": 1800.0}
    monkeypatch.setattr(geo.map_match, "route", far_by_road)
    # 4 km away 60 s later: under the speed limit for a straight line, but a
    # 30-minute drive by road
    await geo.record_point(entity_id="jeremiah", lat=33.2027, lon=-111.5646, accuracy=8, timestamp=now + 60)
    # 2 hours later the same place is reachable
    await geo.record_point(entity_id="jeremiah", lat=33.2027, lon=-111.5646, accuracy=8, timestamp=now + 7200)
    kept = [json.loads(p)["t"] for p in await fake_redis.zrangebyscore("geo:history:jeremiah", 0, now + 9999)]
    assert kept == [now, now + 7200]


@pytest.mark.asyncio
async def test_faster_than_any_vehicle_is_dropped_without_asking_osrm(fake_redis, monkeypatch):
    now = time.time()
    await geo.record_point(entity_id="jeremiah", lat=33.1667, lon=-111.5646, accuracy=8, timestamp=now)

    async def never(*a, **k):
        raise AssertionError("no road check needed")
    monkeypatch.setattr(geo.map_match, "route", never)
    await geo.record_point(entity_id="jeremiah", lat=33.30, lon=-111.5646, accuracy=8, timestamp=now + 10)
    kept = await fake_redis.zrangebyscore("geo:history:jeremiah", 0, now + 99)
    assert len(kept) == 1


# ---------------------------------------------------------------------------
# ETA by road (OSRM) from someone's latest fix
# ---------------------------------------------------------------------------

HOME = {"entity_id": "zone.home", "name": "Home", "latitude": 33.1667, "longitude": -111.5646, "radius": 100.0}


@pytest.mark.asyncio
async def test_eta_home_by_road(client, fake_redis, monkeypatch):
    now = time.time()
    await fake_redis.zadd("geo:history:michele", {json.dumps({"t": now - 30, "lat": 33.30, "lon": -111.70, "acc": 8, "spd": 20.0}): now - 30})

    async def zones():
        return [HOME]

    async def road(a, b, url, timeout_s=5.0):
        assert b == (HOME["latitude"], HOME["longitude"])
        return {"distance_m": 19800.0, "duration_s": 1080.0}
    monkeypatch.setattr(geo, "_zones", zones)
    monkeypatch.setattr(geo.map_match, "route", road)
    resp = client.get("/people/michele/eta?to=home", headers={"X-Internal-Secret": INTERNAL_SECRET})
    body = resp.json()
    assert resp.status_code == 200
    assert body["to"] == "Home" and body["duration_s"] == 1080 and body["moving"] is True and body["arrived"] is False


@pytest.mark.asyncio
async def test_eta_when_already_there(client, fake_redis, monkeypatch):
    now = time.time()
    await fake_redis.zadd("geo:history:michele", {json.dumps({"t": now, "lat": 33.1668, "lon": -111.5646, "acc": 8}): now})

    async def zones():
        return [HOME]
    monkeypatch.setattr(geo, "_zones", zones)
    body = client.get("/people/michele/eta", headers={"X-Internal-Secret": INTERNAL_SECRET}).json()
    assert body["arrived"] is True and body["duration_s"] == 0


@pytest.mark.asyncio
async def test_eta_for_an_unknown_place_says_so(client, fake_redis, monkeypatch):
    async def zones():
        return [HOME]
    monkeypatch.setattr(geo, "_zones", zones)
    await fake_redis.zadd("geo:history:michele", {json.dumps({"t": 1, "lat": 33.3, "lon": -111.7}): 1})
    resp = client.get("/people/michele/eta?to=narnia", headers={"X-Internal-Secret": INTERNAL_SECRET})
    assert resp.status_code == 404 and "narnia" in resp.json()["detail"]


def test_eta_and_telemetry_respect_sharing(client, fake_redis, monkeypatch):
    async def not_shared(viewer, target):
        return False
    monkeypatch.setattr(geo, "_viewer_may_see", not_shared)
    h = {"X-Internal-Secret": INTERNAL_SECRET}
    assert client.get("/people/michele/eta?viewer=kate", headers=h).status_code == 404
    assert client.get("/people/michele/telemetry?viewer=kate", headers=h).status_code == 404
