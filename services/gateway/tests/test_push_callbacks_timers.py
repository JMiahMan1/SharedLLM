"""Timer notification buttons: Stop, Snooze 5 min, +1 min, acting as the caller."""
import pytest
from fastapi.responses import JSONResponse

from services.gateway import push_callbacks


@pytest.fixture
def execution(monkeypatch):
    """Stand in for the gateway's execution proxy; records what each button asks."""
    from services.gateway import main

    calls = []
    answer = {"status": "SUCCESS", "message": "ok"}

    async def proxy(request, endpoint, payload=None, **_):
        calls.append((request, endpoint, payload))
        return JSONResponse(content=answer)

    monkeypatch.setattr(main, "_proxy_execution_with_identity", proxy)
    return calls, answer


def test_the_timer_buttons_are_registered():
    for name in ("timer_stop", "timer_snooze", "timer_add_minute"):
        assert name in push_callbacks.PUSH_CALLBACKS


async def test_snooze_adds_five_minutes_with_the_same_name_and_speaker(execution):
    calls, _ = execution
    request = object()
    params = {"title": "Pasta", "type": "timer", "target_device": "kitchen", "user": "someone_else"}
    assert (await push_callbacks.PUSH_CALLBACKS["timer_snooze"](request, params, None))["status"] == "SUCCESS"
    ((req, endpoint, payload),) = calls
    assert req is request and endpoint == "/execute/timer"
    assert payload == {"action": "add", "type": "timer", "duration_str": "5m", "title": "Pasta", "target_device": "kitchen"}


async def test_one_more_minute_keeps_an_alarm_an_alarm(execution):
    calls, _ = execution
    await push_callbacks.PUSH_CALLBACKS["timer_add_minute"](object(), {"title": "Wake", "type": "alarm"}, None)
    assert calls[0][2] == {"action": "add", "type": "alarm", "duration_str": "1m", "title": "Wake"}


async def test_stop_deletes_a_one_off_by_id_and_leaves_a_recurring_alarm(execution):
    calls, answer = execution
    await push_callbacks.PUSH_CALLBACKS["timer_stop"](object(), {"id": "t1"}, None)
    assert calls[0][2] == {"action": "delete", "id": "t1"}
    calls.clear()
    assert (await push_callbacks.PUSH_CALLBACKS["timer_stop"](object(), {}, None))["status"] == "SUCCESS"
    assert calls == []
    # Already fired and removed: stopping it is still a success
    answer.update(status="FAILURE", message="No matching timer found.")
    assert (await push_callbacks.PUSH_CALLBACKS["timer_stop"](object(), {"id": "t1"}, None))["status"] == "SUCCESS"
