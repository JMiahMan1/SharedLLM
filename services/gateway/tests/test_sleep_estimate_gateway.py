"""Sleep estimated from device use: only the estimate comes back, only the caller's devices are read."""
from contextlib import asynccontextmanager

import pytest
from fastapi.testclient import TestClient

from services.gateway import main as gateway_main
from services.gateway.main import app

DEVICE = "tablet-key-0000001"


@pytest.fixture
def client(monkeypatch):
    async def _resolve(_request):
        return {"user": "kid", "user_id": 7, "is_admin": False}

    monkeypatch.setattr(gateway_main, "_resolve_identity_from_request", _resolve)
    monkeypatch.setenv("TIMEZONE", "America/Phoenix")
    return TestClient(app, headers={"Authorization": "Bearer test-token"})


class _Resp:
    def __init__(self, body, status=200):
        self._body, self.status = body, status

    async def json(self, content_type=None):
        return self._body


def _services(monkeypatch, *, owned=(DEVICE,), registered=True, nights=()):
    calls: list[tuple[str, str, dict]] = []

    class _Client:
        async def get(self, url, **kw):
            calls.append(("GET", url, kw))
            if "/owned-devices/" in url:
                return _Resp({"devices": list(owned)})
            if "/device-registered/" in url:
                return _Resp({"registered": registered, "owner": "kid"})
            return _Resp({"status": "SUCCESS", "nights": list(nights)})

        async def post(self, url, **kw):
            calls.append(("POST", url, kw))
            return _Resp({"status": "SUCCESS"})

    @asynccontextmanager
    async def fake():
        yield _Client()

    monkeypatch.setattr(gateway_main, "borrow_http_client", fake)

    async def _proxy(resp):
        return resp._body

    monkeypatch.setattr(gateway_main, "_proxy_json_response", _proxy)
    return calls


# 2026-10-20 22:30 and 2026-10-21 06:30 in Phoenix (UTC-7).
BED, WAKE = 1792560600000, 1792589400000


def test_estimate_from_the_callers_devices(client, monkeypatch):
    calls = _services(monkeypatch, nights=[{"intervals": [[BED - 1_800_000, BED], [WAKE, WAKE + 600_000]]}])
    body = client.get("/api/health/sleep/estimate", params={"night": "2026-10-21"}).json()
    est = body["estimate"]
    assert est["bedtime"] == "2026-10-20T22:30-07:00" and est["wake_time"] == "2026-10-21T06:30-07:00"
    assert est["duration_minutes"] == 480
    assert "intervals" not in str(body)
    assert calls[0][1].endswith("/owned-devices/kid")
    assert calls[1][2]["params"]["devices"] == DEVICE


def test_no_devices_no_estimate(client, monkeypatch):
    _services(monkeypatch, owned=())
    assert client.get("/api/health/sleep/estimate", params={"night": "2026-10-21"}).json()["estimate"] is None


def test_bad_night_is_refused(client, monkeypatch):
    _services(monkeypatch)
    assert client.get("/api/health/sleep/estimate", params={"night": "yesterday"}).status_code == 422


def test_posting_a_night_records_the_poster(client, monkeypatch):
    calls = _services(monkeypatch)
    r = client.post("/api/usage/night", json={"device_key": DEVICE, "night": "2026-10-21", "intervals": [[1, 2]]})
    assert r.status_code == 200
    post = next(c for c in calls if c[0] == "POST")
    assert post[2]["json"] == {"device": DEVICE, "night": "2026-10-21", "user": "kid", "intervals": [[1, 2]]}


def test_an_unregistered_device_cannot_post(client, monkeypatch):
    calls = _services(monkeypatch, registered=False)
    r = client.post("/api/usage/night", json={"device_key": DEVICE, "night": "2026-10-21", "intervals": []})
    assert r.status_code == 404
    assert not [c for c in calls if c[0] == "POST"]
