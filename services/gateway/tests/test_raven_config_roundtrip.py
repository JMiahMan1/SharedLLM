"""The Raven admin config must read back every field the admin panel saves.

`raven_max_total_seconds` is written by PATCH /api/admin/raven/config and used
by the agent loop, but GET left it out, so the admin field always showed the
1800 default whatever had been saved.
"""
from fastapi.testclient import TestClient

from services.gateway import main as gateway_main
from services.gateway.main import app


def test_get_raven_config_returns_saved_mission_lifetime_cap(monkeypatch):
    async def _admin(_request):
        return {"username": "jeremiah", "is_admin": True}

    async def _settings():
        return {
            "raven_suspended": "false",
            "raven_scan_interval": "300",
            "raven_error_threshold": "5",
            "raven_max_total_seconds": "3600",
            "coding_model": "coder",
        }

    monkeypatch.setattr(gateway_main, "_resolve_identity_from_request", _admin)
    monkeypatch.setattr(gateway_main, "get_llm_settings", _settings)

    resp = TestClient(app).get("/api/admin/raven/config")

    assert resp.status_code == 200
    body = resp.json()
    assert body["raven_max_total_seconds"] == 3600
    assert body["raven_scan_interval"] == 300


def test_get_raven_config_is_admin_only(monkeypatch):
    async def _member(_request):
        return {"username": "michele", "is_admin": False}

    monkeypatch.setattr(gateway_main, "_resolve_identity_from_request", _member)

    assert TestClient(app).get("/api/admin/raven/config").status_code == 403
