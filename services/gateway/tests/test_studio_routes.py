"""The studio's routes and tools must be wired, authenticated, and the same.

Each route and each model tool runs one shared action (``studio.actions``); what
the actions do is proven end to end in ``test_studio_e2e.py`` against the real
Workspace Runtime and execution handler. These tests pin the surface: the paths
exist on the app, an anonymous caller is refused, every action is reachable as a
tool the registry offers and the dispatchers know, and a failure maps to the same
status code whichever door it came through.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from services.gateway import tool_registry
from services.gateway import workspace_api as ws
from services.gateway.main import app
from services.gateway.studio import actions, media, routes, runtime, stl, tools, works


def _paths() -> set[str]:
    from services.gateway.trainer.catalog import _iter_routes

    return {getattr(route, "path", "") for route in _iter_routes(app.routes)}


def test_every_studio_route_is_registered():
    declared = {route.path for route in routes.router.routes}
    missing = sorted(declared - _paths())

    assert "/api/studio/profile" in declared
    assert not missing, f"declared but not included on the app: {missing}"


def test_the_studio_needs_a_signed_in_user():
    anonymous = TestClient(app)

    assert anonymous.get("/api/studio/profile", params={"workspace_id": "w"}).status_code in (401, 403)
    assert anonymous.post("/api/studio/run", json={"workspace_id": "w"}).status_code in (401, 403)
    assert anonymous.post("/api/studio/deck", json={"workspace_id": "w", "markdown": "# x"}).status_code in (401, 403)
    # the newer surfaces are behind the same door
    assert anonymous.post("/api/studio/diagnose", json={"workspace_id": "w"}).status_code in (401, 403)
    assert anonymous.post("/api/studio/verify", json={"workspace_id": "w"}).status_code in (401, 403)
    assert anonymous.post("/api/studio/stl/inspect", json={"workspace_id": "w", "path": "a.stl"}).status_code in (401, 403)


def test_every_action_is_a_route_and_a_tool():
    route_paths = {route.path for route in routes.router.routes}

    assert set(tools.TOOL_ACTIONS.values()) == set(actions.ACTIONS) - {"stl_preview"}
    for tool, path in tools.TOOL_PATHS.items():
        assert path in route_paths, f"{tool} names {path}, which is not a studio route"


def test_the_registry_offers_the_studio_tools_to_raven_and_single_turn_chat():
    rows = {row[0]: row for row in tool_registry._RAVEN_TOOL_TABLE}
    single_turn = tool_registry.single_turn_tools()
    schemas = {schema["function"]["name"] for schema in tool_registry.get_raven_tool_schemas()}

    for tool, path in tools.TOOL_PATHS.items():
        assert rows[tool][1] == tool_registry.SVC_GATEWAY and rows[tool][3] == path
        assert rows[tool][4] is True, "every studio tool acts on a workspace"
        assert tool.lower() in single_turn
        assert tool in schemas


def test_the_dispatchers_run_the_studio_tools_in_process():
    from services.gateway import agent_loop, orchestrator

    for tool in tools.TOOL_ACTIONS:
        assert tool.lower() in agent_loop.ALLOWED_TOOLS
        assert tool.lower() in agent_loop.WORKSPACE_TOOL_ACTIONS
        assert tool.lower() not in orchestrator._TOOL_SERVICE_MAP, "never posted to a service URL"


def test_the_external_agent_reaches_a_studio_route_with_the_callers_key():
    resolved = tool_registry.resolve_tool_call(
        "StudioRunRequest", {"payload": {"action": "test"}}, workspace_id="w1", user_context={"user": "u1"}
    )

    assert resolved.service == tool_registry.SVC_GATEWAY
    assert resolved.path == "/api/studio/run"
    assert resolved.json["workspace_id"] == "w1" and resolved.json["action"] == "test"


@pytest.mark.parametrize(
    "exc, status",
    [
        (stl.StlError("bad mesh"), 400),
        (works.WorkError("empty deck"), 400),
        (works.WorkExists("decks/a exists"), 409),
        (runtime.StudioError("no test step"), 400),
        (runtime.ShellUnavailable("HTTP 500"), 502),
        (media.MediaError("CUDA out of memory"), 502),
        (ws.WorkspaceConflict("changed", 409), 409),
        (ws.WorkspaceFileNotFound("Path not found: a", 404), 404),
        (ws.WorkspaceError("Workspace not found", 404), 404),
        (ws.WorkspaceError("forbidden", 403), 403),
        (ws.WorkspaceError("connection refused"), 502),
    ],
)
def test_one_failure_maps_to_one_status_for_both_doors(exc, status):
    mapped = actions.error_for(exc)

    assert mapped is not None and mapped.status == status
    assert mapped.detail == str(exc)


def test_an_unexpected_error_is_not_dressed_up_as_a_studio_answer():
    assert actions.error_for(KeyError("bug")) is None


async def test_a_tool_payload_that_does_not_fit_is_a_400_naming_the_field():
    with pytest.raises(actions.StudioActionError) as excinfo:
        await tools.call("StudioRunRequest", {"timeout": 5}, user="u1", workspace_id="w1")

    assert excinfo.value.status == 400
    assert "timeout" in excinfo.value.detail


async def test_a_tool_with_no_user_is_refused():
    with pytest.raises(actions.StudioActionError) as excinfo:
        await tools.call("StudioRunRequest", {}, user="", workspace_id="w1")

    assert excinfo.value.status == 401


def test_a_tool_result_shows_the_model_a_failure():
    text = tools.summarize("StudioRunRequest", {"action": "test", "ok": False, "returncode": 1})

    assert text.startswith('StudioRunRequest result: {"ok": false')
