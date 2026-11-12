"""``/health/ready`` drives the dashboard's Live Service Status board.

It reports every service the control plane lists (each container declares how
it is checked, see services/control_plane/monitored.py), each one's own
status, and turns the overall status DEGRADED when any is down.
"""
from contextlib import asynccontextmanager

from fastapi.testclient import TestClient

from services.gateway import main as gateway_main
from services.gateway import readiness
from services.gateway.main import app

CP = "http://cp.invalid:8008"
LISTED = [
    {"name": n, "container": f"sharedllm_{n}", "state": "running", "check": "http",
     "health_url": f"http://{n}:9000/health"}
    for n in ("identity", "geo", "bible", "homehub")
] + [{"name": "sidecar", "container": "sharedllm_sidecar", "state": "running", "check": "container state"}]


def _patch(monkeypatch, down: str | None = None):
    class _Resp:
        def __init__(self, body):
            self.status, self._body = 200, body

        async def json(self, content_type=None):
            return self._body

    class _Client:
        # Like aiohttp: get() is used as an async context manager.
        @asynccontextmanager
        async def get(self, url, **kw):
            if url == f"{CP}/api/monitored":
                yield _Resp({"services": LISTED})
                return
            if down and url.startswith(f"http://{down}:"):
                raise ConnectionError("refused")
            yield _Resp({"status": "ok", "git_sha": "abc123", "start_time": 1.0})

    @asynccontextmanager
    async def fake():
        yield _Client()

    async def _no_sleep(_s):
        return None

    monkeypatch.setattr(gateway_main, "shared_http_client", fake)
    monkeypatch.setattr(gateway_main, "CONTROL_PLANE_URL", CP)
    monkeypatch.setattr(gateway_main, "ping_redis", lambda: True)
    monkeypatch.setattr(gateway_main, "_config_validation_result", None)
    monkeypatch.setattr(readiness, "_last", None)
    monkeypatch.setattr(readiness.asyncio, "sleep", _no_sleep)


def test_ready_covers_every_listed_service(monkeypatch):
    _patch(monkeypatch)
    body = TestClient(app).get("/health/ready").json()
    assert body["status"] == "READY"
    for name in ("identity", "geo", "bible", "homehub", "sidecar", "gateway", "redis"):
        assert body["services"][name] == "OK", name
    assert body["service_details"]["geo"]["git_sha"] == "abc123"
    assert body["service_details"]["sidecar"]["check"] == "container state"
    assert body["inventory"]["source"] == "control_plane"


def test_one_service_down_degrades_only_that_service(monkeypatch):
    _patch(monkeypatch, down="bible")
    body = TestClient(app).get("/health/ready").json()
    assert body["status"] == "DEGRADED"
    assert body["services"]["bible"] == "UNREACHABLE"
    assert body["services"]["geo"] == "OK"
