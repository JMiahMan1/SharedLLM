"""Registering, reconfiguring and deleting a workspace is for administrators.

The workspace runtime does not look at the caller on these routes, so the
gateway is the only gate. Before it existed, DELETE /api/workspaces/{id} did
not even read the request: anyone who could reach the gateway could delete a
workspace, or PATCH one onto another owner or path.

A workspace's Raven missions are read from Identity with the internal secret,
which does not filter by user, so that route checks the caller can see the
workspace.
"""
import pytest
from fastapi.testclient import TestClient

from services.gateway import main as gateway_main
from services.gateway.main import app

WRITES = [
    ("POST", "/api/workspaces", {"id": "w1", "display_name": "W1", "local_path": "w1"}),
    ("PATCH", "/api/workspaces/w1", {"owner_user": "mallory"}),
    ("DELETE", "/api/workspaces/w1", None),
]


def _call(client, verb, path, body):
    kwargs = {"json": body} if body is not None else {}
    return client.request(verb, path, **kwargs)


def _client_as(monkeypatch, ident):
    async def _strict(api_key: str):
        return dict(ident) if ident and api_key else None

    monkeypatch.setattr(gateway_main, "_resolve_strict_identity", _strict)
    headers = {"Authorization": "Bearer k"} if ident else {}
    return TestClient(app, headers=headers, raise_server_exceptions=False)


@pytest.fixture(autouse=True)
def workspace_artifact_actions(monkeypatch):
    calls: list[tuple[str, str]] = []

    async def _action(workspace_id: str, action: str) -> dict:
        calls.append((workspace_id, action))
        return {"action": action, "artifact_count": 0, "deleted": 0, "orphaned": 0}

    monkeypatch.setattr(gateway_main, "_artifact_workspace_action", _action)
    return calls


@pytest.mark.parametrize("verb,path,body", WRITES, ids=[f"{v} {p}" for v, p, _ in WRITES])
def test_anonymous_callers_cannot_change_workspaces(monkeypatch, runtime_upstream, verb, path, body):
    resp = _call(_client_as(monkeypatch, None), verb, path, body)
    assert resp.status_code == 401
    assert "url" not in runtime_upstream, "the runtime was called for an anonymous caller"


@pytest.mark.parametrize("verb,path,body", WRITES, ids=[f"{v} {p}" for v, p, _ in WRITES])
def test_members_cannot_change_workspaces(monkeypatch, runtime_upstream, verb, path, body):
    resp = _call(_client_as(monkeypatch, {"user": "alice", "is_admin": False}), verb, path, body)
    assert resp.status_code == 403
    assert "url" not in runtime_upstream


@pytest.mark.parametrize("verb,path,body", WRITES, ids=[f"{v} {p}" for v, p, _ in WRITES])
def test_admins_reach_the_runtime(monkeypatch, runtime_upstream, verb, path, body):
    resp = _call(_client_as(monkeypatch, {"user": "root", "is_admin": True}), verb, path, body)
    assert resp.status_code == 200, resp.text
    assert runtime_upstream["method"] == verb


@pytest.fixture
def missions_upstream(monkeypatch):
    """Runtime lists only 'mine' to alice; Identity returns one mission."""
    calls: list[str] = []

    async def _resolve(request, body=None):
        ident = await gateway_main._resolve_strict_identity("k")
        if not ident:
            from fastapi import HTTPException

            raise HTTPException(status_code=401, detail="Authentication required")
        return ident

    async def _runtime(method, path, *, json_payload=None, params=None):
        calls.append(f"runtime {path}")
        return {"workspaces": [{"id": "mine"}]}

    class _Resp:
        status = 200

    class _Client:
        async def get(self, url, **kwargs):
            calls.append(f"identity {kwargs.get('params')}")
            return _Resp()

    async def _proxy(resp):
        return [{"id": 1}]

    monkeypatch.setattr(gateway_main, "_resolve_identity_from_request", _resolve)
    monkeypatch.setattr(gateway_main, "workspace_runtime_request", _runtime)
    monkeypatch.setattr(gateway_main, "get_http_client", lambda: _Client())
    monkeypatch.setattr(gateway_main, "_proxy_json_response", _proxy)
    return calls


def test_workspace_missions_need_a_caller(monkeypatch, missions_upstream):
    resp = _client_as(monkeypatch, None).get("/api/workspaces/mine/raven/missions")
    assert resp.status_code == 401
    assert missions_upstream == []


def test_members_see_missions_only_for_their_workspaces(monkeypatch, missions_upstream):
    client = _client_as(monkeypatch, {"user": "alice", "is_admin": False})
    assert client.get("/api/workspaces/mine/raven/missions").status_code == 200
    assert client.get("/api/workspaces/theirs/raven/missions").status_code == 404
    identity_calls = [c for c in missions_upstream if c.startswith("identity")]
    assert identity_calls == ["identity {'workspace_id': 'mine', 'limit': 50}"]


def test_admins_see_any_workspaces_missions(monkeypatch, missions_upstream):
    client = _client_as(monkeypatch, {"user": "root", "is_admin": True})
    assert client.get("/api/workspaces/theirs/raven/missions").status_code == 200
    assert not any(c.startswith("runtime") for c in missions_upstream)


def test_services_with_the_internal_secret_may_clean_up(monkeypatch, runtime_upstream):
    """The live E2E suites delete their workspaces with the internal secret."""
    client = _client_as(monkeypatch, None)
    resp = client.delete("/api/workspaces/w1", headers={"X-Internal-Secret": gateway_main.INTERNAL_SECRET})
    assert resp.status_code == 200, resp.text


def test_deleting_a_workspace_with_artifacts_asks_what_to_do(
    monkeypatch, runtime_upstream, workspace_artifact_actions
):
    async def _counted(workspace_id: str, action: str) -> dict:
        workspace_artifact_actions.append((workspace_id, action))
        if action == "count":
            return {"action": "count", "artifact_count": 2}
        return {"action": action, "deleted": 2, "orphaned": 2}

    monkeypatch.setattr(gateway_main, "_artifact_workspace_action", _counted)
    client = _client_as(monkeypatch, {"user": "root", "is_admin": True})

    needs_choice = client.delete("/api/workspaces/w1")
    assert needs_choice.status_code == 409
    assert needs_choice.json()["needs_confirmation"] is True
    assert needs_choice.json()["artifact_count"] == 2
    assert "url" not in runtime_upstream, "the workspace was deleted before the user chose"
    assert workspace_artifact_actions == [("w1", "count")]

    keep = client.request("DELETE", "/api/workspaces/w1", json={"delete_artifacts": False})
    assert keep.status_code == 200, keep.text
    assert workspace_artifact_actions[-1] == ("w1", "orphan")

    remove = client.request("DELETE", "/api/workspaces/w1", json={"delete_artifacts": True})
    assert remove.status_code == 200, remove.text
    assert workspace_artifact_actions[-1] == ("w1", "delete")
