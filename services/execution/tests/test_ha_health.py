"""Home Assistant outages are detected, fail fast, and recover by themselves.

Before ha_health a down HA cost every call its 45 s timeout, reads quietly
returned "nothing", and a *hung* HA (a timeout, whose aiohttp message is "")
never produced the word "unreachable" the hardware router's direct ESPHome
fallback keys on - so the fallback only ran for refused connections.
"""

import asyncio
import types
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import aiohttp
import pytest

from services.execution import ha_client, ha_health, hardware_router

HA = "http://ha.local:8123"


class _Resp:
    def __init__(self, status=200, payload=None):
        self.status = status
        self._payload = payload if payload is not None else []

    def raise_for_status(self):
        if self.status >= 400:
            raise aiohttp.ClientResponseError(
                request_info=types.SimpleNamespace(real_url=HA), history=(), status=self.status, message="err"
            )

    async def json(self, content_type=None):
        return self._payload

    async def release(self):
        return None


class _CM:
    def __init__(self, outcome):
        self._outcome = outcome

    async def __aenter__(self):
        if isinstance(self._outcome, BaseException):
            raise self._outcome
        return self._outcome

    async def __aexit__(self, *exc):
        return None


class _Client:
    """Serves a scripted sequence of outcomes (a _Resp or an exception) and counts calls."""

    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0

    def _next(self):
        self.calls += 1
        return _CM(self.outcomes.pop(0) if len(self.outcomes) > 1 else self.outcomes[0])

    def post(self, url, **kw):
        return self._next()

    def get(self, url, **kw):
        return self._next()


@pytest.fixture
def ha(monkeypatch):
    def run(*outcomes):
        client = _Client(outcomes)

        @asynccontextmanager
        async def fake_session(ha_url, verify=True):
            yield client

        monkeypatch.setattr(ha_client, "_ha_session", fake_session)
        monkeypatch.setattr(ha_client.credential_alerts, "auth_failed", AsyncMock())
        monkeypatch.setattr(ha_client.credential_alerts, "auth_ok", AsyncMock())
        return client

    return run


def _refused():
    return aiohttp.ClientConnectorError(
        types.SimpleNamespace(host="ha.local", port=8123, ssl=None), OSError(111, "Connection refused")
    )


async def test_a_timeout_is_reported_as_unreachable_so_the_direct_fallback_fires(ha):
    ha(TimeoutError())
    result = await ha_client.call_service(HA, "tok", "light", "turn_on", "light.porch")
    assert result["ok"] is False
    assert "unreachable" in result["error"] and "timed out" in result["error"]
    assert hardware_router._is_ha_unreachable(result)


async def test_two_unreachable_calls_mark_ha_down_and_later_calls_skip_the_wait(ha):
    client = ha(_refused())
    await ha_client.call_service(HA, "tok", "light", "turn_on", "light.a")
    assert not ha_health.is_down(HA)
    await ha_client.call_service(HA, "tok", "light", "turn_on", "light.b")
    assert ha_health.is_down(HA)

    calls_before = client.calls
    result = await ha_client.call_service(HA, "tok", "light", "turn_on", "light.c")
    assert client.calls == calls_before, "a down HA must not be called again before the probe interval"
    assert result == {"ok": False, "error": result["error"], "ha_down": True}
    assert hardware_router._is_ha_unreachable(result)
    assert await ha_client.get_state(HA, "tok", "light.c") is None
    assert await ha_client.get_states(HA, "tok") == []
    with pytest.raises(ConnectionError):
        await ha_client.get_states(HA, "tok", strict=True)
    assert client.calls == calls_before


async def test_a_probe_after_the_interval_brings_ha_back(ha, monkeypatch):
    client = ha(_refused(), _refused(), _Resp(200))
    await ha_client.call_service(HA, "tok", "light", "turn_on", "light.a")
    await ha_client.call_service(HA, "tok", "light", "turn_on", "light.a")
    assert ha_health.is_down(HA)

    host = ha_health._hosts[ha_health._key(HA)]
    host.last_probe -= ha_health.PROBE_INTERVAL_S + 1
    result = await ha_client.call_service(HA, "tok", "light", "turn_on", "light.a")
    assert result["ok"] is True
    assert client.calls == 3
    assert not ha_health.is_down(HA)
    assert ha_health.status() == {}


async def test_an_http_error_is_an_answer_not_an_outage(ha):
    """HA answering 500 is up: falling back to direct control would be wrong."""
    ha(_Resp(500))
    for _ in range(3):
        result = await ha_client.call_service(HA, "tok", "light", "turn_on", "light.a")
        assert result["ok"] is False and "unreachable" not in result["error"]
    assert not ha_health.is_down(HA)


async def test_admins_are_told_once_after_the_grace_period_and_again_on_recovery(ha, monkeypatch):
    sent = []

    async def fake_send(payload, **kw):
        sent.append(payload)
        return True

    from services.common import notify

    monkeypatch.setattr(notify, "send", fake_send)
    ha(_refused(), _refused(), _Resp(200))
    await ha_client.call_service(HA, "tok", "light", "turn_on", "light.a")
    await ha_client.call_service(HA, "tok", "light", "turn_on", "light.a")
    host = ha_health._hosts[ha_health._key(HA)]

    await ha_client.call_service(HA, "tok", "light", "turn_on", "light.a")  # within grace: quiet
    await asyncio.sleep(0)
    assert sent == []

    host.down_since -= ha_health.NOTIFY_AFTER_S + 1
    for _ in range(3):
        await ha_client.call_service(HA, "tok", "light", "turn_on", "light.a")
    await asyncio.sleep(0)
    assert len(sent) == 1 and "down" in str(sent[0]).lower()

    host.last_probe -= ha_health.PROBE_INTERVAL_S + 1
    await ha_client.call_service(HA, "tok", "light", "turn_on", "light.a")
    await asyncio.sleep(0)
    assert len(sent) == 2 and "back" in str(sent[1]).lower()


def test_status_names_the_host_and_reason():
    ha_health.record_unreachable(HA, TimeoutError())
    assert ha_health.status()["http://ha.local"]["status"] == "failing"
    ha_health.record_unreachable(HA, "connection refused")
    entry = ha_health.status()["http://ha.local"]
    assert entry["status"] == "down" and entry["last_error"] == "connection refused"
