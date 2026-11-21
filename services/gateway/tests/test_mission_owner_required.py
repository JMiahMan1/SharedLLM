"""POST /api/raven/missions needs a real person as the mission's owner.

An internal caller with only X-Internal-Secret used to resolve as the shared
household identity, whose name went to Identity as ``user_id="household"`` and
was answered 422 after the request had been accepted. It is now a 400 at the
gateway, before anything is dispatched.
"""

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from services.gateway import main as gateway_main
from services.gateway.main import app

SECRET = "test-internal-secret"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(gateway_main, "INTERNAL_SECRET", SECRET)
    resolved: list[dict] = []
    enqueued: list[dict] = []

    async def _resolve(payload: dict):
        resolved.append(dict(payload))
        if payload.get("rag_user") == "alex" or payload.get("user_id") == 7:
            return {"user": "alex", "id": 7, "is_admin": False}
        raise HTTPException(status_code=401, detail="Invalid credentials")

    async def _settings():
        return {"coding_model": "coder"}

    async def _prompt(query: str) -> str:
        return "system"

    async def _enqueue(**kwargs):
        enqueued.append(kwargs)
        owner = gateway_main._mission_owner_id(kwargs["creds"])
        return {"id": 1, "user_id": owner}

    monkeypatch.setattr(gateway_main, "resolve_identity", _resolve)
    monkeypatch.setattr(gateway_main, "get_llm_settings", _settings)
    monkeypatch.setattr(gateway_main, "_build_raven_system_prompt", _prompt)
    monkeypatch.setattr(gateway_main, "_enqueue_user_mission", _enqueue)
    c = TestClient(app)
    c.resolved = resolved
    c.enqueued = enqueued
    return c


def test_internal_secret_without_user_is_a_clear_400(client):
    resp = client.post("/api/raven/missions", json={"query": "build a thing"}, headers={"X-Internal-Secret": SECRET})
    assert resp.status_code == 400
    assert resp.json()["detail"] == "internal callers must send X-User-Id"
    assert client.resolved == [] and client.enqueued == []


@pytest.mark.parametrize("named", ["alex", "7"])
def test_internal_secret_with_x_user_id_acts_as_that_user(client, named):
    resp = client.post(
        "/api/raven/missions",
        json={"query": "build a thing"},
        headers={"X-Internal-Secret": SECRET, "X-User-Id": named},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["mission"]["user_id"] == 7
    assert client.resolved[-1] == ({"rag_user": "alex"} if named == "alex" else {"user_id": 7})


def test_an_unknown_x_user_id_is_refused(client):
    resp = client.post(
        "/api/raven/missions",
        json={"query": "build a thing"},
        headers={"X-Internal-Secret": SECRET, "X-User-Id": "nobody"},
    )
    assert resp.status_code == 401
    assert client.enqueued == []


def test_a_wrong_secret_is_not_an_internal_caller(client):
    resp = client.post(
        "/api/raven/missions",
        json={"query": "build a thing"},
        headers={"X-Internal-Secret": "wrong", "X-User-Id": "alex"},
    )
    assert resp.status_code == 401
    assert client.enqueued == []


@pytest.mark.parametrize(
    "creds",
    [
        {"user": "household", "id": None},
        {"user": "anonymous"},
        {"user": "alex", "id": "not-a-number"},
        {"user": "alex", "id": True},
    ],
)
def test_a_mission_owner_must_be_a_real_user_id(creds):
    with pytest.raises(HTTPException) as exc:
        gateway_main._mission_owner_id(creds)
    assert exc.value.status_code == 400
    assert "X-User-Id" in exc.value.detail


def test_a_mission_owner_id_is_accepted_as_int_or_digits():
    assert gateway_main._mission_owner_id({"id": 3}) == 3
    assert gateway_main._mission_owner_id({"user_id": "12"}) == 12


def test_reading_a_mission_as_an_internal_caller_also_needs_x_user_id(client):
    resp = client.get("/api/raven/missions/5", headers={"X-Internal-Secret": SECRET})
    assert resp.status_code == 400
    assert resp.json()["detail"] == "internal callers must send X-User-Id"


def test_dispatch_mission_script_names_the_owner_the_route_needs(client, monkeypatch):
    """scripts/dispatch_mission.py sends X-User-Id, so its request passes the real route."""
    import importlib.util
    from pathlib import Path

    import httpx

    script = Path(__file__).resolve().parents[3] / "scripts" / "dispatch_mission.py"
    spec = importlib.util.spec_from_file_location("dispatch_mission_for_route_test", script)
    dm = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(dm)

    sent: dict = {}

    class _Client:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def post(self, url, json=None, headers=None):
            sent["headers"] = headers
            resp = client.post("/api/raven/missions", json=json, headers=headers)
            return httpx.Response(resp.status_code, json=resp.json(), request=httpx.Request("POST", url))

    monkeypatch.setattr(dm.httpx, "Client", _Client)
    result = dm.dispatch_mission("http://gateway.invalid", SECRET, {"query": "build a thing"}, "alex")
    assert sent["headers"]["X-User-Id"] == "alex"
    assert result["mission"]["user_id"] == 7
