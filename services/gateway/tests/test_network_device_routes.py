"""Admin > Network device registry routes through the gateway."""
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
            return "alex"
        raise HTTPException(status_code=403 if auth else 401, detail="Admin only")

    async def call(method, url, *, params=None, json=None):
        seen.append((method, url, params, json))
        if url.endswith("/api/network/devices"):
            if method == "GET":
                return {"devices": []}
            return {"status": "ok"}
        if "/api/network/devices/" in url:
            return {"status": "ok"}
        raise AssertionError(url)

    monkeypatch.setattr(gateway_main, "_require_admin", admin)
    monkeypatch.setattr(network_routes, "_call", call)
    return seen


@pytest.fixture
def client():
    return TestClient(app, raise_server_exceptions=False)


@pytest.mark.parametrize("verb,path", [("get", "/api/network/devices"), ("put", "/api/network/devices/02:00:00:00:00:05"),
                                       ("delete", "/api/network/devices/02:00:00:00:00:05")])
def test_only_admins_manage_devices(client, calls, verb, path):
    kwargs = {"json": {"alias": "x"}} if verb == "put" else {}
    assert getattr(client, verb)(path, headers=KID, **kwargs).status_code == 403
    assert getattr(client, verb)(path, **kwargs).status_code == 401
    assert calls == []


def test_device_routes_proxy(client, calls):
    assert client.get("/api/network/devices", headers=ADMIN).status_code == 200
    r = client.put("/api/network/devices/02:00:00:00:00:05", headers=ADMIN, json={"alias": "Laptop"})
    assert r.status_code == 200
    assert client.delete("/api/network/devices/02:00:00:00:00:05", headers=ADMIN).status_code == 200
    assert calls[0][0] == "GET"
    assert calls[1][0] == "PUT" and calls[1][3] == {"alias": "Laptop"}
    assert calls[2][0] == "DELETE"
