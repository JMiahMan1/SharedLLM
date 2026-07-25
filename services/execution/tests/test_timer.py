# services/execution/tests/test_timer.py
import json
from datetime import datetime, timedelta

import pytest

from services.execution.handlers.timer import handle_timer
from services.execution.schemas import TimerRequest, UserContext


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
        if key in self.store:
            del self.store[key]
            return 1
        return 0

mock_context = UserContext(
    user="test_user",
    ha_url="http://ha.local",
    ha_token="mock-token"
)

@pytest.fixture
def mock_redis_conn(mocker):
    mr = MockRedis()
    mocker.patch("services.execution.handlers.timer.get_redis", return_value=mr)
    return mr

@pytest.mark.asyncio
async def test_timer_add_and_list(mock_redis_conn):
    # Add a timer
    add_req = TimerRequest(
        user_context=mock_context,
        action="add",
        type="timer",
        duration_str="10m",
        title="Kitchen Timer"
    )

    res = await handle_timer(add_req)
    assert res.status == "SUCCESS"
    assert "Set timer" in res.message

    # List timers
    list_req = TimerRequest(
        user_context=mock_context,
        action="list"
    )

    list_res = await handle_timer(list_req)
    assert list_res.status == "SUCCESS"
    assert "Kitchen Timer" in list_res.message

@pytest.mark.asyncio
async def test_timer_delete(mock_redis_conn):
    # Add a timer first
    add_req = TimerRequest(
        user_context=mock_context,
        action="add",
        type="timer",
        duration_str="10m",
        title="Delete Me Timer"
    )
    await handle_timer(add_req)

    # Delete the timer
    del_req = TimerRequest(
        user_context=mock_context,
        action="delete",
        title="Delete Me"
    )

    res = await handle_timer(del_req)
    assert res.status == "SUCCESS"
    assert "Deleted" in res.message

    # Verify it is deleted
    list_req = TimerRequest(
        user_context=mock_context,
        action="list"
    )
    list_res = await handle_timer(list_req)
    assert "Delete Me Timer" not in list_res.message

@pytest.mark.asyncio
async def test_timer_pause_and_resume(mock_redis_conn, mocker):
    # Add a timer
    add_req = TimerRequest(
        user_context=mock_context,
        action="add",
        type="timer",
        duration_str="10m",
        title="Workout Timer"
    )
    add_res = await handle_timer(add_req)
    assert add_res.detail is not None
    timer_id = add_res.detail["timer_id"]

    # Pause it
    pause_req = TimerRequest(
        user_context=mock_context,
        action="pause",
        title="Workout"
    )
    pause_res = await handle_timer(pause_req)
    assert pause_res.status == "SUCCESS"

    # Verify in DB that it is paused
    db_val = await mock_redis_conn.get(f"timer:test_user:{timer_id}")
    t = json.loads(db_val)
    assert t["active"] is False
    assert "paused_at" in t

    # Mock system time for resume to be 5 minutes later
    original_expiry = datetime.fromisoformat(t["expires_at"])
    paused_at_time = datetime.fromisoformat(t["paused_at"])
    resume_time = paused_at_time + timedelta(minutes=5)

    mocker.patch("services.execution.handlers.timer.datetime", mocker.Mock(
        now=lambda *args, **kwargs: resume_time,
        fromisoformat=datetime.fromisoformat
    ))

    # Resume it
    resume_req = TimerRequest(
        user_context=mock_context,
        action="resume",
        title="Workout"
    )
    resume_res = await handle_timer(resume_req)
    assert resume_res.status == "SUCCESS"

    # Verify in DB that it is active and expires_at was pushed by 5 minutes
    db_val_res = await mock_redis_conn.get(f"timer:test_user:{timer_id}")
    t_res = json.loads(db_val_res)
    assert t_res["active"] is True
    assert "paused_at" not in t_res

    new_expiry = datetime.fromisoformat(t_res["expires_at"])
    if new_expiry.tzinfo:
        new_expiry = new_expiry.replace(tzinfo=None)
    expected_expiry = original_expiry + timedelta(minutes=5)
    if expected_expiry.tzinfo:
        expected_expiry = expected_expiry.replace(tzinfo=None)
    assert abs((new_expiry - expected_expiry).total_seconds()) < 1.0
