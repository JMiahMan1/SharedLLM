"""Settings > Nearby & offline through the gateway: identity decides who may
read or change, and a parent's change to a child's parts reaches the child's
phone at once (the same refresh signal as a screen-time change)."""
import pytest
from fastapi.testclient import TestClient

from services.gateway import main as gateway_main
from services.gateway.main import app


@pytest.fixture
def wired(monkeypatch):
    calls: list[tuple] = []
    refreshed: list[str] = []
    replies: dict = {}

    async def fake_identity(request, method, path, **kwargs):
        calls.append((method, path, kwargs.get("json")))
        return replies.get((method, path), (200, {}))

    async def fake_refresh(user):
        refreshed.append(user)

    monkeypatch.setattr(gateway_main, "_identity_as_caller", fake_identity)
    monkeypatch.setattr(gateway_main, "_screen_time_refresh", fake_refresh)
    return calls, refreshed, replies


def test_own_settings_are_read_and_changed_through_identity(wired):
    calls, refreshed, replies = wired
    mine = {"username": "casey", "settings": {"nearby": True, "chat": False, "offline": False, "car": True},
            "managed_by_parent": False, "can_change": True, "always_on": []}
    replies[("GET", "/api/users/me/nearby-settings")] = (200, mine)
    replies[("PUT", "/api/users/me/nearby-settings")] = (200, mine)
    client = TestClient(app)
    assert client.get("/api/users/me/nearby-settings").json() == mine
    assert client.put("/api/users/me/nearby-settings", json={"nearby": True}).status_code == 200
    assert calls[-1] == ("PUT", "/api/users/me/nearby-settings", {"nearby": True})
    # A grown member's own change signals no child's phone.
    assert refreshed == []


def test_a_parents_change_signals_the_childs_phone(wired):
    _calls, refreshed, replies = wired
    replies[("PUT", "/api/users/robin/nearby-settings")] = (200, {
        "username": "robin", "settings": {"nearby": False, "chat": False, "offline": True, "car": True},
        "managed_by_parent": True, "can_change": True, "always_on": ["parent_link"],
    })
    resp = TestClient(app).put("/api/users/robin/nearby-settings", json={"offline": True})
    assert resp.status_code == 200
    assert refreshed == ["robin"]


def test_a_refusal_is_passed_on_and_signals_nothing(wired):
    _calls, refreshed, replies = wired
    replies[("PUT", "/api/users/robin/nearby-settings")] = (403, {"detail": "Only this child's parents can change these"})
    resp = TestClient(app).put("/api/users/robin/nearby-settings", json={"car": False})
    assert resp.status_code == 403
    assert refreshed == []
