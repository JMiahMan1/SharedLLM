"""``/health/ready`` drives the dashboard's Live Service Status board.

It must cover the family-facing services too (geo, bible, telemetry), report
each one's own status, and turn the overall status DEGRADED when any is down.
"""
from contextlib import asynccontextmanager

from fastapi.testclient import TestClient

from services.gateway import main as gateway_main
from services.gateway.main import app


def _patch_probes(monkeypatch, down_prefix: str | None = None):
    class _Resp:
        def __init__(self, status):
            self.status = status

        async def json(self):
            return {"status": "ok", "git_sha": "abc123", "start_time": 1.0}

    class _Client:
        # Like aiohttp: get() is used as an async context manager.
        @asynccontextmanager
        async def get(self, url, **kw):
            if down_prefix and url.startswith(down_prefix):
                raise ConnectionError("refused")
            yield _Resp(200)

    @asynccontextmanager
    async def fake():
        yield _Client()

    async def _no_sleep(_s):
        return None

    monkeypatch.setattr(gateway_main, "shared_http_client", fake)
    monkeypatch.setattr(gateway_main, "ping_redis", lambda: True)
    monkeypatch.setattr(gateway_main, "_config_validation_result", None)
    monkeypatch.setattr(gateway_main.asyncio, "sleep", _no_sleep)


def test_ready_covers_family_services(monkeypatch):
    _patch_probes(monkeypatch)
    body = TestClient(app).get("/health/ready").json()
    assert body["status"] == "READY"
    for name in ("identity", "rag", "geo", "bible", "telemetry", "gateway", "redis"):
        assert body["services"][name] == "OK", name
    assert body["service_details"]["geo"]["git_sha"] == "abc123"


def test_one_service_down_degrades_only_that_service(monkeypatch):
    _patch_probes(monkeypatch, down_prefix=str(gateway_main.BIBLE_SVC))
    body = TestClient(app).get("/health/ready").json()
    assert body["status"] == "DEGRADED"
    assert body["services"]["bible"] == "UNREACHABLE"
    assert body["services"]["geo"] == "OK"
    assert "bible" not in body["service_details"]


def test_response_log_level_marks_failures():
    assert gateway_main._response_log_level(200) == "INFO"
    assert gateway_main._response_log_level(401) == "WARNING"
    assert gateway_main._response_log_level(502) == "ERROR"
    assert gateway_main._response_log_level("N/A") == "INFO"
