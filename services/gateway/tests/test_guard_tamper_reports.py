"""Guard tamper reports from a family PC: kept or refused, never silently lost.

The Windows agent (agent/jarvis-guard) keeps a report queued until this route
answers 200, so a report identity did not keep must not come back as success.
"""
from contextlib import asynccontextmanager

import aiohttp
import pytest
from fastapi.testclient import TestClient

from services.gateway import main as gateway_main
from services.gateway.main import app


@pytest.fixture
def pc(monkeypatch):
    """A family PC presenting its device key; identity answers what each test sets."""
    calls: list[dict] = []
    sent: list[dict] = []
    identity: dict = {"status": 200, "body": {"parents": ["parent-a", "parent-b"], "display_name": "Kid One", "device": "PC"}}

    async def _device(_request):
        return {"device_key": "windows-test-device", "owner_username": "kid", "kind": "windows-pc"}

    class _Resp:
        def __init__(self, status, body):
            self.status, self._body = status, body

        async def json(self, content_type=None):
            return self._body

    class _Client:
        async def post(self, url, **kw):
            calls.append({"url": url, **kw})
            if isinstance(identity.get("raise"), Exception):
                raise identity["raise"]
            return _Resp(identity["status"], identity["body"])

    @asynccontextmanager
    async def fake():
        yield _Client()

    async def fake_send(payload, **kw):
        sent.append(payload)
        return True

    monkeypatch.setattr(gateway_main, "_device_agent_key", _device)
    monkeypatch.setattr(gateway_main, "borrow_http_client", fake)
    monkeypatch.setattr(gateway_main._notify, "send", fake_send)
    return TestClient(app, headers={"Authorization": "Bearer fake-device-key"}), calls, sent, identity


def test_a_report_identity_did_not_keep_is_an_error_so_the_pc_sends_it_again(pc):
    client, calls, sent, identity = pc
    identity.update(status=500, body={"detail": "database is locked"})
    r = client.post("/api/guard/tamper", json={"event": "offline_unlock", "detail": "parent-a"})
    assert r.status_code == 502
    assert "Send it again" in r.json()["detail"]
    assert calls[-1]["url"].endswith("/api/internal/guard/tamper/kid")
    assert sent == []  # nobody is told of a report nobody kept


def test_identity_unreachable_is_an_error_too(pc):
    client, _, sent, identity = pc
    identity["raise"] = aiohttp.ClientConnectionError("connection refused")
    r = client.post("/api/guard/tamper", json={"event": "tray_missing"})
    assert r.status_code == 502
    assert sent == []


def test_an_offline_unlock_names_whose_code_it_was(pc):
    client, calls, sent, _ = pc
    r = client.post("/api/guard/tamper", json={"event": "offline_unlock", "detail": "parent-a"})
    assert r.status_code == 200
    # The device key wins over anything the body claims.
    assert calls[-1]["json"] == {"event": "offline_unlock", "device_key": "windows-test-device", "detail": "parent-a"}
    assert [n["user"] for n in sent] == ["parent-a", "parent-b"]
    assert sent[0]["body"] == "Kid One's PC was unlocked with parent-a's offline code."


def test_wrong_offline_codes_are_reported_with_how_many(pc):
    client, _, sent, _ = pc
    assert client.post("/api/guard/tamper", json={"event": "offline_code_wrong", "detail": "3 wrong codes"}).status_code == 200
    assert sent[-1]["body"] == "Kid One's PC had 3 wrong codes typed in at its offline unlock."


def test_the_pcs_own_events_are_known(pc):
    client, _, sent, _ = pc
    assert client.post("/api/guard/tamper", json={"event": "tray_missing", "detail": "screen time on this PC is not being counted"}).status_code == 200
    assert "without Jarvis Guard's tray" in sent[-1]["body"]
    assert client.post("/api/guard/tamper", json={"event": "guard_pipe_taken"}).status_code == 200
    assert sent[-1]["body"].endswith("which stopped the guard from starting.")


def test_a_phones_offline_unlock_without_a_detail_keeps_the_plain_sentence(pc):
    client, _, sent, _ = pc
    assert client.post("/api/guard/tamper", json={"event": "offline_unlock"}).status_code == 200
    assert sent[-1]["body"] == "Kid One's PC was unlocked with a parent's offline code."


def test_the_new_tamper_reasons_are_known(pc):
    client, _, sent, _ = pc
    for event in ("jarvis_settings_page", "device_admin", "guard_accessibility",
                  "vpn_disconnect", "factory_reset_or_users", "jarvis_install"):
        assert client.post("/api/guard/tamper", json={"event": event}).status_code == 200
