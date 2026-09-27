"""BUG-03 / P1-T3: /execute/media/transport must enforce verify_entity_access.

A user without an assignment for entity X must be denied (403) when sending
transport commands to X — the same access check play uses.
"""
import os

from fastapi.testclient import TestClient

from services.execution.main import app

client = TestClient(app)


def _secret() -> str:
    return os.environ["INTERNAL_SECRET"]


def _post_transport(monkeypatch, allowed_entities, entity_id="media_player.office"):
    import services.execution.main as exec_main
    from services.execution.schemas import ExecutionResult

    reached = []

    async def fake_allowed(username, is_admin):
        return set(allowed_entities)

    async def fake_transport(req):
        reached.append(req.entity_id)
        return ExecutionResult(status="SUCCESS", message="reached", service="media_transport")

    monkeypatch.setattr(exec_main, "_allowed_entity_ids", fake_allowed)
    monkeypatch.setattr(exec_main.MediaPlaybackService, "transport", fake_transport)

    resp = client.post(
        "/execute/media/transport",
        headers={"X-Internal-Secret": _secret()},
        json={
            "user_context": {"user": "testuser", "ha_url": "http://ha.test", "ha_token": "tok"},
            "entity_id": entity_id,
            "command": "pause",
        },
    )
    return resp, reached


def test_transport_to_unassigned_entity_denied(monkeypatch):
    resp, reached = _post_transport(monkeypatch, allowed_entities={"media_player.other"})
    assert resp.status_code == 403
    assert reached == []


def test_transport_to_assigned_entity_allowed(monkeypatch):
    resp, reached = _post_transport(monkeypatch, allowed_entities={"media_player.office"})
    assert resp.status_code == 200
    assert reached == ["media_player.office"]
