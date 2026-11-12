"""Automations, schedules and climate routes: who may read and write, and what a family member sees.

The hub is faked at ``automation_routes._hub`` (it records whether a call used
the caller's own HA credentials or the internal secret); identity and the
permission lookup are faked as in the camera tests. Entity ids are made up.
"""
import pytest
from conftest_media import DEFAULT_IDENTITY, TEST_HA_TOKEN, TEST_HA_URL
from fastapi import HTTPException
from fastapi.testclient import TestClient

from services.execution.entity_access import EntityPermissions
from services.gateway import automation_routes
from services.gateway import main as gateway_main

ITEMS = [
    {"kind": "automation", "id": "porch_at_dusk", "alias": "Porch at dusk", "enabled": True,
     "entities": ["light.porch"], "running": 0},
    {"kind": "automation", "id": "hall_heat", "alias": "Hall heat", "enabled": True,
     "entities": ["climate.hall", "sensor.hall_temp"], "running": 0},
    {"kind": "script", "id": "say_hello", "alias": "Say hello", "enabled": True, "entities": [], "running": 0},
]
SCHEDULES = [
    {"schedule_id": "abc123", "name": "Porch", "timeslots": [
        {"start": "sunset", "actions": [{"service": "light.turn_on", "entity_id": "light.porch"}]}]},
    {"schedule_id": "def456", "name": "Den", "timeslots": [
        {"start": "07:00:00", "actions": [{"service": "climate.set_temperature", "entity_id": "climate.den"}]}]},
]
CLIMATE = {
    "thermostats": [{"entity_id": "climate.hall", "name": "Hall"}, {"entity_id": "climate.den", "name": "Den"}],
    "programs": [{"id": "p1", "name": "Hall week", "entity_ids": ["climate.hall"]},
                 {"id": "p2", "name": "Den week", "entity_ids": ["climate.den"]}],
    "settings": {}, "energy": {"in_window": True, "shed": ["climate.hall", "climate.den"], "comforted": ["climate.den"]},
    "can_edit": True,
}
CATALOG = {"triggers": [], "conditions": [], "actions": [], "services": [],
           "entities": [{"entity_id": "light.porch"}, {"entity_id": "climate.den"}], "persons": [], "zones": []}


@pytest.fixture
def hub(monkeypatch):
    """The fake hub: ``seen`` records (method, path, via, params, json); ``answers`` overrides by path."""
    seen = []
    answers = {}
    defaults = {
        "/api/homehub/automations": {"items": ITEMS, "can_edit": True},
        "/api/homehub/automations/catalog": CATALOG,
        "/api/homehub/automations/automation/hall_heat": {"item": ITEMS[1], "config": {}, "yaml": ""},
        "/api/homehub/automations/automation/porch_at_dusk": {"item": ITEMS[0], "config": {}, "yaml": ""},
        "/api/homehub/automations/automation/porch_at_dusk/traces": {"traces": []},
        "/api/homehub/schedules": {"schedules": SCHEDULES, "can_edit": True},
        "/api/homehub/schedules/def456": SCHEDULES[1],
        "/api/homehub/climate": CLIMATE,
    }

    async def call(method, path, *, creds=None, params=None, json=None, timeout=None):
        via = "secret" if creds is None else f"{creds.get('ha_url')}|{creds.get('ha_token')}"
        seen.append((method, path, via, params, json))
        if path in answers:
            return answers[path]
        return 200, defaults.get(path, {"result": "ok"})

    monkeypatch.setattr(automation_routes, "_hub", call)
    return seen, answers


@pytest.fixture
def perms(monkeypatch):
    """Admins see everything; a member is assigned the porch light and the hall thermostat."""
    async def load(username, is_admin=False):
        if is_admin:
            return EntityPermissions.everything()
        return EntityPermissions(assigned=frozenset({"light.porch", "climate.hall"}))

    monkeypatch.setattr(automation_routes, "load_entity_permissions", load)


@pytest.fixture
def member(monkeypatch):
    """The caller is a family member: reads resolve to them, and the admin check refuses them."""
    async def resolve(body):
        return {**DEFAULT_IDENTITY, "is_admin": False}

    async def not_admin(request):
        raise HTTPException(status_code=403, detail="Admin only")

    monkeypatch.setattr(gateway_main, "resolve_identity", resolve)
    monkeypatch.setattr(gateway_main, "_require_admin", not_admin)


WRITES = [
    ("post", "/api/homehub/automations/automation"),
    ("put", "/api/homehub/automations/automation/porch_at_dusk"),
    ("delete", "/api/homehub/automations/script/say_hello"),
    ("post", "/api/homehub/automations/validate"),
    ("post", "/api/homehub/automations/automation/porch_at_dusk/enabled"),
    ("post", "/api/homehub/automations/script/say_hello/run"),
    ("get", "/api/homehub/automations/export"),
    ("post", "/api/homehub/automations/import"),
    ("get", "/api/homehub/automations/remap"),
    ("put", "/api/homehub/automations/remap"),
    ("post", "/api/homehub/automations/remap/preview"),
    ("post", "/api/homehub/automations/remap/suggest"),
    ("post", "/api/homehub/schedules"),
    ("put", "/api/homehub/schedules/abc123"),
    ("delete", "/api/homehub/schedules/abc123"),
    ("post", "/api/homehub/schedules/abc123/enabled"),
    ("post", "/api/homehub/climate/programs"),
    ("put", "/api/homehub/climate/programs/p1"),
    ("delete", "/api/homehub/climate/programs/p1"),
    ("put", "/api/homehub/climate/settings"),
]


def _send(client, verb, path, **kw):
    if verb in ("post", "put"):
        return getattr(client, verb)(path, json=kw.pop("json", {"kind": "automation"}), **kw)
    return getattr(client, verb)(path, **kw)


@pytest.mark.parametrize("verb,path", WRITES)
def test_members_cannot_write(client, hub, perms, member, verb, path):
    seen, _ = hub
    assert _send(client, verb, path).status_code == 403
    assert seen == []


@pytest.mark.parametrize("verb,path", WRITES)
def test_admin_writes_use_the_internal_secret(client, hub, perms, verb, path):
    seen, _ = hub
    assert _send(client, verb, path).status_code == 200
    assert len(seen) == 1 and seen[0][2] == "secret" and seen[0][1] == path


@pytest.mark.parametrize("verb,path", [("get", "/api/homehub/automations"), ("get", "/api/homehub/climate"),
                                       ("get", "/api/homehub/schedules"), ("put", "/api/homehub/climate/settings"),
                                       ("post", "/api/homehub/climate/hold")])
def test_anonymous_callers_get_nothing(hub, perms, verb, path):
    seen, _ = hub
    anon = TestClient(gateway_main.app)
    assert _send(anon, verb, path).status_code == 401
    assert seen == []


def test_reads_use_the_callers_own_token_and_can_edit_is_the_gateways_answer(client, hub, perms, member):
    seen, _ = hub
    resp = client.get("/api/homehub/automations")
    assert resp.status_code == 200
    assert seen[0][2] == f"{TEST_HA_URL}|{TEST_HA_TOKEN}"
    body = resp.json()
    assert body["can_edit"] is False  # the hub said True for the HA token; the gateway decides
    assert TEST_HA_TOKEN not in resp.text


def test_members_see_only_what_touches_their_entities(client, hub, perms, member):
    ids = [i["id"] for i in client.get("/api/homehub/automations").json()["items"]]
    # hall_heat names climate.hall (assigned); a script that names nothing is shown.
    assert ids == ["porch_at_dusk", "hall_heat", "say_hello"]
    schedules = client.get("/api/homehub/schedules").json()
    assert [s["schedule_id"] for s in schedules["schedules"]] == ["abc123"]
    assert client.get("/api/homehub/schedules/def456").status_code == 404
    entities = client.get("/api/homehub/automations/catalog").json()["entities"]
    assert [e["entity_id"] for e in entities] == ["light.porch"]


def test_climate_overview_is_filtered_for_members(client, hub, perms, member):
    body = client.get("/api/homehub/climate").json()
    assert [t["entity_id"] for t in body["thermostats"]] == ["climate.hall"]
    assert [p["id"] for p in body["programs"]] == ["p1"]
    assert body["energy"]["shed"] == ["climate.hall"] and body["energy"]["comforted"] == []
    assert body["can_edit"] is False


def test_admins_see_everything(client, hub, perms):
    body = client.get("/api/homehub/climate").json()
    assert len(body["thermostats"]) == 2 and body["can_edit"] is True
    assert len(client.get("/api/homehub/schedules").json()["schedules"]) == 2


def test_hidden_item_and_its_traces_are_404(client, hub, perms, member, monkeypatch):
    seen, answers = hub
    answers["/api/homehub/automations/automation/den_only"] = (
        200, {"item": {"id": "den_only", "entities": ["climate.den"]}, "config": {}, "yaml": ""})
    assert client.get("/api/homehub/automations/automation/den_only").status_code == 404
    assert client.get("/api/homehub/automations/automation/den_only/traces").status_code == 404
    assert not any(p.endswith("/traces") for _, p, *_ in seen)
    resp = client.get("/api/homehub/automations/automation/porch_at_dusk/traces")
    assert resp.status_code == 200 and resp.json() == {"traces": []}


def test_hold_needs_control_of_that_thermostat_and_uses_the_callers_token(client, hub, perms, member):
    seen, _ = hub
    body = {"entity_id": "climate.hall", "setpoint": {"hvac_mode": "heat", "temperature": 20}, "until": "next_slot"}
    assert client.post("/api/homehub/climate/hold", json=body).status_code == 200
    assert seen[-1][:3] == ("POST", "/api/homehub/climate/hold", f"{TEST_HA_URL}|{TEST_HA_TOKEN}")
    assert seen[-1][4] == body
    assert client.post("/api/homehub/climate/hold", json={**body, "entity_id": "climate.den"}).status_code == 403
    assert client.post("/api/homehub/climate/resume", json={"entity_id": "climate.den"}).status_code == 403
    assert client.post("/api/homehub/climate/resume", json={"entity_id": "climate.hall"}).status_code == 200
    assert seen[-1][4] == {"entity_id": "climate.hall"}


def test_hold_refuses_an_entity_that_is_not_a_thermostat(client, hub, perms):
    seen, _ = hub
    for bad in ("light.porch", "climate.../x", None, "CLIMATE.hall"):
        assert client.post("/api/homehub/climate/hold", json={"entity_id": bad}).status_code == 400
    assert seen == []


@pytest.mark.parametrize("path", [
    "/api/homehub/automations/widget/porch_at_dusk",
    "/api/homehub/automations/automation/bad%20id",
    "/api/homehub/automations/automation/" + "x" * 201,
    "/api/homehub/schedules/a%2Fb",
])
def test_odd_ids_never_reach_the_hub(client, hub, perms, path):
    seen, _ = hub
    assert client.get(path).status_code == 404
    assert client.delete(path).status_code in (404, 405)
    assert seen == []


def test_export_and_import_check_the_kind(client, hub, perms):
    seen, _ = hub
    assert client.get("/api/homehub/automations/export", params={"kind": "widget"}).status_code == 404
    assert client.post("/api/homehub/automations/import", json={"kind": "nope", "yaml": ""}).status_code == 404
    assert seen == []
    client.get("/api/homehub/automations/export", params={"kind": "script", "id": "say_hello"})
    assert seen[-1][3] == {"kind": "script", "id": "say_hello"}


def test_hub_refusals_keep_their_status_and_words(client, hub, perms):
    _, answers = hub
    answers["/api/homehub/automations/validate"] = (400, {"message": "Bad trigger", "errors": ["triggers[0]"]})
    resp = client.post("/api/homehub/automations/validate", json={"kind": "automation", "config": {}})
    assert resp.status_code == 400
    assert resp.json()["detail"] == "Bad trigger" and resp.json()["errors"] == ["triggers[0]"]
    answers["/api/homehub/climate"] = (503, {"message": "The scheduler is starting"})
    assert client.get("/api/homehub/climate").status_code == 503
    answers["/api/homehub/automations"] = (500, None)
    assert client.get("/api/homehub/automations").status_code == 502


def test_enabled_sends_only_a_boolean(client, hub, perms):
    seen, _ = hub
    client.post("/api/homehub/automations/automation/porch_at_dusk/enabled", json={"enabled": 0, "x": 1})
    assert seen[-1][4] == {"enabled": False}


async def test_the_hub_is_reached_with_the_right_credentials(monkeypatch):
    sent = []

    class Resp:
        status = 200

        async def json(self, content_type=None):
            return {"items": []}

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    class Client:
        def request(self, method, url, **kw):
            sent.append({"method": method, "url": url, **kw})
            return Resp()

    class Shared:
        async def __aenter__(self):
            return Client()

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(gateway_main, "shared_http_client", lambda: Shared())
    monkeypatch.setattr(automation_routes, "INTERNAL_SECRET", "made-up-secret")
    await automation_routes._hub("PUT", "/api/homehub/climate/settings", json={})
    assert sent[-1]["url"] == f"{automation_routes.HOMEHUB_URL}/api/homehub/climate/settings"
    assert sent[-1]["headers"] == {"Authorization": "Bearer made-up-secret"}
    creds = {"ha_url": "http://hub.example:8123/", "ha_token": "made-up-token"}
    await automation_routes._hub("GET", "/api/homehub/automations", creds=creds)
    assert sent[-1]["url"] == "http://hub.example:8123/api/homehub/automations"
    assert sent[-1]["headers"] == {"Authorization": "Bearer made-up-token"}
    with pytest.raises(HTTPException) as err:
        await automation_routes._hub("GET", "/x", creds={"ha_url": "", "ha_token": ""})
    assert err.value.status_code == 503
