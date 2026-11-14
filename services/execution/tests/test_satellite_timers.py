"""A timer set by voice shows and rings on the voice satellite that heard it (satellite_timers.py)."""
import json
from datetime import UTC, datetime, timedelta
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

    async def set(self, key, val):
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
    calls = AsyncMock(return_value={"ok": True, "status_code": 200})
    monkeypatch.setattr(satellite_timers.ha_client, "call_service", calls)
    monkeypatch.setattr(timer_mod, "get_redis", AsyncMock(return_value=MockRedis()))
    monkeypatch.setattr(timer_mod, "stop_ringing_devices", AsyncMock(return_value=[]))
    return calls


def _ctx(satellite=SAT):
    return UserContext(user="sam", ha_url=HUB, ha_token="tok", voice_satellite=satellite)


def _sent(calls):
    return [(c.args[2], c.args[3], c.kwargs["entity_id"], c.kwargs["service_data"]) for c in calls.await_args_list]


def test_event_data_counts_down_and_stops_at_the_end():
    now = datetime(2026, 1, 5, 12, 0, tzinfo=UTC)
    t = {"id": "t1", "title": "Pasta", "duration_sec": 600, "active": True,
         "expires_at": (now + timedelta(seconds=420)).isoformat()}
    assert satellite_timers.event_data(t, "started", now) == {
        "event": "started", "timer_id": "t1", "name": "Pasta", "total_seconds": 600, "seconds_left": 420,
        "is_active": True}
    paused = {**t, "active": False, "paused_at": (now - timedelta(seconds=60)).isoformat()}
    assert satellite_timers.event_data(paused, "updated", now)["seconds_left"] == 480  # frozen at the pause
    assert satellite_timers.event_data(paused, "updated", now)["is_active"] is False
    done = satellite_timers.event_data(t, "finished", now)
    assert done["seconds_left"] == 0 and done["is_active"] is False
    with pytest.raises(ValueError, match="unknown satellite timer event"):
        satellite_timers.event_data(t, "rang", now)


def test_only_a_satellite_entity_counts():
    assert satellite_timers.valid_satellite(SAT) == SAT
    for bad in (None, "", "light.den", "assist_satellite.Den", "media_player.den_speaker"):
        assert satellite_timers.valid_satellite(bad) is None


async def test_a_timer_set_by_voice_tells_its_satellite_through_its_life(hub_calls):
    add = await handle_timer(TimerRequest(user_context=_ctx(), action="add", duration_str="10m", title="Pasta"))
    assert add.status == "SUCCESS"
    assert add.detail["satellite"] == {"satellite": SAT, "event": "started", "ok": True}
    r = await timer_mod.get_redis()
    stored = json.loads(next(iter(r.store.values())))
    assert stored["satellite"] == SAT

    await handle_timer(TimerRequest(user_context=_ctx(None), action="pause", title="Pasta"))
    await handle_timer(TimerRequest(user_context=_ctx(None), action="resume", title="Pasta"))
    await handle_timer(TimerRequest(user_context=_ctx(None), action="reset", title="Pasta"))
    await handle_timer(TimerRequest(user_context=_ctx(None), action="delete", title="Pasta"))
    sent = _sent(hub_calls)
    # Later steps may come from the app (no satellite in their context): the timer remembers its own.
    assert [(d, s, e, data["event"]) for d, s, e, data in sent] == [
        ("assist_satellite", "timer_event", SAT, "started"),
        ("assist_satellite", "timer_event", SAT, "updated"),
        ("assist_satellite", "timer_event", SAT, "updated"),
        ("assist_satellite", "timer_event", SAT, "updated"),
        ("assist_satellite", "timer_event", SAT, "cancelled"),
    ]
    started, paused = sent[0][3], sent[1][3]
    assert started["timer_id"] == add.detail["timer_id"] and started["name"] == "Pasta"
    assert started["total_seconds"] == 600 and started["is_active"] is True
    assert paused["is_active"] is False
    for c in hub_calls.await_args_list:
        assert c.args[:2] == (HUB, "tok")


async def test_stopping_a_timer_cancels_it_on_the_satellite(hub_calls):
    await handle_timer(TimerRequest(user_context=_ctx(), action="add", duration_str="5m", title="Tea"))
    await handle_timer(TimerRequest(user_context=_ctx(), action="stop", title="Tea"))
    assert [d["event"] for *_x, d in _sent(hub_calls)] == ["started", "cancelled"]


async def test_a_timer_set_from_chat_or_the_app_tells_no_satellite(hub_calls):
    add = await handle_timer(TimerRequest(user_context=_ctx(None), action="add", duration_str="10m", title="Eggs"))
    assert add.status == "SUCCESS" and "satellite" not in add.detail
    await handle_timer(TimerRequest(user_context=_ctx(None), action="delete", title="Eggs"))
    hub_calls.assert_not_awaited()


async def test_a_satellite_that_cannot_be_told_is_reported_not_fatal(hub_calls):
    hub_calls.return_value = {"ok": False, "error": "HA returned 404: no TIMERS"}
    add = await handle_timer(TimerRequest(user_context=_ctx(), action="add", duration_str="10m", title="Rice"))
    assert add.status == "SUCCESS"
    assert add.detail["satellite"] == {"satellite": SAT, "event": "started", "ok": False,
                                       "error": "HA returned 404: no TIMERS"}


async def test_no_hub_url_is_said_out_loud():
    out = await satellite_timers.send({"id": "t1", "satellite": SAT}, "finished", None, None)
    assert out == {"satellite": SAT, "event": "finished", "ok": False,
                   "error": "the hub URL is not configured for this user"}


async def test_a_finished_timer_rings_on_its_satellite(monkeypatch):
    import services.execution.main as m

    monkeypatch.setattr(m, "resolve_internal_user", AsyncMock(return_value={
        "user": "sam", "ha_url": HUB, "ha_token": "tok"}))
    monkeypatch.setattr(m.timer, "notify_timer_done", AsyncMock())
    calls = AsyncMock(return_value={"ok": True})
    monkeypatch.setattr(satellite_timers.ha_client, "call_service", calls)
    await m.execute_trigger({"user_id": "sam", "timer": {"id": "t9", "title": "Bread", "duration_sec": 300,
                                                         "satellite": SAT}})
    assert _sent(calls) == [("assist_satellite", "timer_event", SAT, {
        "event": "finished", "timer_id": "t9", "name": "Bread", "total_seconds": 300, "seconds_left": 0,
        "is_active": False})]
