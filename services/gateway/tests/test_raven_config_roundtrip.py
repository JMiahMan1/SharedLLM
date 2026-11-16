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
        return {"username": "alex", "is_admin": True}

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
        return {"username": "sam", "is_admin": False}

    monkeypatch.setattr(gateway_main, "_resolve_identity_from_request", _member)

    assert TestClient(app).get("/api/admin/raven/config").status_code == 403


class _Resp:
    def __init__(self, status=200):
        self.status = status

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _Client:
    def __init__(self, voice_status=200):
        self.calls = []
        self.voice_status = voice_status

    async def patch(self, url, json=None, headers=None):
        self.calls.append(("PATCH", url, json))
        return _Resp()

    def post(self, url, json=None, headers=None):
        self.calls.append(("POST", url, json))
        return _Resp(self.voice_status)


def _patch_client(monkeypatch, client):
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _borrow():
        yield client

    async def _admin(_request):
        return {"username": "alex", "is_admin": True}

    monkeypatch.setattr(gateway_main, "_resolve_identity_from_request", _admin)
    monkeypatch.setattr(gateway_main, "borrow_http_client", _borrow)


def test_a_new_voice_is_saved_and_handed_to_the_speech_engine_at_once(monkeypatch):
    client = _Client()
    _patch_client(monkeypatch, client)
    resp = TestClient(app).patch("/api/admin/raven/config", json={"system_default_tts_voice": "bm_fable,bm_george"})
    assert resp.status_code == 200
    assert client.calls[0][0] == "PATCH" and client.calls[0][1].endswith("/api/settings/system_default_tts_voice")
    assert client.calls[1][0] == "POST" and client.calls[1][1].endswith("/execute/tts/default")
    assert client.calls[1][2] == {"voice": "bm_fable,bm_george"}


def test_a_speech_engine_that_refuses_the_voice_is_reported(monkeypatch):
    _patch_client(monkeypatch, _Client(voice_status=503))
    resp = TestClient(app).patch("/api/admin/raven/config", json={"system_default_tts_voice": "bm_george"})
    assert resp.status_code == 502 and "next restart" in resp.json()["detail"]
