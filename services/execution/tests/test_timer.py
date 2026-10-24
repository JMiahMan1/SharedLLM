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


@pytest.mark.asyncio
async def test_pause_and_resume_target_the_timer_by_id(mock_redis_conn):
    """Pausing "Timer 1" must not pause "Timer 10", and resume must wake the
    timer it was asked about rather than whichever paused one comes first."""
    for title in ("Timer 10", "Timer 1"):
        await handle_timer(TimerRequest(user_context=mock_context, action="add", duration_str="10m", title=title))
    timers = {json.loads(v)["title"]: json.loads(v) for v in mock_redis_conn.store.values()}
    one, ten = timers["Timer 1"], timers["Timer 10"]

    for timer in (ten, one):
        res = await handle_timer(TimerRequest(user_context=mock_context, action="pause", title=timer["title"], id=timer["id"]))
        assert res.detail["timer_id"] == timer["id"]

    res = await handle_timer(TimerRequest(user_context=mock_context, action="resume", title="Timer 1", id=one["id"]))
    assert res.status == "SUCCESS"
    assert res.detail["timer_id"] == one["id"]
    after = {json.loads(v)["id"]: json.loads(v) for v in mock_redis_conn.store.values()}
    assert after[one["id"]]["active"] is True
    assert after[ten["id"]]["active"] is False


def test_duration_parsing_counts_every_part_and_leaves_clock_times_alone():
    from datetime import timedelta

    from services.execution.handlers.timer import _parse_duration

    assert _parse_duration("10m") == timedelta(minutes=10)
    assert _parse_duration("1h30m") == timedelta(minutes=90)
    assert _parse_duration("1 hour 20 minutes") == timedelta(minutes=80)
    assert _parse_duration("90 sec") == timedelta(seconds=90)
    assert _parse_duration("15") == timedelta(minutes=15)
    # A clock time is not a duration: the handler parses it as one instead.
    assert _parse_duration("3pm") is None
    assert _parse_duration("7:30") is None
    assert _parse_duration("tomorrow at 9") is None


class _FakeSession:
    calls: list = []  # noqa: RUF012 - reset by each test
    fail = False

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def post(self, url, json=None, **kwargs):
        if _FakeSession.fail:
            raise OSError("telemetry down")
        _FakeSession.calls.append((url, json))
        return _FakeResp()


class _FakeResp:
    status = 200

    async def json(self, **kwargs):
        return {"status": "SUCCESS"}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


async def test_a_fired_timer_puts_a_notice_on_the_phone(monkeypatch):
    from services.common import http
    from services.execution.handlers.timer import notify_timer_done

    _FakeSession.calls, _FakeSession.fail = [], False
    monkeypatch.setattr(http, "get_client", _FakeSession)
    assert await notify_timer_done("michele", {"id": "t1", "title": "Pasta", "expires_at": "2026-10-15T18:00:00+00:00"})
    (url, body), = _FakeSession.calls
    assert url.endswith("/api/telemetry/notify")
    assert body["user"] == "michele"
    assert body["title"] == "Timer: Pasta" and body["body"] == "Pasta is done"
    assert body["once"] == "timer:t1:2026-10-15T18:00:00+00:00"
    assert body["category"] == "timers" and body["tag"] == "timer:t1"
    stop, snooze, more = body["actions"]
    assert (stop["callback"], stop["params"]) == ("timer_stop", {"id": "t1"})
    assert snooze["callback"] == "timer_snooze" and more["callback"] == "timer_add_minute"
    assert snooze["params"] == {"title": "Pasta", "type": "timer", "target_device": None}


async def test_stop_leaves_a_recurring_alarm_scheduled(monkeypatch):
    from services.common import http
    from services.execution.handlers.timer import notify_timer_done

    _FakeSession.calls, _FakeSession.fail = [], False
    monkeypatch.setattr(http, "get_client", _FakeSession)
    await notify_timer_done("michele", {"id": "a1", "title": "Wake up", "type": "alarm", "recurrence": "daily"})
    (_, body), = _FakeSession.calls
    assert body["title"] == "Alarm: Wake up" and body["kind"] == "alarm"
    assert body["actions"][0]["params"] == {}


async def test_a_notice_failure_does_not_raise(monkeypatch):
    from services.common import http
    from services.execution.handlers.timer import notify_timer_done

    _FakeSession.fail = True
    monkeypatch.setattr(http, "get_client", _FakeSession)
    assert await notify_timer_done("michele", {"title": "Pasta"}) is False


async def test_a_note_reminder_opens_its_note_and_a_snooze_keeps_the_link(monkeypatch):
    from services.common import http
    from services.execution.handlers.timer import notify_timer_done

    _FakeSession.calls, _FakeSession.fail = [], False
    monkeypatch.setattr(http, "get_client", _FakeSession)
    link = "/notes?note=Notes%2FTrip%20ideas.md"
    await notify_timer_done(
        "michele", {"id": "r1", "title": "📝 Trip ideas", "type": "reminder", "url": link, "expires_at": "x"}
    )
    (_, body), = _FakeSession.calls
    assert body["data"]["url"] == link
    assert body["title"] == "Reminder: 📝 Trip ideas"
    assert body["body"] == "📝 Trip ideas"
    assert body["actions"][1]["params"]["url"] == link


async def test_a_stored_link_off_the_app_falls_back_to_communication(monkeypatch):
    from services.common import http
    from services.execution.handlers.timer import notify_timer_done

    _FakeSession.calls, _FakeSession.fail = [], False
    monkeypatch.setattr(http, "get_client", _FakeSession)
    await notify_timer_done("michele", {"id": "r2", "title": "x", "type": "reminder", "url": "https://evil.test/"})
    (_, body), = _FakeSession.calls
    assert body["data"]["url"] == "/communication"
    assert "url" not in body["actions"][1]["params"]


def test_app_link_keeps_only_in_app_paths():
    from services.execution.handlers.timer import app_link

    assert app_link("/notes?note=Notes%2FA.md") == "/notes?note=Notes%2FA.md"
    assert app_link("/notes?note=a:b") == "/notes?note=a:b"
    for bad in ("https://x.test/", "//x.test/a", "javascript:alert(1)", "/\\x.test", "notes", "/a\nb", None, 3, "/" + "a" * 600):
        assert app_link(bad) is None


@pytest.mark.asyncio
async def test_a_reminder_stores_its_link_and_reports_when_it_fires(mock_redis_conn):
    req = TimerRequest(
        user_context=mock_context, action="add", type="reminder", title="📝 Trip ideas",
        time_str="2099-01-01T09:00:00+00:00", url="/notes?note=Notes%2FTrip%20ideas.md",
    )
    res = await handle_timer(req)
    assert res.status == "SUCCESS"
    stored = json.loads(next(iter(mock_redis_conn.store.values())))
    assert stored["url"] == "/notes?note=Notes%2FTrip%20ideas.md"
    assert stored["type"] == "reminder"
    assert res.detail["expires_at"].startswith("2099-01-01T09:00:00")

    bad = TimerRequest(
        user_context=mock_context, action="add", type="reminder", title="x",
        duration_str="10m", url="https://evil.test/",
    )
    await handle_timer(bad)
    assert all("evil" not in value for value in mock_redis_conn.store.values())


class HistoryList(list):
    """A Redis list value: reading it with GET is a WRONGTYPE error."""


class RedisWithHistory(MockRedis):
    async def get(self, key):
        val = self.store.get(key)
        if isinstance(val, HistoryList):
            raise RuntimeError("WRONGTYPE Operation against a key holding the wrong kind of value")
        return val


@pytest.mark.asyncio
async def test_run_history_beside_a_timer_is_not_read_as_a_timer(mocker):
    """Automation keeps timer:<user>:<id>:history lists next to the timers."""
    from services.execution.handlers.timer import get_active_timers

    r = RedisWithHistory()
    mocker.patch("services.execution.handlers.timer.get_redis", return_value=r)
    await handle_timer(TimerRequest(user_context=mock_context, action="add", type="timer", duration_str="10m", title="Pasta"))
    timer_key = next(iter(r.store))
    r.store[f"{timer_key}:history"] = HistoryList(["fired"])

    assert [t["title"] for t in await get_active_timers("test_user")] == ["Pasta"]
    listed = await handle_timer(TimerRequest(user_context=mock_context, action="list"))
    assert "Pasta" in listed.message
    paused = await handle_timer(TimerRequest(user_context=mock_context, action="pause", title="Pasta"))
    assert paused.status == "SUCCESS"
    resumed = await handle_timer(TimerRequest(user_context=mock_context, action="resume", title="Pasta"))
    assert resumed.status == "SUCCESS"
    deleted = await handle_timer(TimerRequest(user_context=mock_context, action="delete", title="Pasta"))
    assert deleted.status == "SUCCESS"
    assert list(r.store) == [f"{timer_key}:history"]


@pytest.mark.asyncio
async def test_household_timer_requires_admin_and_is_visible_to_family(mock_redis_conn):
    from services.execution.handlers.timer import get_active_timers

    non_admin = UserContext(user="kid_user", is_admin=False)
    admin_user = UserContext(user="admin_user", is_admin=True)

    # Non-admin cannot create a household reminder
    denied = await handle_timer(
        TimerRequest(
            user_context=non_admin,
            action="add",
            type="reminder",
            scope="household",
            duration_str="15m",
            title="Chore Check",
        )
    )
    assert denied.status == "FAILURE"
    assert "admin" in denied.message.lower()

    # Admin creates a household alarm across multiple devices with emoji & sound
    created = await handle_timer(
        TimerRequest(
            user_context=admin_user,
            action="add",
            type="alarm",
            scope="household",
            duration_str="20m",
            title="Family Dinner",
            emoji="🍕",
            sound="classic_alarm",
            target_devices=["phone", "esphome:kitchen_display", "media_player.living_room"],
            flash_screen=True,
            esphome_script="flash_screen",
        )
    )
    assert created.status == "SUCCESS"
    assert "household" in created.message
    timer_id = created.detail["timer_id"]

    # Visible to both admin and non-admin family member
    kid_timers = await get_active_timers("kid_user")
    assert len(kid_timers) == 1
    assert kid_timers[0]["title"] == "Family Dinner"
    assert kid_timers[0]["scope"] == "household"
    assert kid_timers[0]["emoji"] == "🍕"
    assert kid_timers[0]["sound"] == "classic_alarm"
    assert kid_timers[0]["target_devices"] == ["phone", "esphome:kitchen_display", "media_player.living_room"]
    assert kid_timers[0]["flash_screen"] is True

    # Non-admin cannot delete a household alarm
    del_denied = await handle_timer(
        TimerRequest(user_context=non_admin, action="delete", id=timer_id, title="Family Dinner")
    )
    assert del_denied.status == "FAILURE"

    # Admin can reset and delete it
    reset_res = await handle_timer(
        TimerRequest(user_context=admin_user, action="reset", id=timer_id, title="Family Dinner")
    )
    assert reset_res.status == "SUCCESS"

    del_ok = await handle_timer(
        TimerRequest(user_context=admin_user, action="delete", id=timer_id, title="Family Dinner")
    )
    assert del_ok.status == "SUCCESS"
    assert await get_active_timers("kid_user") == []


@pytest.mark.asyncio
async def test_household_timer_notification_fans_out_to_family_and_sets_ring_phone(monkeypatch):
    from services.common import http, notify
    from services.execution.handlers.timer import notify_timer_done

    _FakeSession.calls, _FakeSession.fail = [], False
    monkeypatch.setattr(http, "get_client", _FakeSession)

    async def _fake_family():
        return ["jeremiah", "michele"]

    monkeypatch.setattr(notify, "family", _fake_family)

    ok = await notify_timer_done(
        "__household__",
        {
            "id": "fam-1",
            "title": "Eggs Ready",
            "type": "timer",
            "scope": "household",
            "created_by": "jeremiah",
            "emoji": "🥚",
            "sound": "egg_timer",
            "target_devices": ["phone", "esphome:kitchen_display"],
            "expires_at": "2026-10-18T18:00:00+00:00",
        },
    )
    assert ok is True
    assert len(_FakeSession.calls) == 2
    users_notified = [body["user"] for _, body in _FakeSession.calls]
    assert users_notified == ["jeremiah", "michele"]
    _, body = _FakeSession.calls[0]
    assert "🥚" in body["title"]
    assert body["data"]["ring_phone"] is True
    assert body["data"]["sound"] == "egg_timer"
    assert body["data"]["emoji"] == "🥚"


def test_synthesize_and_combine_alarm_wav_bytes():
    from services.execution.handlers.timer import combine_wav_bytes, get_rtttl_for_timer, synthesize_alarm_wav

    egg_wav = synthesize_alarm_wav(sound="egg_timer", emoji="🥚")
    assert egg_wav[:4] == b"RIFF"
    assert len(egg_wav) > 1000

    classic_wav = synthesize_alarm_wav(sound="classic_alarm")
    combined = combine_wav_bytes(egg_wav, classic_wav)
    assert combined[:4] == b"RIFF"
    assert len(combined) > len(egg_wav)

    rtttl = get_rtttl_for_timer(sound="egg_timer", emoji="🥚")
    assert "egg:" in rtttl

