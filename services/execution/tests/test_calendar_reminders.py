"""Calendar reminders: a set time before each event, all-day events at 8:00."""
from datetime import datetime, timedelta

import pytz

from services.execution import calendar_reminders as cr

TZ = pytz.timezone("America/Phoenix")


def at(hour, minute=0, day=7):
    return TZ.localize(datetime(2026, 10, day, hour, minute))


def ev(start, summary="Dentist", **kw):
    return {"summary": summary, "start_time": start.isoformat(), **kw}


def test_a_timed_event_reminds_inside_the_lead_and_not_before_or_after():
    now = at(13, 0)
    events = [
        ev(now + timedelta(minutes=25), id="a", location="12 Oak St"),
        ev(now + timedelta(minutes=45), summary="Later"),
        ev(now - timedelta(minutes=5), summary="Started"),
    ]
    (note,) = cr.due(events, now, 30)
    assert note["category"] == "calendar" and note["tag"] == "calendar:a"
    assert note["title"] == "Dentist at 1:25 pm"
    assert note["body"] == "In 25 min at 12 Oak St."
    assert note["once"] == f"calendar:a:{events[0]['start_time']}"


def test_a_longer_lead_and_off():
    now = at(13, 0)
    later = [ev(now + timedelta(minutes=90), id="b")]
    assert cr.due(later, now, 30) == []
    assert cr.due(later, now, 120)[0]["body"] == "In 1 h 30 min."
    assert cr.due([ev(now + timedelta(minutes=5))], now, 0) == []


def test_all_day_events_remind_once_each_morning_of_their_days():
    trip = ev(at(0, 0, day=7), summary="Camping", id="c", all_day=True, end_time=at(0, 0, day=9).isoformat())
    unflagged = ev(at(0, 0, day=7), summary="Holiday")  # local midnight counts as all-day
    morning = cr.due([trip, unflagged], at(8, 5), 30)
    assert [n["title"] for n in morning] == ["Today: Camping", "Today: Holiday"]
    assert morning[0]["once"] == "calendar:c:2026-10-07" and morning[0]["body"] == "All day."
    assert [n["title"] for n in cr.due([trip], at(8, 30, day=8), 30)] == ["Today: Camping"]
    assert cr.due([trip], at(8, 30, day=9), 30) == []  # the end date is exclusive
    assert cr.due([trip], at(7, 59), 30) == []
    assert cr.due([trip], at(10, 0), 30) == []  # too late in the day to call it a morning reminder


def test_settings_lead_and_timezone():
    assert cr.lead_minutes({}) == 30
    assert cr.lead_minutes({"reminder_minutes": 10}) == 10
    assert cr.lead_minutes({"reminder_minutes": 0}) == 0
    assert cr.lead_minutes({"reminder_minutes": "nonsense"}) == 30
    assert cr.lead_minutes({"reminder_minutes": 99999}) == cr.MAX_LEAD_MIN
    assert cr.person_tz({"timezone": "America/New_York"}).zone == "America/New_York"
    assert cr.person_tz({"timezone": "Mars/Base"}).zone == cr.person_tz({}).zone


async def test_a_round_reads_each_persons_events_in_their_zone_and_sends(monkeypatch):
    import services.execution.main as _main

    sent, reads = [], []
    now_utc = pytz.utc.localize(datetime(2026, 10, 7, 20, 0))  # 1 pm in Phoenix, 4 pm in New York

    async def fake_family(**_):
        return ["michele", "jeremiah"]

    async def fake_resolve(rag_user=None, **_):
        tz = "America/New_York" if rag_user == "jeremiah" else "America/Phoenix"
        return {"user": rag_user, "calendar_settings": {"timezone": tz}}

    async def fake_events(user, creds, lo, hi):
        reads.append((user, lo))
        return [ev(at(13, 20), id=f"{user}-1")]

    async def fake_send(payload, **_):
        sent.append(payload)
        return True

    from services.common import notify
    monkeypatch.setattr(notify, "family", fake_family)
    monkeypatch.setattr(notify, "send", fake_send)
    monkeypatch.setattr(_main, "resolve_internal_user", fake_resolve)
    monkeypatch.setattr(cr, "events_for", fake_events)

    assert await cr.remind_once(now_utc) == 2
    assert [p["user"] for p in sent] == ["michele", "jeremiah"]
    assert sent[0]["title"] == "Dentist at 1:20 pm"
    assert sent[1]["title"] == "Dentist at 4:20 pm"  # his clock, not the household's
    assert reads[1][1].startswith("2026-10-07T00:00:00-04:00")

