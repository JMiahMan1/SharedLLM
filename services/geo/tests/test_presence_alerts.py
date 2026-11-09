"""Arrival / departure notices from HA zones, with an ETA home on departure."""
import json
import time

import pytest

import services.geo.main as geo

HOME = {"entity_id": "zone.home", "name": "Home", "latitude": 33.1667, "longitude": -111.5646, "radius": 100.0}
WORK = {"entity_id": "zone.work", "name": "Work", "latitude": 33.30, "longitude": -111.80, "radius": 150.0}


class R:
    def __init__(self):
        self.kv, self.h, self.z = {}, {}, {}

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

    async def zrevrange(self, k, a, b):
        items = sorted(self.z.get(k, {}).items(), key=lambda kv: kv[1], reverse=True)
        members = [m for m, _ in items]
        return members[a:b + 1]

    async def zadd(self, k, mapping):
        zs = self.z.setdefault(k, {})
        zs.update({m: float(s) for m, s in mapping.items()})
        return len(mapping)

    async def zrevrangebyscore(self, k, maxv, minv, start=None, num=None):
        maxf = float(maxv)
        minf = float(minv)
        items = [(m, s) for m, s in self.z.get(k, {}).items() if minf <= s <= maxf]
        items.sort(key=lambda kv: kv[1], reverse=True)
        members = [m for m, _ in items]
        if start is not None and num is not None:
            members = members[start:start + num]
        return members

    async def keys(self, pattern):
        import fnmatch
        return [k for k in self.kv if fnmatch.fnmatchcase(k, pattern)]


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
    # The later look at a pending arrival is a background timer; tests run it
    # by hand (``_confirm_pending_arrival``) instead of waiting two minutes.
    later = []

    def spawn(coro):
        later.append(coro)
        coro.close()

    monkeypatch.setattr(geo, "_spawn", spawn)
    sent.later = later
    yield sent
    geo._zone_cache.update({"at": 0.0, "zones": []})


@pytest.mark.asyncio
async def test_leaving_work_tells_the_family_with_an_eta_home(world):
    r = R()
    t = time.time()
    assert await geo._presence_transition(r, "jeremiah", 33.30, -111.80, t) == []   # first sighting: silent
    assert await geo._presence_transition(r, "jeremiah", 33.25, -111.70, t + 60) == []   # leaving...
    events = await geo._presence_transition(r, "jeremiah", 33.24, -111.68, t + 60 + geo.DEPART_DWELL_S)
    assert [e["kind"] for e in events] == ["departed"]
    assert world == [("michele", "Jeremiah left Work", "Home in about 22 min by road.")]  # not kate, not himself


@pytest.mark.asyncio
async def test_arriving_home(world):
    r = R()
    t = time.time()
    await geo._presence_transition(r, "michele", 33.25, -111.70, t)
    await geo._presence_transition(r, "michele", 33.1668, -111.5646, t + 600)
    await geo._presence_transition(r, "michele", 33.1668, -111.5646, t + 750)   # still there
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
    await geo._presence_transition(r, "michele", 33.16832, -111.5646, t + 60 + geo.DEPART_DWELL_S, acc=8.0)
    assert ("jeremiah", "Michele left Home", "") in world


@pytest.mark.asyncio
async def test_a_zone_drawn_tighter_than_a_phone_can_resolve_does_not_flap(world):
    """A 40 m zone.home and an indoor fix 200 m out flapped "left Home" all day.

    The drawn radius is floored for the presence decision only, so an uncertain
    fix near a tight zone is not a departure; a genuinely distant one still is.
    """
    r = R()
    t = time.time()
    geo._zone_cache.update({"at": time.time(), "zones": [{**HOME, "radius": 40.0}, WORK]})
    try:
        await geo._presence_transition(r, "jeremiah", 33.1667, -111.5646, t)          # home
        # 195 m out, and the phone admits 76 m of uncertainty: not a departure.
        await geo._presence_transition(r, "jeremiah", 33.168455, -111.5646, t + 60, acc=76.0)
        assert world == []
        # A kilometre away, though, is a real departure.
        await geo._presence_transition(r, "jeremiah", 33.1757, -111.5646, t + 120, acc=10.0)
        await geo._presence_transition(r, "jeremiah", 33.1800, -111.5646, t + 120 + geo.DEPART_DWELL_S, acc=10.0)
        assert ("michele", "Jeremiah left Home", "") in world
    finally:
        geo._zone_cache.update({"at": time.time(), "zones": [HOME, WORK]})


@pytest.mark.asyncio
async def test_a_tight_home_zone_does_not_flap_on_a_bad_fix(world):
    """``zone.home`` here is drawn 40 m across while an indoor fix can sit 200 m
    out, which flapped "left Home" / "arrived at Home" all day. A zone tighter
    than a phone can resolve is widened for the presence decision, so the same
    phone reads as home while a real trip away still counts."""
    r = R()
    t = time.time()
    geo._zone_cache.update({"at": time.time(), "zones": [{**HOME, "radius": 40.0}, WORK]})
    await geo._presence_transition(r, "michele", 33.1667, -111.5646, t)              # home
    # 195 m out with 76 m of uncertainty: still home, the zone is only 40 m wide
    await geo._presence_transition(r, "michele", 33.168452, -111.5646, t + 30, acc=76.0)
    assert world == []
    # 500 m out is a real departure, however tight the zone was drawn
    await geo._presence_transition(r, "michele", 33.171191, -111.5646, t + 60, acc=76.0)
    await geo._presence_transition(r, "michele", 33.171191, -111.5646, t + 60 + geo.DEPART_DWELL_S, acc=76.0)
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
    # Leave work, come back, leave again inside ten minutes: one notice.
    for dt, (lat, lon) in [(0, (33.30, -111.80)), (60, (33.25, -111.70)), (260, (33.25, -111.70)),
                           (320, (33.30, -111.80)), (450, (33.30, -111.80)),
                           (500, (33.25, -111.70)), (700, (33.25, -111.70))]:
        await geo._presence_transition(r, "jeremiah", lat, lon, t + dt)
    departures = [s for s in world if s[1] == "Jeremiah left Work"]
    assert len(departures) == 1


@pytest.mark.asyncio
async def test_presence_notices_are_family_presence_with_a_map_button(world):
    r = R()
    t = time.time()
    await geo._presence_transition(r, "michele", 33.25, -111.70, t)
    await geo._presence_transition(r, "michele", 33.1668, -111.5646, t + 600)
    await geo._presence_transition(r, "michele", 33.1668, -111.5646, t + 750)   # still there
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
    await geo._presence_transition(r, "leo", 33.30, -111.80, t + 200)
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
    await geo._presence_transition(r, "jeremiah", 33.25, -111.70, t + 60 + geo.DEPART_DWELL_S)
    assert [(n["user"], n["category"]) for n in world.payloads] == [("michele", "family_safety")]


@pytest.mark.asyncio
async def test_child_geofence_arrival_and_departure_alerts(world, monkeypatch):
    async def mock_children():
        return {
            "noah": {
                "username": "noah",
                "display_name": "Noah",
                "parents": ["jeremiah", "michele"],
                "geofence_alerts": {
                    "zone.work": {"arrive": True, "depart": True},
                },
            }
        }
    monkeypatch.setattr(geo, "_guardian_children_cached", mock_children)
    r = R()
    t = time.time()
    # Noah starts outside
    await geo._presence_transition(r, "noah", 33.25, -111.70, t)
    # Noah arrives at zone.work
    await geo._presence_transition(r, "noah", 33.30, -111.80, t + 60)
    await geo._presence_transition(r, "noah", 33.30, -111.80, t + 200)
    by_user = {n["user"]: n for n in world.payloads}
    assert "jeremiah" in by_user and "michele" in by_user
    assert by_user["jeremiah"]["category"] == "family_safety"
    assert by_user["jeremiah"]["title"] == "Noah arrived at Work"
    assert by_user["jeremiah"]["data"]["geofence"] is True

    # Noah leaves zone.work
    world.payloads.clear()
    world.clear()
    await geo._presence_transition(r, "noah", 33.25, -111.70, t + 320)
    await geo._presence_transition(r, "noah", 33.25, -111.70, t + 320 + geo.DEPART_DWELL_S)
    by_user = {n["user"]: n for n in world.payloads}
    assert "jeremiah" in by_user and "michele" in by_user
    assert by_user["jeremiah"]["category"] == "family_safety"
    assert by_user["jeremiah"]["title"] == "Noah left Work"
    assert "Home in about 22 min by road" in by_user["jeremiah"]["body"]



@pytest.mark.asyncio
async def test_a_parent_who_does_not_share_location_still_hears(world, monkeypatch):
    """Recipients were only people with a breadcrumb history; a parent whose
    phone does not run tracking never heard about anyone."""

    async def family(**_kw):
        return ["dana", "jeremiah", "kate", "michele"]

    monkeypatch.setattr(geo.notify, "family", family)
    r = R()
    t = time.time()
    await geo._presence_transition(r, "michele", 33.25, -111.70, t)
    await geo._presence_transition(r, "michele", 33.1668, -111.5646, t + 600)
    await geo._presence_transition(r, "michele", 33.1668, -111.5646, t + 750)   # still there
    assert ("dana", "Michele arrived at Home", "") in world
    assert not any(user == "kate" for user, *_ in world)  # still not in the circle


@pytest.mark.asyncio
async def test_driving_past_a_place_does_not_say_you_arrived(world):
    r = R()
    t = time.time()
    await geo._presence_transition(r, "michele", 33.25, -111.70, t)
    # One fix inside Work at 30 mph, the next one already past it.
    assert await geo._presence_transition(r, "michele", 33.30, -111.80, t + 20, speed=13.0) == []
    assert await geo._presence_transition(r, "michele", 33.31, -111.82, t + 40, speed=13.0) == []
    assert world == []
    # The look two minutes later finds nothing pending.
    assert await geo._confirm_pending_arrival(r, "michele", "zone.work", t + 20, now=t + 200) is False
    assert world == []


@pytest.mark.asyncio
async def test_a_pending_arrival_is_not_counted_while_still_travelling(world):
    r = R()
    t = time.time()
    await geo._presence_transition(r, "michele", 33.25, -111.70, t)
    await geo._presence_transition(r, "michele", 33.30, -111.80, t + 20, speed=13.0)
    # Still inside 3 minutes later but moving at speed (a big place, a long road).
    assert await geo._presence_transition(r, "michele", 33.3005, -111.8005, t + 200, speed=12.0) == []
    assert world == []


@pytest.mark.asyncio
async def test_arriving_on_foot_counts_at_once(world):
    r = R()
    t = time.time()
    await geo._presence_transition(r, "michele", 33.25, -111.70, t)
    events = await geo._presence_transition(r, "michele", 33.1668, -111.5646, t + 600, speed=0.4, activity="still")
    assert [e["kind"] for e in events] == ["arrived"]
    assert ("jeremiah", "Michele arrived at Home", "") in world


@pytest.mark.asyncio
async def test_a_phone_that_goes_quiet_after_arriving_is_counted_by_the_later_look(world):
    r = R()
    t = time.time()
    await geo._presence_transition(r, "michele", 33.25, -111.70, t)
    await geo._presence_transition(r, "michele", 33.1668, -111.5646, t + 600)
    assert world == [] and len(world.later) == 1
    # Too soon: still pending.
    assert await geo._confirm_pending_arrival(r, "michele", "zone.home", t + 600, now=t + 650) is False
    assert await geo._confirm_pending_arrival(r, "michele", "zone.home", t + 600, now=t + 730) is True
    assert ("jeremiah", "Michele arrived at Home", "") in world
    # Counted once: the next fix at home is no change.
    world.clear()
    assert await geo._presence_transition(r, "michele", 33.1668, -111.5646, t + 900) == []
    assert world == []


def _active_trip(user, start):
    return json.dumps({
        "id": f"trip_{user}_{int(start)}",
        "user_id": user,
        "user_name": user.title(),
        "start_time": start,
        "last_moving_time": start + 600,
        "last_point_time": start + 600,
        "start_location": {"name": "Work", "latitude": 33.30, "longitude": -111.80},
        "end_location": {"name": "Home", "latitude": 33.1667, "longitude": -111.5646},
        "distance_miles": 14.2,
        "status": "in_progress",
        "vehicle_name": None,
    })


async def _drive_home_arrive(r, person, t):
    await geo._presence_transition(r, person, 33.25, -111.70, t)             # away, silent
    await geo._presence_transition(r, person, 33.1668, -111.5646, t + 600)   # home
    await geo._presence_transition(r, person, 33.1668, -111.5646, t + 750)   # still home


@pytest.mark.asyncio
async def test_a_shared_drive_does_not_notify_each_traveler(world):
    """Two people on the same drive home: nobody gets an arrival notice for
    the person they rode with."""
    r = R()
    t = time.time()
    for user in ("jeremiah", "michele"):
        r.kv[f"geo:active_trip:{user}"] = _active_trip(user, t - 1800)
    await _drive_home_arrive(r, "jeremiah", t)
    assert world == []


@pytest.mark.asyncio
async def test_a_solo_drive_still_notifies_the_family(world):
    r = R()
    t = time.time()
    r.kv["geo:active_trip:jeremiah"] = _active_trip("jeremiah", t - 1800)   # michele not driving
    await _drive_home_arrive(r, "jeremiah", t)
    assert ("michele", "Jeremiah arrived at Home", "") in world


@pytest.mark.asyncio
async def test_a_shared_drive_whose_trip_already_finalized_still_suppresses(world):
    r = R()
    t = time.time()
    mine = json.loads(_active_trip("jeremiah", t - 1800))
    theirs = json.loads(_active_trip("michele", t - 1790))
    for trip in (mine, theirs):
        trip.pop("last_moving_time")
        trip.pop("last_point_time")
        trip["end_time"] = trip["start_time"] + 600
        trip["status"] = "completed"
    for trip in (mine, theirs):
        r.kv[f"geo:trip:{trip['id']}"] = json.dumps(trip)
        await r.zadd(f"geo:trips:user:{trip['user_id']}", {trip["id"]: trip["start_time"]})
        await r.zadd("geo:trips:all", {trip["id"]: trip["start_time"]})
    await _drive_home_arrive(r, "jeremiah", t)
    assert ("michele", "Jeremiah arrived at Home", "") not in world


@pytest.mark.asyncio
async def test_a_phantom_trip_while_the_phone_sits_at_home_says_nothing(world):
    """Noah's phone, on the nightstand overnight, "drove" 1 km east in 48 s
    (the fused provider extrapolating, accuracy growing 40 -> 93 m) and was
    back home 2.5 minutes later; it did that 27 times in one night, and each
    told the family he had left. Leaving now takes staying out."""
    r = R()
    t = time.time()
    await geo._presence_transition(r, "noah", 33.1667, -111.5646, t)                       # home
    for i, (lon, acc) in enumerate([(-111.5621, 40.0), (-111.5594, 52.0), (-111.5569, 64.0), (-111.5547, 93.0)]):
        assert await geo._presence_transition(r, "noah", 33.1647, lon, t + 16 * (i + 1), acc=acc, speed=20.0) == []
    # Back home 2.5 minutes on: it never left.
    assert await geo._presence_transition(r, "noah", 33.1667, -111.5646, t + 150, acc=15.0) == []
    # Even a later stray fix outside starts afresh rather than counting the old one.
    assert await geo._presence_transition(r, "noah", 33.1647, -111.5621, t + 400, acc=20.0) == []
    assert world == []


@pytest.mark.asyncio
async def test_a_phantom_followed_by_silence_is_still_not_a_departure(world):
    """05:29 the phone "drove" 1 km out, then sent nothing for 2.5 minutes and
    came back home. The silence is not proof of a trip; only a fix still
    outside after the dwell is."""
    r = R()
    t = time.time()
    await geo._presence_transition(r, "noah", 33.1667, -111.5646, t)                       # home
    await geo._presence_transition(r, "noah", 33.1625, -111.5547, t + 48, acc=93.0, speed=17.0)
    assert await geo._presence_transition(r, "noah", 33.1666, -111.5643, t + 200, acc=15.0) == []
    assert world == []
