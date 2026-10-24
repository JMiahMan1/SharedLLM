"""Place reminders: "remind me when I get home" rings on the person's own arrival."""
import time

import pytest
from fastapi.testclient import TestClient

import services.geo.main as geo
from services.geo import place_reminders

HOME = {"entity_id": "zone.home", "name": "Home", "latitude": 33.1667, "longitude": -111.5646, "radius": 100.0}
WORK = {"entity_id": "zone.work", "name": "Work", "latitude": 33.30, "longitude": -111.80, "radius": 150.0}
AWAY = (33.25, -111.70)


class R:
    def __init__(self):
        self.kv, self.h = {}, {}

    async def get(self, k):
        return self.kv.get(k)

    async def set(self, k, v, ex=None, nx=False):
        if nx and k in self.kv:
            return None
        self.kv[k] = v
        return True

    async def hgetall(self, k):
        return dict(self.h.get(k, {}))

    async def hget(self, k, f):
        return self.h.get(k, {}).get(f)

    async def hset(self, k, f, v):
        self.h.setdefault(k, {})[f] = v

    async def hdel(self, k, f):
        return 1 if self.h.get(k, {}).pop(f, None) is not None else 0


@pytest.fixture
def world(monkeypatch):
    sent = []
    geo._zone_cache.update({"at": time.time(), "zones": [HOME, WORK]})

    async def tracked(r):
        return ["jeremiah", "michele"]

    async def may_see(viewer, target):
        return True

    async def send(payload):
        sent.append(payload)
        return True

    async def eta(person, to="home"):
        return {"arrived": True}

    monkeypatch.setattr(geo, "_tracked_users", tracked)
    monkeypatch.setattr(geo, "_viewer_may_see", may_see)
    monkeypatch.setattr(geo, "_send_notice", send)
    monkeypatch.setattr(geo, "compute_eta", eta)
    yield sent
    geo._zone_cache.update({"at": 0.0, "zones": []})


def _reminders(sent):
    return [p for p in sent if p["category"] == "timers"]


async def _set(r, user="jeremiah", zone="zone.home", on="arrive", title="Take the bins out", url="/notes?note=Notes%2FBins.md"):
    return await place_reminders.add(r, user, place_reminders.validate(
        {"zone": zone, "on": on, "title": title, "url": url}), "Home" if zone == "zone.home" else "Work")


@pytest.mark.asyncio
async def test_arriving_home_rings_the_reminder_once_for_that_person_only(world):
    r = R()
    entry = await _set(r)
    t = time.time()
    await geo._presence_transition(r, "jeremiah", *AWAY, t)
    await geo._presence_transition(r, "jeremiah", 33.1668, -111.5646, t + 600)
    rung = _reminders(world)
    assert len(rung) == 1
    notice = rung[0]
    assert notice["user"] == "jeremiah"
    assert notice["title"] == "📍 Reminder: Take the bins out" and notice["body"] == "Arrived at Home"
    assert notice["data"]["url"] == "/notes?note=Notes%2FBins.md"
    assert notice["once"] == f"place-reminder:{entry['id']}"
    # Gone once it went off: leaving and coming back again is silent.
    assert await place_reminders.list_for(r, "jeremiah") == []
    await geo._presence_transition(r, "jeremiah", *AWAY, t + 1200)
    await geo._presence_transition(r, "jeremiah", 33.1668, -111.5646, t + 1800)
    assert len(_reminders(world)) == 1


@pytest.mark.asyncio
async def test_someone_elses_arrival_and_the_wrong_event_do_not_ring_it(world):
    r = R()
    await _set(r, on="leave")
    t = time.time()
    # Michele arriving home is not Jeremiah's reminder.
    await geo._presence_transition(r, "michele", *AWAY, t)
    await geo._presence_transition(r, "michele", 33.1668, -111.5646, t + 600)
    # Jeremiah arriving is not a departure.
    await geo._presence_transition(r, "jeremiah", *AWAY, t)
    await geo._presence_transition(r, "jeremiah", 33.1668, -111.5646, t + 600)
    assert _reminders(world) == []
    await geo._presence_transition(r, "jeremiah", *AWAY, t + 1200)
    assert [n["body"] for n in _reminders(world)] == ["Left Home"]


@pytest.mark.asyncio
async def test_first_sighting_inside_the_place_is_not_an_arrival(world):
    r = R()
    await _set(r)
    await geo._presence_transition(r, "jeremiah", 33.1668, -111.5646, time.time())
    assert _reminders(world) == []
    assert len(await place_reminders.list_for(r, "jeremiah")) == 1


def test_validate_refuses_bad_reminders():
    good = {"zone": "zone.home", "on": "arrive", "title": "Bins"}
    assert place_reminders.validate(good) == {"zone": "zone.home", "on": "arrive", "title": "Bins", "url": None}
    for bad in (
        None,
        {**good, "zone": "home"},
        {**good, "on": "nearby"},
        {**good, "title": "  "},
        {**good, "url": "https://evil.example"},
        {**good, "url": "//evil.example/x"},
    ):
        with pytest.raises(ValueError):
            place_reminders.validate(bad)


@pytest.mark.asyncio
async def test_remove_only_takes_a_waiting_reminder_by_its_id():
    r = R()
    entry = await _set(r)
    assert await place_reminders.remove(r, "jeremiah", "../../x") is False
    assert await place_reminders.remove(r, "michele", entry["id"]) is False
    assert await place_reminders.remove(r, "jeremiah", entry["id"]) is True
    assert await place_reminders.remove(r, "jeremiah", entry["id"]) is False


@pytest.fixture
def routes(monkeypatch):
    r = R()

    async def redis():
        return r

    async def zones():
        return [HOME, WORK]

    monkeypatch.setattr(geo, "get_redis", redis)
    monkeypatch.setattr(geo, "_zones", zones)
    monkeypatch.setattr(geo, "INTERNAL_SECRET", "s3cret")
    return TestClient(geo.app), r


def test_routes_add_list_and_remove_a_place_reminder(routes):
    client, _ = routes
    headers = {"X-Internal-Secret": "s3cret"}
    made = client.post("/place-reminders", params={"user": "jeremiah"}, headers=headers,
                       json={"zone": "zone.work", "on": "leave", "title": "Call Mum"})
    assert made.status_code == 200
    entry = made.json()["reminder"]
    assert entry["zone_name"] == "Work" and entry["on"] == "leave"
    listed = client.get("/place-reminders", params={"user": "jeremiah"}, headers=headers).json()["reminders"]
    assert [e["id"] for e in listed] == [entry["id"]]
    assert client.get("/place-reminders", params={"user": "michele"}, headers=headers).json()["reminders"] == []
    assert client.delete(f"/place-reminders/{entry['id']}", params={"user": "jeremiah"}, headers=headers).status_code == 200
    assert client.delete(f"/place-reminders/{entry['id']}", params={"user": "jeremiah"}, headers=headers).status_code == 404


def test_routes_refuse_an_unknown_place_and_a_missing_secret(routes):
    client, _ = routes
    headers = {"X-Internal-Secret": "s3cret"}
    body = {"zone": "zone.moon", "on": "arrive", "title": "x"}
    assert client.post("/place-reminders", params={"user": "jeremiah"}, headers=headers, json=body).status_code == 422
    assert client.get("/place-reminders", params={"user": "jeremiah"}).status_code == 403
