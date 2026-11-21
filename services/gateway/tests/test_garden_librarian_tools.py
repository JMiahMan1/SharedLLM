"""The six garden Librarian tools: offered, resolvable, and executable.

A tool is only useful if three things hold: the model is told about it, its call
resolves to a route this gateway actually serves, and that route really does the
thing. Each of those is checked here, so a rename can never quietly turn a tool
into a 404 the model only discovers at run time.
"""
from __future__ import annotations

import pytest
from fastapi.responses import Response
from fastapi.testclient import TestClient

from services.gateway import garden_assist, garden_routes, tool_registry
from services.gateway import main as gateway_main
from services.gateway.main import app

GARDEN_TOOLS = (
    "garden_summary",
    "garden_log_care",
    "garden_log_harvest",
    "garden_recipe_matches",
    "garden_preservation_guide",
    "garden_plan_suggest",
)


@pytest.fixture()
def client():
    return TestClient(app, headers={"Authorization": "Bearer parent-key"})


@pytest.fixture()
def parent(monkeypatch):
    async def acting(_request):
        return {"user": "mother", "is_admin": True, "family_role": "mother"}

    monkeypatch.setattr(gateway_main, "_acting_identity", acting)


def test_all_six_tools_are_offered_to_the_model():
    schemas = {schema["function"]["name"]: schema["function"] for schema in tool_registry.get_tool_schemas()}

    for name in GARDEN_TOOLS:
        assert name in schemas, f"{name} is not offered"
        assert schemas[name]["description"].strip(), f"{name} has no description"


def test_the_write_tools_declare_what_they_need():
    schemas = {schema["function"]["name"]: schema["function"] for schema in tool_registry.get_tool_schemas()}

    assert schemas["garden_log_care"]["parameters"]["required"] == ["target"]
    assert set(schemas["garden_log_harvest"]["parameters"]["required"]) == {"target", "quantity"}
    assert "target" in schemas["garden_log_care"]["parameters"]["properties"]


@pytest.mark.parametrize("name,method,path", [
    ("garden_summary", "GET", "/api/garden/assistant/summary"),
    ("garden_log_care", "POST", "/api/garden/assistant/log-care"),
    ("garden_log_harvest", "POST", "/api/garden/assistant/log-harvest"),
    ("garden_recipe_matches", "GET", "/api/garden/recipes/matches"),
    ("garden_preservation_guide", "GET", "/api/garden/preservation/advice"),
    ("garden_plan_suggest", "GET", "/api/garden/assistant/plan-context"),
])
def test_each_tool_resolves_to_its_gateway_route(name, method, path):
    call = tool_registry.resolve_tool_call(name, {"target": "Monty", "quantity": 2})

    assert call.service == "gateway"
    assert (call.method, call.path) == (method, path)


def test_every_resolved_path_is_a_route_this_gateway_serves():
    # The app's own OpenAPI document is the definitive list of served paths
    # (``app.routes`` keeps included routers nested in this FastAPI version).
    served = set(app.openapi().get("paths", {}))

    for name in GARDEN_TOOLS:
        call = tool_registry.resolve_tool_call(name, {"target": "Monty", "quantity": 2})
        assert call.path in served, f"{name} points at {call.path}, which does not exist"


def test_the_care_tool_defaults_to_watering_from_voice():
    call = tool_registry.resolve_tool_call("garden_log_care", {"target": "Sir Basil"})

    assert call.json == {"target": "Sir Basil", "action": "water", "still_damp": False, "source": "voice"}


def test_the_harvest_tool_keeps_what_it_was_given():
    call = tool_registry.resolve_tool_call(
        "garden_log_harvest",
        {"target": "Roma Row", "quantity": 3, "unit": "lb", "disposition": "frozen", "notes": "BLT night"},
    )

    assert call.json["quantity"] == 3
    assert call.json["disposition"] == "frozen"
    assert call.json["notes"] == "BLT night"


def test_an_unknown_tool_is_still_not_resolved():
    with pytest.raises(Exception):
        tool_registry.resolve_tool_call("garden_water_everything", {})


# --------------------------------------------------------------------------- executing them

def test_running_the_summary_tool_answers_from_the_garden(client, parent, monkeypatch):
    async def summary(_creds, **_kw):
        return {"due_count": 1, "spoken": "Monty needs water today."}

    monkeypatch.setattr(garden_assist, "summarize", summary)

    resp = client.get("/api/garden/assistant/summary")

    assert resp.status_code == 200, resp.text
    assert resp.json()["spoken"] == "Monty needs water today."


def test_running_the_care_tool_reaches_the_plant_the_person_named(client, parent, monkeypatch):
    seen: dict = {}

    async def care(_creds, target, *, action="water", still_damp=False, source="voice", **kw):
        seen.update({"target": target, "action": action, "source": source})
        return {"plant": {"plant_id": "p-monty", "nickname": "Monty"}, "message": "Logged watering for Monty."}

    monkeypatch.setattr(garden_assist, "log_care", care)

    resp = client.post("/api/garden/assistant/log-care", json={"target": "Monty", "action": "water"})

    assert resp.status_code == 200, resp.text
    assert resp.json()["message"] == "Logged watering for Monty."
    assert seen == {"target": "Monty", "action": "water", "source": "voice"}


def test_a_nameless_care_request_is_refused_before_anything_is_written(client, parent):
    resp = client.post("/api/garden/assistant/log-care", json={"action": "water"})

    assert resp.status_code == 422
    assert "Name a plant" in resp.json()["detail"]


def test_a_harvest_without_a_quantity_is_refused(client, parent):
    resp = client.post("/api/garden/assistant/log-harvest", json={"target": "Roma Row"})

    assert resp.status_code == 422
    assert "quantity" in resp.json()["detail"]


def test_the_plan_tool_answers_with_the_real_yard(client, parent, monkeypatch):
    async def call(method, path, *, params=None, json=None, headers=None):
        body = {
            "/profile": {"family_size": 7, "hardiness_zone": "5b"},
            "/locations": {"locations": [
                {"id": "bed-1", "name": "Bed 1", "kind": "raised_bed"},
                {"id": "room-1", "name": "Living Room", "kind": "indoor_room"},
            ]},
            "/pantry-goals": {"goals": [{"item": "Marinara sauce", "target_qty": 40}]},
            "/plans": {"plans": [{"id": "plan-1", "year": 2027}]},
        }[path]
        import json as _json

        return Response(content=_json.dumps(body), media_type="application/json")

    monkeypatch.setattr(garden_routes, "_call", call)

    resp = client.get("/api/garden/assistant/plan-context")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["profile"]["hardiness_zone"] == "5b"
    # Indoor rooms are Home Hub areas, not planting beds.
    assert [bed["id"] for bed in body["beds"]] == ["bed-1"]
    assert body["pantry_goals"][0]["item"] == "Marinara sauce"
