"""Admin > Network routes (docs/NETWORK.md): admins only, and the router's
password goes to Identity and nowhere else."""
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from services.gateway import main as gateway_main
from services.gateway import network_routes
from services.gateway.main import app

ADMIN = {"Authorization": "Bearer admin-key"}
KID = {"Authorization": "Bearer kid-key"}


@pytest.fixture
def calls(monkeypatch):
    seen = []

    async def admin(request):
        auth = request.headers.get("Authorization")
        if auth == "Bearer admin-key":
            return "jeremiah"
        raise HTTPException(status_code=403 if auth else 401, detail="Admin only")

    async def call(method, url, *, params=None, json=None):
        seen.append((method, url, params, json))
        if url.endswith("/api/network/status") or url.endswith("/api/network/check"):
            return {"configured": True, "connected": True, "radios": []}
        if url.endswith("/api/network/log"):
            return {"lines": []}
        if url.endswith("/api/network/outages"):
            return {"days": params["days"], "outages": []}
        if url.endswith("/api/network/usage"):
            return {"hours": params["hours"], "devices": []}
        if url.endswith("/api/admin/seed-credential"):
            return {"status": "SUCCESS"}
        raise AssertionError(url)

    monkeypatch.setattr(gateway_main, "_require_admin", admin)
    monkeypatch.setattr(network_routes, "_call", call)
    return seen


@pytest.fixture
def client():
    return TestClient(app, raise_server_exceptions=False)


@pytest.mark.parametrize("verb,path", [("get", "/api/network/status"), ("get", "/api/network/log"),
                                       ("get", "/api/network/usage"), ("get", "/api/network/outages"),
                                       ("post", "/api/network/check"),
                                       ("put", "/api/network/settings")])
def test_only_admins_see_the_network(client, calls, verb, path):
    kwargs = {"json": {"url": "https://192.168.2.1", "username": "jarvis"}} if verb == "put" else {}
    assert getattr(client, verb)(path, headers=KID, **kwargs).status_code == 403
    assert getattr(client, verb)(path, **kwargs).status_code == 401
    assert calls == []


def test_status_log_and_usage_come_from_the_monitor(client, calls):
    assert client.get("/api/network/status", headers=ADMIN).json()["connected"] is True
    client.get("/api/network/log?level=err&q=hostapd", headers=ADMIN)
    assert client.get("/api/network/usage?hours=168", headers=ADMIN).json()["hours"] == 168
    assert calls[1][2] == {"limit": 200, "level": "err", "q": "hostapd"}


def test_saving_settings_stores_them_in_identity_then_checks(client, calls):
    resp = client.put("/api/network/settings", headers=ADMIN,
                      json={"url": "https://192.168.2.1/", "username": " jarvis ", "password": "r0uter-pw"})
    assert resp.status_code == 200 and "r0uter-pw" not in resp.text
    seeded = [c[3] for c in calls if c[1].endswith("/seed-credential")]
    assert seeded == [{"field": "router_url", "value": "https://192.168.2.1"},
                      {"field": "router_user", "value": "jarvis"},
                      {"field": "router_pass", "value": "r0uter-pw"}]
    assert calls[-1][1].endswith("/api/network/check")


def test_a_blank_password_keeps_the_stored_one(client, calls):
    client.put("/api/network/settings", headers=ADMIN, json={"url": "https://192.168.2.1", "username": "jarvis"})
    assert [c[3]["field"] for c in calls if c[1].endswith("/seed-credential")] == ["router_url", "router_user"]


@pytest.mark.parametrize("url", ["http://192.168.2.1", "192.168.2.1", "https://"])
def test_the_router_address_must_be_https(client, calls, url):
    resp = client.put("/api/network/settings", headers=ADMIN, json={"url": url, "username": "jarvis"})
    assert resp.status_code == 400 and calls == []
