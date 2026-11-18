"""Reply reminders in the parents' private chat (services/gateway/private_chat_reminders.py).

The server decides from metadata only: who sent the newest message and when,
the read markers, and one bit the reader's device may send (the message asks
something). These tests hold every anti-spam rule: one reminder per unanswered
message, at most two a day, none in quiet hours, none once the reader replied
or reminders are off, none for an old message, none for a chat that is gone or
locked; that the flag shortens the delay; that a pass is idempotent (the state
is on disk); and that the notice never carries content.
"""
import json
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from services.common import notify
from services.gateway import private_chat_reminders as reminders
from services.gateway.tests.test_private_chat import PEOPLE, _as, _create, _legacy, env  # noqa: F401  (env is a fixture)
from services.gateway.tests.test_private_chat_messages import _sealed

TOKEN = "room123abc"
BASE = f"/api/private-chat/chats/{TOKEN}"
TZ = ZoneInfo("America/Denver")
HOUR = 3600


def _local(day: int, hour: int, minute: int = 0) -> float:
    return datetime(2026, 11, day, hour, minute, tzinfo=TZ).timestamp()


#: Alex's message goes at 10:00 household time on the 18th.
SENT = _local(18, 10)


# --- The rules on their own -------------------------------------------------------


def _decide(state=None, *, latest=None, read_upto=1, now=SENT + 6 * HOUR + 60, recipient="sam", **kw):
    latest = latest if latest is not None else {"id": 1, "by": "alex", "at": SENT}
    base = reminders._empty()
    base["read_seen"] = {recipient: {"id": latest.get("id"), "at": int(SENT)}}
    for key, value in (state or {}).items():
        base[key] = value
    return reminders.decide(recipient=recipient, latest=latest, read_upto=read_upto, state=base, now=now, tz=TZ,
                            delay_s=6 * HOUR, question_delay_s=2 * HOUR,
                            quiet=reminders.parse_quiet("21:30-07:30"), daily_max=2, max_age_s=3 * 86400, **kw)


def test_due_once_the_delay_has_passed_and_not_before():
    assert _decide(now=SENT + 6 * HOUR - 60).reason == "waiting"
    decision = _decide()
    assert decision.due and not decision.question


def test_a_question_brings_it_forward():
    flagged = {"expects": {"sam": 1}}
    assert _decide(flagged, now=SENT + 2 * HOUR - 60).reason == "waiting"
    decision = _decide(flagged, now=SENT + 2 * HOUR + 60)
    assert decision.due and decision.question
    # Without the flag the same moment is still too soon.
    assert _decide(now=SENT + 2 * HOUR + 60).reason == "waiting"


def test_a_flag_for_an_older_message_does_not_count():
    assert _decide({"expects": {"sam": 0}}, now=SENT + 2 * HOUR + 60).reason == "waiting"


def test_nothing_while_it_is_unread_that_is_the_nudge():
    assert _decide(read_upto=0).reason == "unread"


def test_nothing_once_the_reader_replied():
    assert _decide(latest={"id": 2, "by": "sam", "at": SENT}, read_upto=2).reason == "replied"


def test_nothing_when_the_reader_turned_reminders_off():
    assert _decide({"enabled": {"sam": False}}).reason == "off"
    # The other member's choice is theirs only.
    assert _decide({"enabled": {"alex": False}}).due


def test_one_reminder_per_message():
    assert _decide({"reminded": {"sam": [1]}}).reason == "already"


def test_read_late_the_clock_starts_from_the_reading():
    late = {"read_seen": {"sam": {"id": 1, "at": int(SENT + 3 * HOUR)}}}
    assert _decide(late, now=SENT + 6 * HOUR + 60).reason == "waiting"
    assert _decide(late, now=SENT + 9 * HOUR - 60).reason == "waiting"
    assert _decide(late, now=SENT + 9 * HOUR + 60).due


def test_never_for_a_message_older_than_three_days():
    stale = {"read_seen": {"sam": {"id": 1, "at": int(SENT + 70 * HOUR)}}}
    assert _decide(stale, now=SENT + 76 * HOUR + 60).reason == "too_old"


@pytest.mark.parametrize("hour,minute,quiet", [
    (21, 30, True), (23, 0, True), (2, 0, True), (7, 29, True), (7, 30, False), (12, 0, False), (21, 29, False),
])
def test_quiet_hours_cross_midnight(hour, minute, quiet):
    rule = reminders.parse_quiet("21:30-07:30")
    assert reminders.in_quiet_hours(_local(19, hour, minute), TZ, rule) is quiet


def test_quiet_hours_hold_a_reminder_until_morning():
    late_evening = {"read_seen": {"sam": {"id": 1, "at": int(_local(18, 17))}}}
    assert _decide(late_evening, now=_local(18, 23)).reason == "quiet"
    assert _decide(late_evening, now=_local(19, 7, 31)).due


def test_quiet_hours_within_one_day_and_off():
    rule = reminders.parse_quiet("13:00-14:00")
    assert reminders.in_quiet_hours(_local(18, 13, 30), TZ, rule)
    assert not reminders.in_quiet_hours(_local(18, 14, 0), TZ, rule)
    assert reminders.in_quiet_hours(_local(18, 3), TZ, None) is False
    assert reminders.parse_quiet("off") is None


def test_at_most_two_a_day_per_person():
    today = [int(_local(18, 8)), int(_local(18, 9))]
    assert _decide({"sent": {"sam": today}}).reason == "daily_cap"
    yesterday = [int(_local(17, 12)), int(_local(17, 13))]
    assert _decide({"sent": {"sam": yesterday}}).due


def test_the_text_says_who_never_what():
    assert reminders.reminder_text("Alex", False) == "Alex is waiting to hear back from you"
    assert reminders.reminder_text("Alex", True) == "Alex asked you something and is waiting to hear back"


@pytest.mark.parametrize("raw", ["21:30", "25:00-07:00", "21:30-7", "nine-five", "07:30-07:30"])
def test_a_malformed_quiet_setting_is_an_error_not_a_guess(raw):
    with pytest.raises(ValueError, match="PRIVATE_CHAT_REMINDER_QUIET"):
        reminders.parse_quiet(raw)


def test_malformed_numbers_are_errors(monkeypatch):
    monkeypatch.setenv("PRIVATE_CHAT_REMINDER_DELAY_S", "soon")
    with pytest.raises(ValueError, match="PRIVATE_CHAT_REMINDER_DELAY_S"):
        reminders._env_int("PRIVATE_CHAT_REMINDER_DELAY_S", 1, low=1, high=10)
    monkeypatch.setenv("PRIVATE_CHAT_REMINDER_DELAY_S", "99")
    with pytest.raises(ValueError, match="out of range"):
        reminders._env_int("PRIVATE_CHAT_REMINDER_DELAY_S", 1, low=1, high=10)
    monkeypatch.setenv("PRIVATE_CHAT_REMINDERS", "maybe")
    with pytest.raises(ValueError, match="PRIVATE_CHAT_REMINDERS"):
        reminders._env_flag("PRIVATE_CHAT_REMINDERS", True)


# --- A whole pass, against the real stores ----------------------------------------


class _Resp:
    def __init__(self, body, status=200):
        self.status = status
        self._body = body

    async def json(self, content_type=None):
        return self._body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _Identity:
    def __init__(self, people):
        self.people = people

    def get(self, url, **kw):
        assert url.endswith("/api/users")
        return _Resp(list(self.people.values()))


@pytest.fixture
def chat(env, monkeypatch):  # noqa: F811
    assert _create(env).status_code == 200
    sent: list[dict] = []
    env["deliver"] = True

    async def send(payload, **kw):
        if not env["deliver"]:
            return False
        sent.append(payload)
        return True

    monkeypatch.setattr(notify, "send", send)
    monkeypatch.setattr(reminders, "_household_tz", lambda: TZ)
    monkeypatch.setattr(reminders, "ENABLED", True)
    monkeypatch.setattr(reminders, "DELAY_S", 6 * HOUR)
    monkeypatch.setattr(reminders, "QUESTION_DELAY_S", 2 * HOUR)
    monkeypatch.setattr(reminders, "QUIET", reminders.parse_quiet("21:30-07:30"))
    monkeypatch.setattr(reminders, "DAILY_MAX", 2)
    monkeypatch.setattr(reminders, "MAX_AGE_S", 3 * 86400)
    env["sent"] = sent
    env["people"] = dict(PEOPLE)
    return env


def _message(ctx, user: str, at: float) -> int:
    resp = ctx["client"].post(f"{BASE}/messages", json={"sealed": _sealed()}, headers=_as(user))
    assert resp.status_code == 200, resp.text
    mid = resp.json()["detail"]["message"]["id"]
    # Backdate it to the test's clock.
    path = ctx["root"] / "messages" / TOKEN / "m" / f"{mid}.json"
    record = json.loads(path.read_text())
    record["at"] = int(at)
    path.write_text(json.dumps(record))
    return mid


def _read(ctx, user: str) -> None:
    assert ctx["client"].post(f"{BASE}/read", json={}, headers=_as(user)).status_code == 200


def _pass(ctx, now: float) -> int:
    import asyncio

    return asyncio.run(reminders.check_once(_Identity(ctx["people"]), identity="http://identity", telemetry="http://t",
                                            secret="s", now=now))


def test_read_and_left_gets_one_gentle_reminder(chat):
    _message(chat, "alex", SENT)
    _read(chat, "sam")
    assert _pass(chat, SENT + HOUR) == 0  # first seen read; the clock is running
    assert _pass(chat, SENT + 7 * HOUR) == 1
    (notice,) = chat["sent"]
    assert notice["user"] == "sam"
    assert notice["title"] == "Private chat"
    assert notice["body"] == "Alex is waiting to hear back from you"
    # The nudge's channel and sound, a little lower, replacing an older reminder.
    assert notice["category"] == "nudge" and notice["kind"] == "nudge" and notice["priority"] == 3
    assert notice["tag"] == f"private-reminder:{TOKEN}" and notice["replace"] is True
    assert notice["data"]["url"] == f"/family?chat={TOKEN}" and notice["data"]["reminder"] is True
    text = json.dumps(notice)
    assert "jarvis-envelope" not in text and "sealed" not in text
    # Idempotent: every later pass, and a restart (the state is on disk), sends nothing more.
    assert _pass(chat, SENT + 8 * HOUR) == 0
    assert _pass(chat, SENT + 30 * HOUR) == 0
    assert len(chat["sent"]) == 1
    saved = json.loads((chat["root"] / "reminders" / f"{TOKEN}.json").read_text())
    assert saved["reminded"] == {"sam": [1]}
    assert set(saved) == {"enabled", "expects", "read_seen", "reminded", "sent"}


def test_the_sender_is_never_reminded_and_unread_is_left_to_the_nudge(chat):
    _message(chat, "alex", SENT)
    assert _pass(chat, SENT + HOUR) == 0
    assert _pass(chat, SENT + 10 * HOUR) == 0  # Sam never read it
    assert chat["sent"] == []


def test_a_reply_ends_it(chat):
    _message(chat, "alex", SENT)
    _read(chat, "sam")
    _pass(chat, SENT + HOUR)
    _message(chat, "sam", SENT + 2 * HOUR)
    assert _pass(chat, SENT + 3 * HOUR) == 0
    # Alex has read Sam's reply: Alex is the one who might be reminded now, about Sam's message.
    _read(chat, "alex")
    _pass(chat, SENT + 3 * HOUR)
    assert _pass(chat, SENT + 8 * HOUR) == 0
    assert _pass(chat, SENT + 9 * HOUR + 60) == 1
    assert chat["sent"][0]["user"] == "alex" and chat["sent"][0]["body"] == "Sam is waiting to hear back from you"


def test_a_newer_message_restarts_the_clock_and_two_a_day_is_the_most(chat):
    _message(chat, "alex", _local(18, 8))
    _read(chat, "sam")
    _pass(chat, _local(18, 8, 5))
    assert _pass(chat, _local(18, 14, 10)) == 1
    _message(chat, "alex", _local(18, 14, 30))
    _read(chat, "sam")
    _pass(chat, _local(18, 14, 35))
    assert _pass(chat, _local(18, 20, 40)) == 1
    _message(chat, "alex", _local(18, 20, 45))
    _read(chat, "sam")
    _pass(chat, _local(18, 20, 50))
    assert _pass(chat, _local(19, 2)) == 0  # quiet hours
    # A new day: allowed again, after quiet hours end.
    assert _pass(chat, _local(19, 7, 35)) == 1
    assert len(chat["sent"]) == 3


def test_the_daily_cap_counts_per_person(chat):
    state = reminders._empty()
    state["sent"] = {"sam": [int(_local(18, 8)), int(_local(18, 9))]}
    reminders._save(TOKEN, state)
    _message(chat, "alex", _local(18, 10))
    _read(chat, "sam")
    _pass(chat, _local(18, 10, 5))
    assert _pass(chat, _local(18, 16, 10)) == 0
    assert chat["sent"] == []


def test_the_flag_from_the_readers_device_brings_it_forward(chat):
    mid = _message(chat, "alex", SENT)
    _read(chat, "sam")
    resp = chat["client"].post(f"{BASE}/expects-reply", json={"message_id": mid, "expects_reply": True}, headers=_as("sam"))
    assert resp.status_code == 200, resp.text
    _pass(chat, SENT + 5 * 60)
    assert _pass(chat, SENT + 2 * HOUR + 10 * 60) == 1
    assert chat["sent"][0]["body"] == "Alex asked you something and is waiting to hear back"


def test_the_flag_is_only_for_the_partners_newest_message(chat):
    first = _message(chat, "alex", SENT)
    second = _message(chat, "alex", SENT + 60)
    c = chat["client"]
    flag = {"expects_reply": True}
    assert c.post(f"{BASE}/expects-reply", json={"message_id": first, **flag}, headers=_as("sam")).status_code == 409
    assert c.post(f"{BASE}/expects-reply", json={"message_id": second, **flag}, headers=_as("alex")).status_code == 409
    for bad in ({"message_id": "2", **flag}, {"message_id": second, "expects_reply": "yes"}, {"message_id": True, **flag}):
        assert c.post(f"{BASE}/expects-reply", json=bad, headers=_as("sam")).status_code == 422
    assert c.post(f"{BASE}/expects-reply", json={"message_id": second, **flag}, headers=_as("kid")).status_code == 404
    assert c.post(f"{BASE}/expects-reply", json={"message_id": second, **flag}, headers=_as("sam")).status_code == 200
    # Only the bit and the id are kept.
    saved = json.loads((chat["root"] / "reminders" / f"{TOKEN}.json").read_text())
    assert saved["expects"] == {"sam": second}
    assert c.post(f"{BASE}/expects-reply", json={"message_id": second, "expects_reply": False},
                  headers=_as("sam")).status_code == 200
    assert json.loads((chat["root"] / "reminders" / f"{TOKEN}.json").read_text())["expects"] == {}


def test_each_person_can_turn_their_own_reminders_off(chat):
    c = chat["client"]
    got = c.get(f"{BASE}/reminders", headers=_as("sam")).json()
    assert got["enabled"] is True and got["delay_s"] == 6 * HOUR and got["question_delay_s"] == 2 * HOUR
    assert got["quiet"] == "21:30-07:30" and got["daily_max"] == 2
    assert c.put(f"{BASE}/reminders", json={"enabled": False}, headers=_as("sam")).json()["enabled"] is False
    assert c.get(f"{BASE}/reminders", headers=_as("sam")).json()["enabled"] is False
    assert c.get(f"{BASE}/reminders", headers=_as("alex")).json()["enabled"] is True
    assert c.put(f"{BASE}/reminders", json={"enabled": "no"}, headers=_as("sam")).status_code == 422
    assert c.put(f"{BASE}/reminders", json={"enabled": True}, headers=_as("kid")).status_code == 404
    assert c.get(f"{BASE}/reminders", headers=_as("gran")).status_code == 404
    _message(chat, "alex", SENT)
    _read(chat, "sam")
    _pass(chat, SENT + HOUR)
    assert _pass(chat, SENT + 7 * HOUR) == 0
    assert c.put(f"{BASE}/reminders", json={"enabled": True}, headers=_as("sam")).json()["enabled"] is True
    assert _pass(chat, SENT + 7 * HOUR) == 1


def test_a_failed_delivery_is_tried_again(chat):
    _message(chat, "alex", SENT)
    _read(chat, "sam")
    _pass(chat, SENT + HOUR)
    chat["deliver"] = False
    assert _pass(chat, SENT + 7 * HOUR) == 0
    chat["deliver"] = True
    assert _pass(chat, SENT + 7 * HOUR + 300) == 1
    saved = json.loads((chat["root"] / "reminders" / f"{TOKEN}.json").read_text())
    assert len(saved["sent"]["sam"]) == 1


def test_nothing_for_a_deleted_chat_and_its_state_goes_with_it(chat):
    _message(chat, "alex", SENT)
    _read(chat, "sam")
    _pass(chat, SENT + HOUR)
    assert (chat["root"] / "reminders" / f"{TOKEN}.json").exists()
    assert chat["client"].delete(BASE, headers=_as("alex")).status_code == 200
    assert not (chat["root"] / "reminders" / f"{TOKEN}.json").exists()
    assert _pass(chat, SENT + 7 * HOUR) == 0


def test_nothing_when_a_member_is_no_longer_a_parent(chat):
    _message(chat, "alex", SENT)
    _read(chat, "sam")
    _pass(chat, SENT + HOUR)
    chat["people"]["sam"] = {**PEOPLE["sam"], "family_role": "other"}
    assert _pass(chat, SENT + 7 * HOUR) == 0


def test_nothing_for_a_chat_still_on_talk(env, monkeypatch):  # noqa: F811
    _legacy(env)
    monkeypatch.setattr(reminders, "_household_tz", lambda: TZ)
    assert _pass({**env, "people": dict(PEOPLE)}, SENT + 7 * HOUR) == 0
    assert env["notices"] == []


def test_nothing_when_switched_off_or_without_a_time_zone(chat, monkeypatch):
    _message(chat, "alex", SENT)
    _read(chat, "sam")
    _pass(chat, SENT + HOUR)
    monkeypatch.setattr(reminders, "ENABLED", False)
    assert _pass(chat, SENT + 7 * HOUR) == 0
    monkeypatch.setattr(reminders, "ENABLED", True)
    monkeypatch.setattr(reminders, "_household_tz", lambda: None)
    assert _pass(chat, SENT + 7 * HOUR) == 0
    assert chat["sent"] == []


def test_the_server_never_learns_more_than_metadata(chat):
    mid = _message(chat, "alex", SENT)
    _read(chat, "sam")
    chat["client"].post(f"{BASE}/expects-reply", json={"message_id": mid, "expects_reply": True}, headers=_as("sam"))
    _pass(chat, SENT + HOUR)
    _pass(chat, SENT + 3 * HOUR)
    raw = (chat["root"] / "reminders" / f"{TOKEN}.json").read_text()
    assert "jarvis-envelope" not in raw and "ct" not in json.loads(raw)
    # Exactly: settings, the flagged id, when it was first seen read, which ids were reminded, when.
    saved = json.loads(raw)
    assert saved["expects"] == {"sam": mid}
    assert saved["read_seen"]["sam"]["id"] == mid
    assert saved["reminded"] == {"sam": [mid]}

