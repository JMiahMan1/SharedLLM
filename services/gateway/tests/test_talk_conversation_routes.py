"""The chat page's conversation management reaches execution's Talk actions."""
from fastapi.testclient import TestClient

from services.gateway import main as gateway_main
from services.gateway.main import app


def _capture(monkeypatch):
    sent = []

    async def fake(request, path, payload, as_user=None):
        sent.append((path, payload))
        return {"status": "SUCCESS"}

    monkeypatch.setattr(gateway_main, "_proxy_execution_with_identity", fake)
    return sent


def test_conversation_management_routes(monkeypatch):
    sent = _capture(monkeypatch)
    c = TestClient(app)
    c.post("/api/communication/talk/conversations/group", json={"name": "Family", "participants": ["michele"]})
    c.delete("/api/communication/talk/conversations", params={"token": "abc"})
    c.get("/api/communication/talk/participants", params={"token": "abc"})
    c.post("/api/communication/talk/participants", json={"token": "abc", "user": "kids"})
    c.delete("/api/communication/talk/participants", params={"token": "abc", "attendee_id": 5})
    c.get("/api/communication/talk/people", params={"search": "mi"})
    assert [p for _, p in sent] == [
        {"action": "create_group", "message": "Family", "participants": ["michele"]},
        {"action": "delete_conversation", "token": "abc"},
        {"action": "participants", "token": "abc"},
        {"action": "add_participant", "token": "abc", "target_user": "kids"},
        {"action": "remove_participant", "token": "abc", "attendee_id": 5},
        {"action": "people", "search": "mi", "token": None},
    ]
    assert {path for path, _ in sent} == {"/execute/talk"}


def test_a_call_link_is_requested_from_execution(monkeypatch):
    sent = _capture(monkeypatch)
    c = TestClient(app)
    c.post("/api/communication/talk/call/link", json={"token": "abc", "as_user": "admin"})
    assert sent == [("/execute/talk", {"action": "call_link", "token": "abc"})]
