"""Saved places and visit suggestions through the gateway (docs/GEO_SERVICE.md).

A saved place is one the family adds from Wander. The gateway pins it to the
caller, only lets the owner or an admin change it, and only lets the notify list
name family members. Visit suggestions are always the caller's own.
"""
import pytest
from fastapi.testclient import TestClient

from services.gateway import family_safety


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


@pytest.fixture
def proxy(monkeypatch):
    from services.gateway import main

    calls = []
    answer = {"status": 200, "body": {"places": []}}

    async def caller(request):
        return "leo"

    async def identity(request):
        return {"user": "leo", "is_admin": False}

    class Family:
        async def family(self):
            return ["dad", "michele", "leo"]

    class Client:
        def request(self, method, url, params=None, json=None, **kw):
            calls.append((method, url, params, json))
            return _Resp(answer["status"], answer["body"])

    monkeypatch.setattr(main, "_require_authenticated", caller)
    # family_safety imports this inside the function, so patch it on main.
    monkeypatch.setattr(main, "_resolve_identity_from_request", identity)
    monkeypatch.setattr(family_safety, "notify", Family())
    monkeypatch.setattr(family_safety, "get_client", lambda: Client())
    return TestClient(main.app), calls, answer


def test_listing_places_asks_for_the_callers_places(proxy):
    client, calls, answer = proxy
    answer["body"] = {"places": [{"id": "a" * 32, "name": "Gym"}]}
    resp = client.get("/api/geo/saved-places")
    assert resp.status_code == 200
    method, url, params, _ = calls[-1]
    assert method == "GET" and url.endswith("/places")
    assert params == {"viewer": "leo"}
    assert resp.json()["places"][0]["name"] == "Gym"


def test_adding_a_place_pins_it_to_the_caller(proxy):
    client, calls, answer = proxy
    answer["body"] = {"place": {"id": "a" * 32}}
    resp = client.post(
        "/api/geo/saved-places",
        json={"name": "Gym", "lat": 33.4, "lon": -111.9, "scope": "family", "important": True, "notify": ["michele"]},
    )
    assert resp.status_code == 200
    method, url, params, body = calls[-1]
    assert method == "POST" and url.endswith("/places")
    assert params == {"owner": "leo"}
    assert body == {"name": "Gym", "lat": 33.4, "lon": -111.9, "scope": "family", "important": True, "notify": ["michele"]}


def test_the_notify_list_must_name_family_members(proxy):
    client, calls, _ = proxy
    resp = client.post("/api/geo/saved-places", json={"name": "Gym", "lat": 1, "lon": 2, "notify": ["stranger"]})
    assert resp.status_code == 422
    assert "Not in the family" in resp.json()["detail"]
    assert calls == []


def test_only_the_owner_or_an_admin_may_change_a_place(proxy):
    client, calls, answer = proxy
    answer["body"] = {"places": [{"id": "a" * 32, "name": "Gym", "owner": "michele"}]}
    resp = client.put(f"/api/geo/saved-places/{'a' * 32}", json={"important": True})
    assert resp.status_code == 403
    assert "who added" in resp.json()["detail"]
    assert calls[-1][0] == "GET"  # it looked the place up before refusing

    answer["body"] = {"places": []}
    assert client.delete(f"/api/geo/saved-places/{'b' * 32}").status_code == 404


def test_a_change_forwards_only_what_was_sent(proxy):
    client, calls, answer = proxy
    answer["body"] = {"places": [{"id": "a" * 32, "owner": "leo"}]}
    resp = client.put(f"/api/geo/saved-places/{'a' * 32}", json={"important": True, "notify": ["dad"]})
    assert resp.status_code == 200
    method, url, _params, body = calls[-1]
    assert method == "PUT" and url.endswith(f"/places/{'a' * 32}")
    assert body == {"important": True, "notify": ["dad"]}


def test_removing_a_place_goes_through_once_the_owner_owns_it(proxy):
    client, calls, answer = proxy
    answer["body"] = {"places": [{"id": "a" * 32, "owner": "leo"}]}
    assert client.delete(f"/api/geo/saved-places/{'a' * 32}").status_code == 200
    method, url, _, _ = calls[-1]
    assert method == "DELETE" and url.endswith(f"/places/{'a' * 32}")


def test_visit_suggestions_are_the_callers_own(proxy):
    client, calls, answer = proxy
    answer["body"] = {"suggestions": [{"lat": 1, "lon": 2, "count": 3, "place": "Corner Cafe"}]}
    resp = client.get("/api/geo/visit-suggestions")
    assert resp.status_code == 200
    assert calls[-1][2] == {"user": "leo"}
    assert resp.json()["suggestions"][0]["place"] == "Corner Cafe"


def test_dismissing_a_suggestion_forwards_the_point(proxy):
    client, calls, _ = proxy
    assert client.post("/api/geo/visit-suggestions/dismiss", json={"lat": 1, "lon": 2}).status_code == 200
    method, url, params, body = calls[-1]
    assert method == "POST" and url.endswith("/visit-suggestions/dismiss")
    assert params == {"user": "leo"}
    assert body == {"lat": 1, "lon": 2}
    assert client.post("/api/geo/visit-suggestions/dismiss", json="nope").status_code == 422
