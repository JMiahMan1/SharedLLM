"""Fasting is shared only among parents, adult children and teens."""
import pytest
from fastapi.testclient import TestClient

from services.gateway import main

ROLES = {"dad": "father", "mom": "mother", "sis": "adult_child", "bro": "teen", "kid": "child", "house": "other"}


@pytest.fixture
def client(monkeypatch):
    sent = []

    async def acting(request):
        return {"user": request.headers.get("X-Test-User", "dad"), "is_admin": False}

    async def role(user):
        return ROLES.get(user, "other")

    async def health(method, path, request, *, body=None, **kw):
        sent.append(body)
        return {"ok": True}

    monkeypatch.setattr(main, "_acting_identity", acting)
    monkeypatch.setattr(main, "_family_role", role)
    monkeypatch.setattr(main, "_health_json", health)
    c = TestClient(main.app, raise_server_exceptions=False)
    c.sent = sent
    return c


@pytest.mark.parametrize("viewer", ["mom", "sis", "bro"])
def test_a_parent_shares_a_fast_with_family_old_enough(client, viewer):
    resp = client.put("/api/health/shares", json={"viewer": viewer, "scope": "fasting"})
    assert resp.status_code == 200 and client.sent[-1]["viewer"] == viewer


@pytest.mark.parametrize("viewer", ["kid", "house", "stranger"])
def test_never_with_a_young_child_or_a_non_family_account(client, viewer):
    resp = client.put("/api/health/shares", json={"viewer": viewer, "scope": "fasting"})
    assert resp.status_code == 403 and client.sent == []


def test_a_young_child_cannot_share_one_either_and_turning_off_is_always_allowed(client):
    assert client.put(
        "/api/health/shares", json={"viewer": "dad", "scope": "fasting"}, headers={"X-Test-User": "kid"},
    ).status_code == 403
    assert client.put(
        "/api/health/shares", json={"viewer": "kid", "scope": "fasting", "enabled": False},
    ).status_code == 200
