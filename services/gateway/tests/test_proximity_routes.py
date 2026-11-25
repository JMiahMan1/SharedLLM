"""Gateway routes for family phones near each other: always the signed-in person."""
from fastapi.testclient import TestClient

from services.gateway.main import app
from services.gateway.tests.test_vehicle_bluetooth_routes import _patch_geo, as_user  # noqa: F401


def test_sightings_are_filed_under_the_signed_in_person(monkeypatch, as_user):  # noqa: F811
    calls = _patch_geo(monkeypatch, {})
    r = as_user("pat").post("/api/geo/proximity", json={"events": [{"person": "gracie"}], "observer": "alex"})
    assert r.status_code == 200
    assert calls[-1]["path"] == "/proximity/pat"
    assert calls[-1]["json"] == {"events": [{"person": "gracie"}]}


def test_the_key_needs_a_sign_in_and_rotating_it_a_parent(monkeypatch, as_user):  # noqa: F811
    _patch_geo(monkeypatch, {("get", "/proximity/config"): (200, {"key": "k"})})
    assert TestClient(app).get("/api/geo/proximity/config").status_code == 401
    assert as_user("pat").get("/api/geo/proximity/config").json() == {"key": "k"}
    assert as_user("pat").post("/api/geo/proximity/key/rotate").status_code == 403
    assert as_user("mom", is_admin=True).post("/api/geo/proximity/key/rotate").status_code == 200
