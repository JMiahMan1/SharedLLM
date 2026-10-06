"""The household DNS mappings: only an admin may read or change them.

None of these routes checked the caller; they were unreachable only because
Caddy sent /api/admin* elsewhere, and routing them correctly exposed them.
"""
import pytest
from fastapi.testclient import TestClient

from services.gateway import main as gateway_main
from services.gateway.main import app

CALLS = [
    ("get", "/api/admin/dns", None),
    ("post", "/api/admin/dns/register", {"hostname": "evil.lan", "ip": "10.0.0.66"}),
    ("delete", "/api/admin/dns/jarvis.lan", None),
    ("post", "/api/admin/dns/update", {"dns_upstream": "6.6.6.6"}),
]


def _as(monkeypatch, ident):
    async def acting(request):
        return ident
    monkeypatch.setattr(gateway_main, "_acting_identity", acting)

    async def setting(key, default=""):
        raise AssertionError("an unauthorised caller reached the DNS settings")
    monkeypatch.setattr(gateway_main, "fetch_global_setting", setting)


@pytest.mark.parametrize("verb,path,body", CALLS)
def test_anonymous_callers_are_refused(monkeypatch, verb, path, body):
    _as(monkeypatch, None)
    resp = getattr(TestClient(app), verb)(path, **({"json": body} if body else {}))
    assert resp.status_code == 401


@pytest.mark.parametrize("verb,path,body", CALLS)
def test_non_admins_are_refused(monkeypatch, verb, path, body):
    _as(monkeypatch, {"user": "michele", "is_admin": False})
    resp = getattr(TestClient(app), verb)(path, **({"json": body} if body else {}))
    assert resp.status_code == 403
