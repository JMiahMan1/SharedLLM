"""Saved places and visit suggestions through the gateway (docs/GEO_SERVICE.md).

A saved place is one the family adds from Wander, kept on HomeHub (the only
store of places). The gateway pins it to the caller, only lets the owner or an
admin change it, never shows anyone (an admin included) someone else's private
place, and only lets the notify list name family members. Visit suggestions are
always the caller's own. Every name, id and coordinate here is made up.
"""
import pytest
from fastapi.testclient import TestClient

from services.gateway import discovery_routes, family_safety


class _Resp:
    def __init__(self, status, body):
        self.status = status
        self._body = body

    async def json(self, content_type=None):
        return self._body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class FakeHub:
    """HomeHub's /hub/zones as the internal secret sees it: ``viewer`` hides others' private places."""

    def __init__(self):
        self.zones = {}
        self.calls = []

    def add(self, zid, name, owner, visibility="family"):
        self.zones[zid] = {"id": zid, "entity_id": f"zone.{name.lower()}" if visibility == "family" else f"place.{zid}",
                           "name": name, "latitude": 1.0, "longitude": 2.0, "radius": 150.0, "source": "hub",
                           "owner": f"hub-{owner}" if owner else None, "owner_name": owner,
                           "visibility": visibility, "important": False, "notify": [], "pin": ""}

    def _sees(self, z, viewer):
        return z["visibility"] == "family" or z["owner_name"] == viewer

    async def __call__(self, method, path, *, params=None, json=None, timeout=None):
        self.calls.append((method, path, params, json))
        viewer = (params or {}).get("viewer")
        home = {"id": "home", "entity_id": "zone.home", "name": "Home", "source": "home", "owner_name": None,
                "visibility": "family"}
        if path == "/hub/zones" and method == "GET":
            return 200, {"zones": [home, *[z for z in self.zones.values() if self._sees(z, viewer)]]}
        if path == "/hub/zones" and method == "POST":
            zid = "n" * 32
            self.add(zid, json["name"], json.get("owner"), json.get("visibility", "family"))
            self.zones[zid].update({k: v for k, v in json.items() if k not in ("owner",)})
            return 200, self.zones[zid]
        zid = path.rsplit("/", 1)[-1]
        z = self.zones.get(zid)
        if z is None or not self._sees(z, viewer):
            return 404, {"status": "error", "message": "No such zone"}
        if method == "GET":
            return 200, z
        if method == "PATCH":
            z.update(json)
            return 200, z
        del self.zones[zid]
        return 200, {"status": "removed", "id": zid}


@pytest.fixture
def proxy(monkeypatch):
    from services.gateway import main

    geo_calls = []
    answer = {"status": 200, "body": {}}
    who = {"user": "leo", "is_admin": False}
    hub = FakeHub()

    async def caller(request):
        return who["user"]

    async def identity(request):
        return dict(who)

    class Family:
        async def family(self):
            return ["dad", "sam", "leo"]

    class Client:
        def request(self, method, url, params=None, json=None, **kw):
            geo_calls.append((method, url, params, json))
            return _Resp(answer["status"], answer["body"])

        def post(self, url, **kw):
            geo_calls.append(("POST", url, None, None))
            return _Resp(200, {"zones": 1})

    monkeypatch.setattr(main, "_require_authenticated", caller)
    # family_safety imports this inside the function, so patch it on main.
    monkeypatch.setattr(main, "_resolve_identity_from_request", identity)
    monkeypatch.setattr(family_safety, "notify", Family())
    monkeypatch.setattr(family_safety, "get_client", lambda: Client())
    monkeypatch.setattr(discovery_routes, "_hub", hub)
    return TestClient(main.app), hub, who, geo_calls, answer


def test_listing_shows_the_family_places_and_only_the_callers_private_ones(proxy):
    client, hub, _who, _geo, _ = proxy
    hub.add("a" * 32, "Gym", "sam")
    hub.add("b" * 32, "Den", "sam", "private")
    hub.add("c" * 32, "Studio", "leo", "private")
    resp = client.get("/api/geo/saved-places")
    assert resp.status_code == 200
    assert hub.calls[-1][2] == {"viewer": "leo"}
    places = {p["name"]: p for p in resp.json()["places"]}
    assert sorted(places) == ["Gym", "Studio"]  # Home is not a saved place; Sam's den is Sam's
    assert places["Studio"] == {"id": "c" * 32, "entity_id": "place." + "c" * 32, "name": "Studio", "lat": 1.0,
                                "lon": 2.0, "radius": 150.0, "scope": "user", "owner": "leo", "important": False,
                                "notify": [], "icon": ""}


def test_adding_a_place_pins_it_to_the_caller_on_homehub(proxy):
    client, hub, _who, geo_calls, _ = proxy
    resp = client.post("/api/geo/saved-places", json={
        "name": "Gym", "lat": 40.0, "lon": -105.4, "scope": "family", "important": True, "notify": ["sam"],
        "icon": "gym", "owner": "dad"})
    assert resp.status_code == 200, resp.text
    method, path, params, body = hub.calls[-1]
    assert (method, path, params) == ("POST", "/hub/zones", {"viewer": "leo"})
    assert body == {"name": "Gym", "latitude": 40.0, "longitude": -105.4, "radius": 150.0, "visibility": "family",
                    "pin": "gym", "important": True, "notify": ["sam"], "owner": "leo"}
    assert resp.json()["place"]["owner"] == "leo"
    assert geo_calls[-1][1].endswith("/zones/refresh")  # geo uses it at once


def test_a_bad_place_is_refused_before_homehub(proxy):
    client, hub, *_ = proxy
    for body in ({"name": "Gym", "lat": 1, "lon": 2, "notify": ["stranger"]}, {"lat": 1, "lon": 2},
                 {"name": "x", "lat": 91, "lon": 0}, {"name": "x", "lat": 1, "lon": 2, "scope": "everyone"}):
        assert client.post("/api/geo/saved-places", json=body).status_code == 422, body
    assert hub.calls == []


def test_a_radius_is_kept_in_range(proxy):
    client, hub, *_ = proxy
    client.post("/api/geo/saved-places", json={"name": "Big", "lat": 1, "lon": 2, "radius": 99999})
    assert hub.calls[-1][3]["radius"] == family_safety.MAX_RADIUS_M


def test_only_the_owner_or_an_admin_may_change_or_remove_a_shared_place(proxy):
    client, hub, who, _geo, _ = proxy
    hub.add("a" * 32, "Gym", "sam")
    resp = client.put(f"/api/geo/saved-places/{'a' * 32}", json={"important": True})
    assert resp.status_code == 403 and "who added" in resp.json()["detail"]
    assert client.delete(f"/api/geo/saved-places/{'a' * 32}").status_code == 403
    assert hub.calls[-1][0] == "GET" and "a" * 32 in hub.zones  # it looked, then refused
    # Home and a zone drawn for the household have no owner: only an admin changes them.
    assert client.delete("/api/geo/saved-places/home").status_code == 404
    who.update(user="dad", is_admin=True)
    assert client.put(f"/api/geo/saved-places/{'a' * 32}", json={"important": True}).status_code == 200
    assert hub.zones["a" * 32]["important"] is True
    assert client.delete(f"/api/geo/saved-places/{'a' * 32}").status_code == 200
    assert "a" * 32 not in hub.zones


def test_someone_elses_private_place_is_not_there_even_for_an_admin(proxy):
    client, hub, who, _geo, _ = proxy
    hub.add("b" * 32, "Den", "sam", "private")
    who.update(user="dad", is_admin=True)
    assert client.put(f"/api/geo/saved-places/{'b' * 32}", json={"name": "Mine"}).status_code == 404
    assert client.delete(f"/api/geo/saved-places/{'b' * 32}").status_code == 404
    assert "Den" not in [p["name"] for p in client.get("/api/geo/saved-places").json()["places"]]
    assert hub.zones["b" * 32]["name"] == "Den"


def test_a_change_forwards_only_what_was_sent(proxy):
    client, hub, *_ = proxy
    hub.add("a" * 32, "Gym", "leo")
    resp = client.put(f"/api/geo/saved-places/{'a' * 32}", json={"important": True, "notify": ["dad"]})
    assert resp.status_code == 200
    method, path, params, body = hub.calls[-1]
    assert (method, path, params) == ("PATCH", f"/hub/zones/{'a' * 32}", {"viewer": "leo"})
    assert body == {"important": True, "notify": ["dad"]}


def test_with_no_homehub_there_are_no_places(proxy, monkeypatch):
    client, *_ = proxy
    from fastapi import HTTPException

    async def off(*_a, **_k):
        raise HTTPException(status_code=503, detail="HomeHub is not set up.")

    monkeypatch.setattr(discovery_routes, "_hub", off)
    resp = client.get("/api/geo/saved-places")
    assert resp.status_code == 503 and "HomeHub" in resp.json()["detail"]
    assert client.post("/api/geo/saved-places", json={"name": "Gym", "lat": 1, "lon": 2}).status_code == 503


def test_visit_suggestions_are_the_callers_own(proxy):
    client, _hub, _who, geo_calls, answer = proxy
    answer["body"] = {"suggestions": [{"lat": 1, "lon": 2, "count": 3, "place": "Corner Cafe"}]}
    resp = client.get("/api/geo/visit-suggestions")
    assert resp.status_code == 200
    assert geo_calls[-1][2] == {"user": "leo"}
    assert resp.json()["suggestions"][0]["place"] == "Corner Cafe"


def test_dismissing_a_suggestion_forwards_the_point(proxy):
    client, _hub, _who, geo_calls, _ = proxy
    assert client.post("/api/geo/visit-suggestions/dismiss", json={"lat": 1, "lon": 2}).status_code == 200
    method, url, params, body = geo_calls[-1]
    assert method == "POST" and url.endswith("/visit-suggestions/dismiss")
    assert params == {"user": "leo"}
    assert body == {"lat": 1, "lon": 2}
    assert client.post("/api/geo/visit-suggestions/dismiss", json="nope").status_code == 422
