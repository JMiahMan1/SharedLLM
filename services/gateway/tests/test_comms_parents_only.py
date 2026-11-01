"""Calls, texts and message notifications reach the parents only: an admin
who is not a parent sees the rest of a child's activity, not those."""
from fastapi.testclient import TestClient

from services.gateway import main as gateway_main
from services.gateway.main import app, without_comms

FEED = {"status": "SUCCESS", "events": [
    {"feature": "sms", "item": "Leo", "detail": {"body": "secret"}},
    {"feature": "calls", "item": "Mom"},
    {"feature": "social", "item": "Snapchat"},
    {"feature": "bible", "item": "John 3"},
]}


class _Resp:
    status = 200

    def __init__(self, data):
        self.data = data

    async def json(self, content_type=None):
        return self.data

    async def read(self):
        import json
        return json.dumps(self.data).encode()

    @property
    def headers(self):
        return {"Content-Type": "application/json"}


def _client(monkeypatch, rule):
    async def creds(request, body=None):
        return {"user": "viewer"}

    class _C:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, **kw):
            if "may-view-raw" in url:
                return _Resp(rule)
            return _Resp(FEED)

    monkeypatch.setattr(gateway_main, "_resolve_identity_from_request", creds)
    monkeypatch.setattr(gateway_main, "borrow_http_client", lambda: _C())
    return TestClient(app)


def test_a_parent_sees_calls_and_messages(monkeypatch):
    c = _client(monkeypatch, {"allowed": True, "technical": True, "comms": True})
    events = c.get("/api/guardian/activity/kid").json()["events"]
    assert [e["feature"] for e in events] == ["sms", "calls", "social", "bible"]


def test_an_admin_who_is_not_a_parent_does_not(monkeypatch):
    c = _client(monkeypatch, {"allowed": True, "technical": True, "comms": False})
    body = c.get("/api/guardian/activity/kid").json()
    assert [e["feature"] for e in body["events"]] == ["bible"]
    assert body["comms_hidden"] is True
    assert "secret" not in str(body)


def test_the_recap_and_concerns_lose_them_too():
    recap = {"recap": {"comms": {"calls": 3}, "screen": {}, "concerns": [{"feature": "sms", "excerpt": "x"}, {"feature": "web"}]}}
    out = without_comms(recap)["recap"]
    assert "comms" not in out and out["concerns"] == [{"feature": "web"}]
    assert without_comms({"concerns": [{"feature": "social"}, {"feature": "media"}]})["concerns"] == [{"feature": "media"}]


def test_the_calls_and_messages_tab_is_refused_to_a_non_parent(monkeypatch):
    c = _client(monkeypatch, {"allowed": True, "technical": True, "comms": False})
    assert c.get("/api/guardian/comms/kid").status_code == 403


def test_the_calls_and_messages_tab_for_a_parent(monkeypatch):
    c = _client(monkeypatch, {"allowed": True, "technical": False, "comms": True})
    assert c.get("/api/guardian/comms/kid", params={"days": 7}).status_code == 200
