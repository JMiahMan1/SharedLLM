"""Rooms/Energy/History/Climate routes: the caller's hub credentials, device permissions, admin-only settings.

The hub is mocked at the HTTP layer; identity and the permission lookup are
faked. What matters: an entity the caller may not see is not there (in rooms,
thermostats, history, statistics and the energy breakdown), household totals
stay, the HA token never reaches the browser, and settings are for admins.
"""
import pytest
from conftest_media import DEFAULT_IDENTITY, TEST_HA_TOKEN, TEST_HA_URL, mock_upstream
from fastapi import HTTPException

from services.execution.entity_access import EntityPermissions
from services.gateway import dashboard_routes
from services.gateway import main as gateway_main


def _sent(upstream, prefix):
    """(url, kwargs) of every GET made to a URL starting with ``prefix``."""
    return [(str(url), call.kwargs) for (m, url), calls in upstream.requests.items()
            if m == "GET" and str(url).startswith(prefix) for call in calls]


def ent(eid, **kw):
    return {"entity_id": eid, "domain": eid.split(".")[0], "name": eid, "state": "on", "attributes": {}, **kw}


ROOMS = {"floors": [], "areas": [
    {"area_id": "kitchen", "name": "Kitchen", "floor_id": None, "icon": None,
     "entities": [ent("light.kitchen_main"), ent("lock.back_door")]},
    {"area_id": "study", "name": "Study", "floor_id": None, "icon": None, "entities": [ent("switch.study_fan")]},
], "unassigned": [ent("switch.porch"), ent("switch.secret_heater")]}


@pytest.fixture
def perms(monkeypatch):
    state = {"assigned": frozenset({"light.kitchen_main", "switch.porch", "climate.hall", "sensor.hall_temp",
                                    "sensor.dryer_energy", "sensor.dryer_power"})}

    async def load(username, is_admin=False):
        if is_admin:
            return EntityPermissions.everything()
        return EntityPermissions(assigned=state["assigned"])

    monkeypatch.setattr(dashboard_routes, "load_entity_permissions", load)
    return state


@pytest.fixture
def member(monkeypatch):
    async def resolve(body):
        return {**DEFAULT_IDENTITY, "is_admin": False}

    monkeypatch.setattr(gateway_main, "resolve_identity", resolve)


def test_rooms_follow_permissions_and_drop_empty_areas(client, upstream, perms, member):
    mock_upstream(upstream, "GET", f"{TEST_HA_URL}/api/homehub/dashboard/rooms", payload=ROOMS)
    resp = client.get("/api/dashboard/rooms")
    assert resp.status_code == 200
    body = resp.json()
    assert [a["area_id"] for a in body["areas"]] == ["kitchen"]
    assert [e["entity_id"] for e in body["areas"][0]["entities"]] == ["light.kitchen_main"]
    assert [e["entity_id"] for e in body["unassigned"]] == ["switch.porch"]
    assert TEST_HA_TOKEN not in resp.text and TEST_HA_URL not in resp.text
    (_url, kwargs), = _sent(upstream, f"{TEST_HA_URL}/api/homehub/dashboard/rooms")
    assert kwargs["headers"]["Authorization"] == f"Bearer {TEST_HA_TOKEN}"


def test_admins_see_every_room(client, upstream, perms):
    mock_upstream(upstream, "GET", f"{TEST_HA_URL}/api/homehub/dashboard/rooms", payload=ROOMS)
    assert len(client.get("/api/dashboard/rooms").json()["areas"]) == 2


def test_climate_filters_power_and_sensors(client, upstream, perms, member):
    mock_upstream(upstream, "GET", f"{TEST_HA_URL}/api/homehub/dashboard/climate", payload={"thermostats": [
        {**ent("climate.hall"), "power": [ent("sensor.hall_ac_power")], "sensors": [ent("sensor.hall_temp")]},
        {**ent("climate.attic"), "power": [], "sensors": []}]})
    (t,) = client.get("/api/dashboard/climate").json()["thermostats"]
    assert t["entity_id"] == "climate.hall" and t["power"] == [] and len(t["sensors"]) == 1


def test_history_drops_entities_the_caller_cannot_see(client, upstream, perms, member):
    mock_upstream(upstream, "GET", f"{TEST_HA_URL}/api/history/period/", payload=[
        [{"entity_id": "climate.hall", "state": "cool", "last_changed": "2026-11-10T00:00:00+00:00",
          "attributes": {"current_temperature": 77}}]])
    resp = client.get("/api/dashboard/history", params={"entity_ids": "climate.hall,lock.back_door",
                                                         "attributes": "true"})
    assert resp.status_code == 200
    assert resp.json()["series"]["climate.hall"][0] == {"t": "2026-11-10T00:00:00+00:00", "s": "cool",
                                                          "a": {"current_temperature": 77}}
    (_url, kwargs), = _sent(upstream, f"{TEST_HA_URL}/api/history/period/")
    assert kwargs["params"]["filter_entity_id"] == "climate.hall"


def test_history_limits(client, perms):
    assert client.get("/api/dashboard/history").status_code == 400
    many = ",".join(f"sensor.s{i}" for i in range(13))
    assert client.get("/api/dashboard/history", params={"entity_ids": many}).status_code == 400
    assert client.get("/api/dashboard/history", params={
        "entity_ids": "sensor.a", "start": "2026-01-01T00:00:00Z", "end": "2026-03-01T00:00:00Z"}).status_code == 400
    assert client.get("/api/dashboard/history", params={"entity_ids": "../etc"}).status_code == 400


def test_statistics_keep_household_meters_and_visible_entities(client, upstream, perms, member):
    mock_upstream(upstream, "GET", f"{TEST_HA_URL}/api/homehub/statistics", payload={"x": []})
    resp = client.get("/api/dashboard/statistics", params={
        "statistic_ids": "utility:acct_energy_consumption,sensor.dryer_energy,sensor.oven_energy", "period": "day"})
    assert resp.status_code == 200
    (_url, kwargs), = _sent(upstream, f"{TEST_HA_URL}/api/homehub/statistics")
    assert kwargs["params"]["statistic_ids"] == "utility:acct_energy_consumption,sensor.dryer_energy"


def test_energy_summary_hides_circuits_but_keeps_totals(client, upstream, perms, member):
    mock_upstream(upstream, "GET", f"{TEST_HA_URL}/api/homehub/energy/summary", payload={
        "grid": {"kwh": 30.0}, "utility": [{"name": "Utility", "kwh": 29.0}],
        "devices": [{"name": "Dryer", "stat": "sensor.dryer_energy", "kwh": 3.0, "power_entity": "sensor.dryer_power",
                     "power_w": 10.0},
                    {"name": "Oven", "stat": "sensor.oven_energy", "kwh": 2.0, "power_entity": "sensor.oven_power",
                     "power_w": 5.0}]})
    body = client.get("/api/dashboard/energy/summary", params={"range": "day"}).json()
    assert body["grid"]["kwh"] == 30.0 and body["utility"][0]["kwh"] == 29.0
    assert [d["name"] for d in body["devices"]] == ["Dryer"]
    assert body["devices"][0]["power_w"] == 10.0


def test_entities_picker_lists_visible_entities(client, upstream, perms, member):
    mock_upstream(upstream, "GET", f"{TEST_HA_URL}/api/states", payload=[
        {"entity_id": "sensor.hall_temp", "state": "71", "attributes": {"friendly_name": "Hall", "unit_of_measurement": "°F"}},
        {"entity_id": "lock.back_door", "state": "locked", "attributes": {}}])
    body = client.get("/api/dashboard/entities", params={"domains": "sensor,lock"}).json()
    assert [e["entity_id"] for e in body["entities"]] == ["sensor.hall_temp"]


@pytest.fixture
def admin_hub(monkeypatch):
    seen = []

    async def admin(request):
        auth = request.headers.get("Authorization")
        if auth == "Bearer admin-key":
            return "parent"
        raise HTTPException(status_code=403, detail="Admin only")

    class Resp:
        def __init__(self, status, body):
            self.status, self._body = status, body

        async def json(self, content_type=None):
            return self._body

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        def request(self, method, url, json=None, headers=None, timeout=None):
            seen.append((method, url, json, headers))
            if url.endswith("/hub/energy/prefs") and json and json.get("tariff") == "bad":
                return Resp(400, {"message": "tariff must be an object"})
            return Resp(200, {"ok": True})

    monkeypatch.setattr(gateway_main, "_require_admin", admin)
    monkeypatch.setattr(gateway_main, "shared_http_client", lambda: Client())
    return seen


def test_settings_are_admin_only_and_use_the_internal_secret(client, admin_hub):
    assert client.put("/api/dashboard/energy/prefs", json={}, headers={"Authorization": "Bearer kid"}).status_code == 403
    ok = client.put("/api/dashboard/energy/prefs", json={"tariff": {"currency": "USD"}},
                    headers={"Authorization": "Bearer admin-key"})
    assert ok.status_code == 200
    method, url, body, headers = admin_hub[-1]
    assert (method, url.endswith("/hub/energy/prefs"), body) == ("PUT", True, {"tariff": {"currency": "USD"}})
    assert headers["Authorization"].startswith("Bearer ") and "admin-key" not in headers["Authorization"]
    bad = client.put("/api/dashboard/energy/prefs", json={"tariff": "bad"}, headers={"Authorization": "Bearer admin-key"})
    assert bad.status_code == 400 and bad.json()["detail"] == "tariff must be an object"
    assert client.post("/api/dashboard/energy/tariff/suggest", json={"days": 90, "junk": 1},
                       headers={"Authorization": "Bearer admin-key"}).status_code == 200
    assert admin_hub[-1][2] == {"days": 90}
    assert client.put("/api/dashboard/prefs", json={"hidden": []},
                      headers={"Authorization": "Bearer admin-key"}).status_code == 200
