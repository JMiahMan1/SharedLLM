"""Geo's part of the account export (docs/ACCOUNT_EXPORT.md)."""
import json

import pytest
from fastapi.testclient import TestClient

import services.geo.main as geo


class FakeRedis:
    """Just the commands the export uses, in memory."""

    def __init__(self):
        self.kv, self.h, self.z = {}, {}, {}

    async def get(self, k):
        return self.kv.get(k)

    async def set(self, k, v, ex=None, nx=False):
        self.kv[k] = v
        return True

    async def hgetall(self, k):
        return dict(self.h.get(k, {}))

    async def hset(self, k, f=None, v=None, mapping=None):
        self.h.setdefault(k, {}).update(mapping or {f: v})

    async def zadd(self, k, mapping):
        self.z.setdefault(k, {}).update(mapping)

    async def zcard(self, k):
        return len(self.z.get(k, {}))

    async def zrange(self, k, start, end):
        items = [m for m, _ in sorted(self.z.get(k, {}).items(), key=lambda kv: kv[1])]
        return items[start:] if end == -1 else items[start:end + 1]


@pytest.fixture
def client(monkeypatch):
    r = FakeRedis()

    async def redis():
        return r

    monkeypatch.setattr(geo, "get_redis", redis)
    monkeypatch.setattr(geo, "INTERNAL_SECRET", "s3cret")
    c = TestClient(geo.app, headers={"X-Internal-Secret": "s3cret"})
    r.h["geo:places"] = {
        "p1": json.dumps({"id": "p1", "owner": "lee", "name": "Gym", "scope": "user"}),
        "p2": json.dumps({"id": "p2", "owner": "max", "name": "Max's office", "scope": "family"}),
    }
    trip = {"id": "t1", "user_id": "lee", "start_time": 100, "updated_at": 200, "distance_miles": 3.2,
            "is_shared": True, "shared_with": ["max"]}
    r.kv["geo:trip:t1"] = json.dumps(trip)
    r.z["geo:trips:user:lee"] = {"t1": 100}
    r.kv["geo:trip:t9"] = json.dumps({"id": "t9", "user_id": "max", "start_time": 5, "updated_at": 5})
    r.z["geo:trips:user:max"] = {"t9": 5}
    r.h["geo:steps:lee"] = {"2026-01-01": "8000"}
    r.z["geo:history:lee"] = {json.dumps({"t": float(i), "lat": 1.0, "lon": 2.0}): float(i) for i in range(12)}
    return c, r


def test_needs_the_internal_secret(client):
    c, _ = client
    assert c.get("/internal/account/export/lee", headers={"X-Internal-Secret": "x"}).status_code == 403


def test_export_has_only_the_persons_own_places_and_trips(client):
    c, _ = client
    data = c.get("/internal/account/export/lee").json()["collections"]
    assert [p["id"] for p in data["places"]] == ["p1"]
    assert [t["id"] for t in data["trips"]] == ["t1"]
    assert data["daily_steps"] == [{"day": "2026-01-01", "steps": "8000"}]


def test_location_history_comes_in_pages(client):
    c, _ = client
    seen, start = [], 0
    while start is not None:
        page = c.get(f"/internal/account/export/lee/location-history?start={start}&count=5").json()
        assert len(page["points"]) <= 5
        seen += [p["t"] for p in page["points"]]
        start = page["next"]
    assert seen == [float(i) for i in range(12)]


def test_restore_to_a_new_account_drops_sharing_and_avoids_taken_ids(client):
    c, r = client
    data = c.get("/internal/account/export/lee").json()["collections"]
    data["trips"].append({"id": "t9", "user_id": "max", "start_time": 7, "updated_at": 7})  # max's id
    out = c.post("/internal/account/import/sky", json={"collections": data, "dry_run": False}).json()["collections"]
    assert out["trips"]["add"] == 2
    assert out["places"]["add"] == 1
    assert json.loads(r.kv["geo:trip:t9"])["user_id"] == "max"  # untouched
    restored = [json.loads(r.kv[f"geo:trip:{i}"]) for i in r.z["geo:trips:user:sky"]]
    assert all("shared_with" not in t for t in restored)
    assert all(t["user_id"] == "sky" for t in restored)
    owners = {json.loads(v)["owner"] for v in r.h["geo:places"].values()}
    assert owners == {"lee", "max", "sky"}


def test_restore_never_overwrites_newer_without_asking(client):
    c, r = client
    data = c.get("/internal/account/export/lee").json()["collections"]
    data["trips"][0].update(distance_miles=9.9, updated_at=150)  # older than the server's 200
    data["daily_steps"][0]["steps"] = "1"
    out = c.post("/internal/account/import/lee", json={"collections": data, "dry_run": False}).json()["collections"]
    assert out["trips"]["conflict"] == 1 and out["daily_steps"]["conflict"] == 1
    assert json.loads(r.kv["geo:trip:t1"])["distance_miles"] == 3.2
    assert r.h["geo:steps:lee"]["2026-01-01"] == "8000"
    data["trips"][0]["updated_at"] = 300
    out = c.post("/internal/account/import/lee", json={"collections": data, "dry_run": False}).json()["collections"]
    assert out["trips"]["update"] == 1
    stored = json.loads(r.kv["geo:trip:t1"])
    assert stored["distance_miles"] == 9.9
    assert stored["shared_with"] == ["max"]  # the server's sharing is kept
