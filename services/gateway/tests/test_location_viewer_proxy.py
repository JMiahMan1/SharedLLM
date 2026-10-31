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


class _Post:
    def __init__(self, status):
        self.status = status

    async def json(self):
        return {}


def _posting(monkeypatch, status):
    async def acting(request):
        return {"user": "leo", "is_admin": False}
    monkeypatch.setattr(gateway_main, "_acting_identity", acting)

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, **kw):
            return _Post(status)

    monkeypatch.setattr(gateway_main, "shared_http_client", lambda: _Client())


def test_a_fix_identity_refuses_is_a_4xx_so_the_phone_drops_it(monkeypatch):
    _posting(monkeypatch, 422)
    assert TestClient(app).post("/api/users/leo/location", json={"latitude": "x"}).status_code == 422
    assert TestClient(app).post("/api/users/location", json={"latitude": "x"}).status_code == 422


def test_identity_down_is_still_a_5xx_so_the_phone_keeps_the_fix(monkeypatch):
    _posting(monkeypatch, 503)
    assert TestClient(app).post("/api/users/leo/location", json={"latitude": 1, "longitude": 2}).status_code == 502


def test_a_driving_event_goes_to_geo_as_the_signed_in_person(monkeypatch):
    posted = []

    async def acting(request):
        return {"user": "zachary", "is_admin": False}

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None, **kw):
            posted.append((url, json))
            return _Post(200)

    async def to_execution(request, path, payload, as_user=None):
        return {"status": "SUCCESS"}

    monkeypatch.setattr(gateway_main, "_acting_identity", acting)
    monkeypatch.setattr(gateway_main, "shared_http_client", lambda: _Client())
    monkeypatch.setattr(gateway_main, "_proxy_execution_with_identity", to_execution)
    body = {"feature": "driving", "event": "hard_brake", "detail": {"at": 1, "from_mph": 40, "to_mph": 20}, "as_user": "someone_else"}
    assert TestClient(app).post("/api/activity", json=body).status_code == 200
    url, sent = posted[0]
    assert url.endswith("/driving-events")
    assert sent == {"user": "zachary", "event": "hard_brake", "detail": {"at": 1, "from_mph": 40, "to_mph": 20}}
