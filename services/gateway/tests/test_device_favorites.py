"""GET /api/devices/favorites: the Devices widget's pins, with live state, compact.

Small clients (the watch) read their device buttons from this. Identity and
the execution service are faked; the route's job is the join and the shape.
"""
import json
from contextlib import asynccontextmanager

from fastapi.testclient import TestClient

from services.gateway import main as gateway_main
from services.gateway.main import app


class _Resp:
    def __init__(self, status, payload):
        self.status = status
        self._payload = payload
        self.text = json.dumps(payload)

    async def json(self):
        return self._payload

    async def read(self):
        return self.text.encode()


def _install(monkeypatch, widgets, search_payload, identity_status=200):
    seen = {}

    class _IdentityClient:
        async def get(self, url, **kw):
            seen["identity_url"] = url
            seen["identity_auth"] = (kw.get("headers") or {}).get("Authorization")
            return _Resp(identity_status, {"widgets": widgets})

        async def post(self, url, **kw):  # request logging shares the client
            return _Resp(200, {})

    @asynccontextmanager
    async def fake_shared():
        yield _IdentityClient()

    class _ExecClient:
        async def post(self, url, **kw):
            seen["exec_url"] = url
            seen["exec_json"] = kw.get("json")
            return _Resp(200, search_payload)

    async def fake_ctx(request, body):
        return {"user": "jeremiah"}

    monkeypatch.setattr(gateway_main, "shared_http_client", fake_shared)
    monkeypatch.setattr(gateway_main, "get_http_client", lambda: _ExecClient())
    monkeypatch.setattr(gateway_main, "_resolve_user_context", fake_ctx)
    return seen


def test_favorites_joined_with_state_in_pin_order(monkeypatch):
    widgets = [{"widget_key": "device_control", "pinned_devices": ["switch.fan", "light.office", "lock.front"]}]
    search = {"status": "SUCCESS", "detail": {"entities": [
        {"entity_id": "light.office", "friendly_name": "Office Light", "state": "on"},
        {"entity_id": "switch.fan", "friendly_name": "Fan", "state": "off"},
    ]}}
    seen = _install(monkeypatch, widgets, search)
    resp = TestClient(app).get("/api/devices/favorites", headers={"Authorization": "Bearer sk-x"})
    assert resp.status_code == 200
    assert resp.json()["devices"] == [
        {"entity_id": "switch.fan", "domain": "switch", "name": "Fan", "state": "off"},
        {"entity_id": "light.office", "domain": "light", "name": "Office Light", "state": "on"},
        # not visible to this user (or gone): reported, not dropped or leaked
        {"entity_id": "lock.front", "domain": "lock", "name": "lock.front", "state": "unavailable"},
    ]
    assert seen["identity_auth"] == "Bearer sk-x"
    assert seen["exec_json"]["domain"] == "light,lock,switch"
    assert seen["exec_json"]["user_context"] == {"user": "jeremiah"}


def test_no_pins_says_how_to_add_them(monkeypatch):
    seen = _install(monkeypatch, [{"widget_key": "device_control", "pinned_devices": []}], {})
    body = TestClient(app).get("/api/devices/favorites", headers={"Authorization": "Bearer sk-x"}).json()
    assert body["devices"] == []
    assert "Devices widget" in body["message"]
    assert "exec_url" not in seen  # nothing to look up


def test_identity_rejection_is_passed_through(monkeypatch):
    _install(monkeypatch, [], {}, identity_status=401)
    resp = TestClient(app).get("/api/devices/favorites", headers={"Authorization": "Bearer bad"})
    assert resp.status_code == 401


def test_search_failure_is_an_error_not_an_empty_list(monkeypatch):
    widgets = [{"widget_key": "device_control", "pinned_devices": ["light.office"]}]
    _install(monkeypatch, widgets, {"status": "FAILURE", "message": "Home Assistant URL or token not configured"})
    resp = TestClient(app).get("/api/devices/favorites", headers={"Authorization": "Bearer sk-x"})
    assert resp.status_code == 502
    assert "not configured" in resp.json()["detail"]


def test_device_pairing_route_forwards_to_execution_as_the_user(monkeypatch):
    """POST /api/devices/pair exists on the gateway and carries the user's context."""
    seen = {}

    class _ExecClient:
        async def post(self, url, **kw):
            if "/execute/" in url:  # request logging shares this client
                seen["url"] = url
                seen["json"] = kw.get("json")
            return _Resp(200, {"status": "SUCCESS", "message": "Enter the code shown on Jarvis Watch.",
                               "detail": {"method": "code"}})

    async def fake_ctx(request, body):
        return {"user": "jeremiah", "api_key": "sk-x"}

    monkeypatch.setattr(gateway_main, "get_http_client", lambda: _ExecClient())
    monkeypatch.setattr(gateway_main, "_resolve_user_context", fake_ctx)
    resp = TestClient(app).post("/api/devices/pair",
                                json={"step": "start", "host": "192.168.2.105", "jarvis_url": "http://evil.example"},
                                headers={"Authorization": "Bearer sk-x", "X-Forwarded-Proto": "https",
                                         "X-Forwarded-Host": "jarvis.sumemail.com"})
    assert resp.status_code == 200
    assert seen["url"].endswith("/execute/esphome/pair")
    # the address the user reached Jarvis at, not one the caller supplies
    assert seen["json"]["jarvis_url"] == "https://jarvis.sumemail.com"
    assert seen["json"]["step"] == "start" and seen["json"]["user_context"]["user"] == "jeremiah"
