"""Travel times: on the roads, on foot, between two places, and to a person.

The point of these tests is the pair of mistakes that made "how long will it
take me to get to Nellis auction?" answer with a location summary and then a
plain "I have no mapping tool": a journey between two named places had no way
in at all, and a walking ETA would have been asked of the driving network.
"""
import time

import pytest
from fastapi import HTTPException

import services.geo.main as geo
import services.geo.map_match as mm

HOME = {"entity_id": "zone.home", "name": "Home", "latitude": 39.7392, "longitude": -104.9903, "radius": 100.0}
WORK = {"entity_id": "zone.work", "name": "Work", "latitude": 39.8725, "longitude": -105.2257, "radius": 150.0}
HIDEOUT = {"entity_id": "place.7", "name": "The Hideout", "latitude": 39.5, "longitude": -105.0,
           "radius": 50.0, "private": True, "owner": "alex"}


class _Resp:
    def __init__(self, payload: dict):
        self._payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    async def json(self, content_type=None):
        return self._payload


class _Session:
    """A stand-in for aiohttp's session that records what OSRM was asked."""

    def __init__(self, payload: dict, seen: list[str]):
        self._payload = payload
        self._seen = seen

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    def get(self, url, params=None, timeout=None):
        self._seen.append(url)
        return _Resp(self._payload)


def _osrm(monkeypatch, payload: dict) -> list[str]:
    """Point map_match at a canned OSRM answer, and return the URLs it asks for."""
    seen: list[str] = []
    monkeypatch.setattr(mm.aiohttp, "ClientSession", lambda: _Session(payload, seen))
    return seen


class _Redis:
    """Enough of Redis for the presence keys the ETA code reads."""

    def __init__(self, history: dict[str, list[dict]] | None = None):
        self._history = {k: [dict(f) for f in v] for k, v in (history or {}).items()}

    async def zrevrange(self, key, start, end):
        import json

        fixes = self._history.get(key.split(":", 2)[-1], [])
        fixes.sort(key=lambda f: f.get("t", 0), reverse=True)
        return [json.dumps(f) for f in fixes[: end + 1]]


# --- the OSRM profile is part of the URL -------------------------------------

async def test_a_drive_is_asked_of_the_road_network(monkeypatch):
    seen = _osrm(monkeypatch, {"code": "Ok", "routes": [{"distance": 4828.0, "duration": 900.0}]})
    out = await mm.route((39.7392, -104.9903), (39.8725, -105.2257), "http://osrm:5000/")
    assert out == {"distance_m": 4828.0, "duration_s": 900.0}
    assert seen[0].startswith("http://osrm:5000/route/v1/driving/")


async def test_a_walk_is_asked_of_the_foot_network(monkeypatch):
    seen = _osrm(monkeypatch, {"code": "Ok", "routes": [{"distance": 1200.0, "duration": 860.0}]})
    out = await mm.route((39.7392, -104.9903), (39.7420, -104.9860), "http://osrm-foot:5000/", profile="foot")
    assert out == {"distance_m": 1200.0, "duration_s": 860.0}
    assert seen[0].startswith("http://osrm-foot:5000/route/v1/foot/")


async def test_who_is_closest_uses_the_profile_it_was_given(monkeypatch):
    seen = _osrm(monkeypatch, {"code": "Ok", "durations": [[600.0], [900.0]]})
    out = await mm.table([(39.7, -104.9), (39.8, -105.2)], (39.7392, -104.9903), "http://osrm-foot", profile="foot")
    assert out == [600.0, 900.0]
    assert "/table/v1/foot/" in seen[0]


def test_a_mode_picks_its_own_server_and_name(monkeypatch):
    monkeypatch.setattr(geo, "OSRM_URL", "http://road")
    monkeypatch.setattr(geo, "OSRM_FOOT_URL", "http://foot")
    for mode in ("walking", "walk", "on foot", "  Foot  "):
        assert geo.eta_route(mode) == ("http://foot", "foot", "walking"), mode
    for mode in ("driving", "drive", None, "", "teleport"):
        assert geo.eta_route(mode) == ("http://road", "driving", "driving"), mode


# --- whose places a name may reach -------------------------------------------

async def _places(monkeypatch, places):
    async def zones():
        return places

    async def geocode(text, near=None):
        return None

    monkeypatch.setattr(geo, "_zones", zones)
    monkeypatch.setattr(geo, "_geocode_place", geocode)


@pytest.mark.parametrize("viewer,expected", [("alex", "The Hideout"), ("Sam", None), (None, None)])
async def test_a_private_place_is_only_found_by_its_owner(monkeypatch, viewer, expected):
    await _places(monkeypatch, [HOME, WORK, HIDEOUT])
    dest = await geo._destination("the hideout", viewer=viewer)
    assert (dest["name"] if dest else None) == expected


async def test_the_households_places_are_found_by_anyone(monkeypatch):
    await _places(monkeypatch, [HOME, WORK, HIDEOUT])
    for viewer in ("alex", "sam", None):
        assert (await geo._destination("home", viewer=viewer))["name"] == "Home"
        assert (await geo._destination("ZONE.WORK", viewer=viewer))["name"] == "Work"


async def test_a_coordinate_pair_is_a_place(monkeypatch):
    await _places(monkeypatch, [HOME])
    dest = await geo._destination("39.5, -105.25")
    assert (dest["latitude"], dest["longitude"]) == (39.5, -105.25)
    assert dest["radius"] == 50.0


# --- a person's travel time ---------------------------------------------------

def _route_to(monkeypatch, distance_m=16093.0, duration_s=1500.0):
    calls: dict = {}

    async def route(a, b, server, profile="driving", timeout_s=5.0):
        calls.update(a=a, b=b, server=server, profile=profile)
        return {"distance_m": distance_m, "duration_s": duration_s}

    monkeypatch.setattr(geo.map_match, "route", route)
    monkeypatch.setattr(geo, "OSRM_URL", "http://road")
    monkeypatch.setattr(geo, "OSRM_FOOT_URL", "http://foot")
    return calls


def _fix_at(monkeypatch, lat=39.70, lon=-104.95, spd=0.0):
    # A few miles out, so Home is a drive away rather than "arrived".
    async def get_redis():
        return _Redis()

    async def latest(r, clean):
        return {"lat": lat, "lon": lon, "t": time.time(), "spd": spd}

    monkeypatch.setattr(geo, "get_redis", get_redis)
    monkeypatch.setattr(geo, "_latest_fix", latest)


async def test_a_walk_says_walking_and_uses_the_foot_network(monkeypatch):
    _fix_at(monkeypatch)
    calls = _route_to(monkeypatch, distance_m=1200.0, duration_s=860.0)

    async def dest(to, near=None, viewer=None):
        return HOME

    monkeypatch.setattr(geo, "_destination", dest)
    out = await geo.compute_eta("alex", "home", viewer="alex", mode="walking")
    assert out["mode"] == "walking"
    assert out["duration_s"] == 860
    assert (calls["server"], calls["profile"]) == ("http://foot", "foot")


async def test_a_drive_is_the_default(monkeypatch):
    _fix_at(monkeypatch)
    calls = _route_to(monkeypatch)

    async def dest(to, near=None, viewer=None):
        return WORK

    monkeypatch.setattr(geo, "_destination", dest)
    out = await geo.compute_eta("alex", "work")
    assert out["mode"] == "driving"
    assert (calls["server"], calls["profile"]) == ("http://road", "driving")


async def test_someone_elses_private_place_is_not_named_on_their_behalf(monkeypatch):
    _fix_at(monkeypatch)
    _route_to(monkeypatch)
    seen: dict = {}

    async def dest(to, near=None, viewer=None):
        seen["viewer"] = viewer
        return HOME

    monkeypatch.setattr(geo, "_destination", dest)
    # Alex asks where Sam is heading; only Sam's own places may be matched.
    await geo.compute_eta("sam", "the hideout", viewer="alex")
    assert seen["viewer"] is None
    await geo.compute_eta("sam", "the hideout", viewer="sam")
    assert seen["viewer"] == "sam"


async def test_an_unreachable_road_says_so_plainly(monkeypatch):
    _fix_at(monkeypatch)

    async def dest(to, near=None, viewer=None):
        return HOME

    async def no_route(*_a, **_kw):
        return None

    monkeypatch.setattr(geo, "_destination", dest)
    monkeypatch.setattr(geo.map_match, "route", no_route)
    with pytest.raises(HTTPException) as err:
        await geo.compute_eta("alex", "home")
    assert err.value.status_code == 503
    assert "travel time" in err.value.detail


# --- a journey between two places --------------------------------------------

async def test_two_places_can_be_compared_without_anyone_position(monkeypatch):
    calls = _route_to(monkeypatch, distance_m=48280.0, duration_s=1800.0)

    async def dest(to, near=None, viewer=None):
        return WORK if to == "work" else {**HOME, "name": "Airport"}

    monkeypatch.setattr(geo, "_destination", dest)
    out = await geo.compute_place_eta("work", "airport", viewer="alex", mode="driving")
    assert (out["from"], out["to"], out["mode"]) == ("Work", "Airport", "driving")
    assert out["duration_s"] == 1800
    assert out["distance_m"] == 48280
    assert out["eta"] > time.time()
    assert calls["a"] == (WORK["latitude"], WORK["longitude"])


async def test_a_journey_to_the_same_place_arrives(monkeypatch):
    _route_to(monkeypatch)

    async def dest(to, near=None, viewer=None):
        return WORK

    monkeypatch.setattr(geo, "_destination", dest)
    out = await geo.compute_place_eta("work", "work")
    assert out["arrived"] is True
    assert out["duration_s"] == 0


async def test_an_unknown_end_names_itself(monkeypatch):
    async def dest(to, near=None, viewer=None):
        return WORK if to == "work" else None

    monkeypatch.setattr(geo, "_destination", dest)
    with pytest.raises(HTTPException) as err:
        await geo.compute_place_eta("work", "nowhere in particular")
    assert err.value.status_code == 404
    assert "nowhere in particular" in err.value.detail


# --- the internal /eta route --------------------------------------------------

async def test_the_eta_route_takes_two_places(monkeypatch):
    monkeypatch.setattr(geo, "_verify_internal_secret", lambda header: True)
    called: dict = {}

    async def place(origin, to, viewer=None, mode="driving"):
        called.update(origin=origin, to=to, viewer=viewer, mode=mode)
        return {"from": "Work", "to": "Airport", "mode": "walking"}

    monkeypatch.setattr(geo, "compute_place_eta", place)
    out = await geo.get_place_eta(to="Airport", origin="Work", from_place=None, user=None, person=None,
                                  mode="walking", viewer="alex", is_admin=None, x_internal_secret="s")
    assert out["from"] == "Work"
    assert called == {"origin": "Work", "to": "Airport", "viewer": "alex", "mode": "walking"}


async def test_the_eta_route_accepts_from_as_origin(monkeypatch):
    monkeypatch.setattr(geo, "_verify_internal_secret", lambda header: True)
    called: dict = {}

    async def place(origin, to, viewer=None, mode="driving"):
        called.update(origin=origin, to=to)
        return {}

    monkeypatch.setattr(geo, "compute_place_eta", place)
    await geo.get_place_eta(to="the airport", origin=None, from_place="work", user=None, person=None,
                            mode="driving", viewer=None, is_admin=None, x_internal_secret="s")
    assert called == {"origin": "work", "to": "the airport"}


async def test_the_eta_route_reads_a_persons_latest_fix(monkeypatch):
    monkeypatch.setattr(geo, "_verify_internal_secret", lambda header: True)
    seen: dict = {}

    async def may_view(viewer, target, is_admin):
        seen["asked"] = (viewer, target)
        return target.strip().lower()

    async def eta(clean, to="home", viewer=None, mode="driving"):
        return {"user_id": clean, "to": to, "mode": mode}

    monkeypatch.setattr(geo, "_require_may_view", may_view)
    monkeypatch.setattr(geo, "compute_eta", eta)
    out = await geo.get_place_eta(to="home", origin=None, from_place=None, user="Sam", person=None,
                                  mode="walking", viewer="alex", is_admin=None, x_internal_secret="s")
    assert out == {"user_id": "sam", "to": "home", "mode": "walking"}
    assert seen["asked"] == ("alex", "Sam")


async def test_a_journey_needs_an_end(monkeypatch):
    monkeypatch.setattr(geo, "_verify_internal_secret", lambda header: True)
    with pytest.raises(HTTPException) as err:
        await geo.get_place_eta(to="home", origin=None, from_place=None, user=None, person=None,
                                mode="driving", viewer=None, is_admin=None, x_internal_secret="s")
    assert err.value.status_code == 400


async def test_the_eta_route_is_internal_only(monkeypatch):
    monkeypatch.setattr(geo, "_verify_internal_secret", lambda header: False)
    with pytest.raises(HTTPException) as err:
        await geo.get_place_eta(to="home", origin="work", from_place=None, user=None, person=None,
                                mode="driving", viewer=None, is_admin=None, x_internal_secret=None)
    assert err.value.status_code == 403


# --- who is closest -----------------------------------------------------------

async def test_who_is_closest_can_be_asked_on_foot(monkeypatch):
    monkeypatch.setattr(geo, "_verify_internal_secret", lambda header: True)
    now = time.time()
    redis = _Redis({"alex": [{"lat": 39.70, "lon": -104.95, "t": now}]})

    async def get_redis():
        return redis

    async def tracked(r):
        return ["alex"]

    async def may_view(viewer, target, is_admin):
        return target

    async def table(sources, destination, server, profile="driving"):
        assert profile == "foot"
        assert server == "http://foot"
        return [420.0]

    monkeypatch.setattr(geo, "get_redis", get_redis)
    monkeypatch.setattr(geo, "_tracked_users", tracked)
    monkeypatch.setattr(geo, "_require_may_view", may_view)
    monkeypatch.setattr(geo, "map_match", mm)
    monkeypatch.setattr(mm, "table", table)
    monkeypatch.setattr(geo, "OSRM_URL", "http://road")
    monkeypatch.setattr(geo, "OSRM_FOOT_URL", "http://foot")

    async def dest(to, near=None, viewer=None):
        return HOME

    monkeypatch.setattr(geo, "_destination", dest)
    out = await geo.get_closest(to="home", mode="walking", viewer="alex", is_admin=None, x_internal_secret="s")
    assert out["mode"] == "walking"
    assert out["people"][0]["duration_s"] == 420
