"""The family map's feeds carry the signed-in user, so Identity and geo can
leave out the people and private places that are not theirs to see."""
from fastapi.testclient import TestClient

from services.gateway import main as gateway_main
from services.gateway.main import app


class _Resp:
    status = 200

    async def json(self):
        return {}


def _as(monkeypatch, ident):
    async def acting(request):
        return ident
    monkeypatch.setattr(gateway_main, "_acting_identity", acting)
    sent = []

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, **kw):
            sent.append((url, kw.get("params")))
            return _Resp()

    monkeypatch.setattr(gateway_main, "shared_http_client", lambda: _Client())
    return sent


def test_the_location_feed_names_the_viewer(monkeypatch):
    sent = _as(monkeypatch, {"user": "michele", "is_admin": False})
    assert TestClient(app).get("/api/users/location/all").status_code == 200
    url, params = sent[0]
    assert url.endswith("/api/users/location/all")
    assert params == {"viewer": "michele", "is_admin": "false"}


def test_the_location_feed_needs_a_signed_in_user(monkeypatch):
    sent = _as(monkeypatch, None)
    assert TestClient(app).get("/api/users/location/all").status_code == 401
    assert sent == []


def test_places_are_read_as_the_viewer(monkeypatch):
    sent = _as(monkeypatch, {"user": "gracie", "is_admin": False})
    assert TestClient(app).get("/api/geo/zones").status_code == 200
    url, params = sent[0]
    assert url.endswith("/zones") and params == {"viewer": "gracie"}


def test_a_days_history_is_read_as_the_viewer(monkeypatch):
    sent = _as(monkeypatch, {"user": "michele", "is_admin": False})
    resp = TestClient(app).get("/api/geo/people/leo/day", params={"day": "2026-10-31"})
    assert resp.status_code == 200
    url, params = sent[0]
    assert url.endswith("/people/leo/day")
    assert params == {"day": "2026-10-31", "viewer": "michele", "is_admin": ""}


def test_a_days_history_refuses_a_path_in_the_name(monkeypatch):
    sent = _as(monkeypatch, {"user": "michele", "is_admin": False})
    assert TestClient(app).get("/api/geo/people/leo/day", params={"day": "../../x"}).status_code == 422
    assert sent == []
