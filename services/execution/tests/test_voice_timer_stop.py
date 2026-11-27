"""Bare stop from a satellite with no fired timer does not claim ringing."""
from unittest.mock import AsyncMock

import pytest

from services.execution import satellite_timers
from services.execution.handlers import timer as timer_mod
from services.execution.handlers.timer import handle_timer
from services.execution.schemas import TimerRequest, UserContext

HUB = "http://hub.example.test:8123"
SAT = "assist_satellite.den_speaker"


class MockRedis:
    def __init__(self):
        self.store = {}

    async def set(self, key, val, **_):
        self.store[key] = val

    async def get(self, key):
        return self.store.get(key)

    async def keys(self, pattern):
        prefix = pattern.replace("*", "")
        return [k for k in self.store if k.startswith(prefix)]

    async def delete(self, key):
        return 1 if self.store.pop(key, None) is not None else 0


@pytest.fixture
def hub_calls(monkeypatch):
    monkeypatch.setattr(satellite_timers.ha_client, "call_service", AsyncMock(return_value={"ok": True, "status_code": 200}))
    monkeypatch.setattr(timer_mod, "get_redis", AsyncMock(return_value=MockRedis()))
    monkeypatch.setattr(timer_mod, "stop_ringing_devices", AsyncMock(return_value=[]))
    return


@pytest.mark.asyncio
async def test_no_fired_timer_not_ringing(hub_calls):
    ctx = UserContext(user="sam", ha_url=HUB, ha_token="tok", voice_satellite=SAT)
    req = TimerRequest(user_context=ctx, action="stop", target_device=SAT)
    result = await handle_timer(req)
    assert result.detail.get("ringing") is False


@pytest.mark.asyncio
async def test_fired_timer_ringing(hub_calls):
    await timer_mod.get_redis()
    fired = {"id": "t5", "title": "Pasta", "duration_sec": 600, "user_id": "sam", "satellite": SAT,
             "expires_at": "2026-01-05T12:00:00+00:00"}
    await timer_mod.remember_fired_satellite(fired)
    ctx = UserContext(user="sam", ha_url=HUB, ha_token="tok", voice_satellite=SAT)
    req = TimerRequest(user_context=ctx, action="stop", target_device=SAT)
    result = await handle_timer(req)
    assert result.detail.get("ringing") is True
