"""Arrival / departure notices from HA zones, with an ETA home on departure."""
import json
import time

import pytest

import services.geo.main as geo

HOME = {"entity_id": "zone.home", "name": "Home", "latitude": 33.1667, "longitude": -111.5646, "radius": 100.0}
WORK = {"entity_id": "zone.work", "name": "Work", "latitude": 33.30, "longitude": -111.80, "radius": 150.0}


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
        self.h.get(k, {}).pop(f, None)


class _Sent(list):
    """(user, title, body) per notice, with the whole notices in ``payloads``."""

    def __init__(self):
        super().__init__()
        self.payloads = []


@pytest.fixture
def world(monkeypatch):
    sent = _Sent()
    payloads = sent.payloads
    geo._zone_cache.update({"at": time.time(), "zones": [HOME, WORK]})

    async def tracked(r):
        return ["jeremiah", "kate", "michele"]

    async def may_see(viewer, target):
        return viewer != "kate"   # kate is not in the circle

    async def send(payload):
        assert payload["category"] in ("family_presence", "family_safety")
        sent.append((payload["user"], payload["title"], payload["body"]))
        payloads.append(payload)
        return True

    async def eta(person, to="home"):
        return {"arrived": False, "duration_s": 1320}
    monkeypatch.setattr(geo, "_tracked_users", tracked)
    monkeypatch.setattr(geo, "_viewer_may_see", may_see)
    monkeypatch.setattr(geo, "_send_notice", send)
    monkeypatch.setattr(geo, "compute_eta", eta)
    yield sent
    geo._zone_cache.update({"at": 0.0, "zones": []})


@pytest.mark.asyncio
async def test_leaving_work_tells_the_family_with_an_eta_home(world):
    r = R()
    t = time.time()
    assert await geo._presence_transition(r, "jeremiah", 33.30, -111.80, t) == []   # first sighting: silent
    events = await geo._presence_transition(r, "jeremiah", 33.25, -111.70, t + 60)
    assert [e["kind"] for e in events] == ["departed"]
    assert world == [("michele", "Jeremiah left Work", "Home in about 22 min by road.")]  # not kate, not himself


@pytest.mark.asyncio
async def test_arriving_home(world):
    r = R()
    t = time.time()
    await geo._presence_transition(r, "michele", 33.25, -111.70, t)
    await geo._presence_transition(r, "michele", 33.1668, -111.5646, t + 600)
    assert ("jeremiah", "Michele arrived at Home", "") in world


@pytest.mark.asyncio
async def test_jitter_at_the_edge_does_not_flap(world):
    r = R()
    t = time.time()
    await geo._presence_transition(r, "michele", 33.1667, -111.5646, t)            # home
    # 130 m out: past the 100 m radius but inside the 50 m exit margin
    await geo._presence_transition(r, "michele", 33.16787, -111.5646, t + 30)
    assert world == []


@pytest.mark.asyncio
async def test_a_vague_fix_at_the_edge_is_not_a_departure(world):
    r = R()
    t = time.time()
    await geo._presence_transition(r, "michele", 33.1667, -111.5646, t)            # home
    # 180 m out with 90 m uncertainty: could well be inside
    await geo._presence_transition(r, "michele", 33.16832, -111.5646, t + 30, acc=90.0)
    assert world == []
    # The same spot, precisely: gone
    await geo._presence_transition(r, "michele", 33.16832, -111.5646, t + 60, acc=8.0)
    assert ("jeremiah", "Michele left Home", "") in world


@pytest.mark.asyncio
async def test_the_old_switch_moves_to_the_family_presence_preference_once(monkeypatch):
    """Geo's own presence switch is gone: whoever had it off gets
    family_presence=false in identity, and the old entry is dropped only once
    identity took it."""
    r = R()
    await r.hset(geo.LEGACY_PRESENCE_ALERTS_KEY, "michele", "off")
    await r.hset(geo.LEGACY_PRESENCE_ALERTS_KEY, "kate", "off")
    await r.hset(geo.LEGACY_PRESENCE_ALERTS_KEY, "leo", "on")
    calls, status = [], {"michele": 200, "kate": 503}

    class Resp:
        def __init__(self, code):
            self.status = code

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    class Client:
        def put(self, url, params=None, json=None, **kw):
            calls.append((url, params["username"], json))
            return Resp(status[params["username"]])

    monkeypatch.setattr(geo, "get_client", lambda: Client())
    assert await geo._migrate_presence_switches(r) == 1
    assert ("michele", {"family_presence": False}) in [(u, j) for _, u, j in calls]
    assert calls[0][0].endswith("/api/internal/push/preferences")
    assert r.h[geo.LEGACY_PRESENCE_ALERTS_KEY] == {"kate": "off"}, "kept until identity answers"
    status["kate"] = 200
    assert await geo._migrate_presence_switches(r) == 1
    assert r.h[geo.LEGACY_PRESENCE_ALERTS_KEY] == {}
    assert await geo._migrate_presence_switches(r) == 0


@pytest.mark.asyncio
async def test_the_same_notice_is_not_repeated_within_minutes(world):
    r = R()
    t = time.time()
    for i, (lat, lon) in enumerate([(33.30, -111.80), (33.25, -111.70), (33.30, -111.80), (33.25, -111.70)]):
        await geo._presence_transition(r, "jeremiah", lat, lon, t + i * 60)
    departures = [s for s in world if s[1] == "Jeremiah left Work"]
    assert len(departures) == 1


@pytest.mark.asyncio
async def test_presence_notices_are_family_presence_with_a_map_button(world):
    r = R()
    t = time.time()
    await geo._presence_transition(r, "michele", 33.25, -111.70, t)
    await geo._presence_transition(r, "michele", 33.1668, -111.5646, t + 600)
    notice = world.payloads[0]
    assert notice["category"] == "family_presence" and notice["tag"] == "presence:michele"
    assert notice["data"]["url"] == "/wander?person=michele"
    assert notice["actions"] == [{"label": "Map", "type": "view", "url": "/wander?person=michele"}]


@pytest.mark.asyncio
async def test_an_important_place_is_a_safety_notice_for_the_chosen_people(world, monkeypatch):
    async def tracked(r):
        return ["jeremiah", "kate", "leo", "michele"]

    async def may_see(viewer, target):
        return True

    monkeypatch.setattr(geo, "_tracked_users", tracked)
    monkeypatch.setattr(geo, "_viewer_may_see", may_see)
    r = R()
    await geo.safety.save_place_setting(r, "zone.work", True, ["Michele", "kate"])
    t = time.time()
    await geo._presence_transition(r, "leo", 33.25, -111.70, t)
    await geo._presence_transition(r, "leo", 33.30, -111.80, t + 60)
    by_user = {n["user"]: n for n in world.payloads}
    assert by_user["michele"]["category"] == "family_safety" and by_user["michele"]["priority"] == 4
    assert by_user["kate"]["category"] == "family_safety"
    # Not chosen for the place: the ordinary arrival.
    assert by_user["jeremiah"]["category"] == "family_presence"
    assert "leo" not in by_user


@pytest.mark.asyncio
async def test_an_important_place_is_family_safety_for_everyone_when_none_chosen(world):
    r = R()
    await geo.safety.save_place_setting(r, "zone.work", True, [])
    t = time.time()
    await geo._presence_transition(r, "jeremiah", 33.30, -111.80, t)
    await geo._presence_transition(r, "jeremiah", 33.25, -111.70, t + 60)
    assert [(n["user"], n["category"]) for n in world.payloads] == [("michele", "family_safety")]
