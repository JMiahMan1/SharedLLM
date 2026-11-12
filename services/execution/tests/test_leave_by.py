"""Leave-by reminders from calendar events with a location and the drive time."""
from datetime import datetime, timedelta

import pytz

from services.execution import leave_by

TZ = pytz.timezone("Etc/GMT+7")
NOW = TZ.localize(datetime(2026, 10, 7, 13, 0)).timestamp()


def ev(hours, minutes=0, location="12 Oak St, Springfield", summary="Dentist", **kw):
    start = TZ.localize(datetime(2026, 10, 7, 13, 0)) + timedelta(hours=hours, minutes=minutes)
    return {"summary": summary, "start_time": start.isoformat(), "location": location, **kw}


def test_candidates_need_a_location_a_time_and_to_be_soon():
    events = [
        ev(1),
        ev(1, location=""),                         # nowhere to drive to
        ev(-1),                                     # already started
        ev(6),                                      # too far out
        {"summary": "Holiday", "start_time": TZ.localize(datetime(2026, 10, 7, 0, 0)).isoformat(), "location": "Beach"},
        {"summary": "Bad", "start_time": "soon", "location": "x"},
    ]
    assert [e["summary"] for e in leave_by.candidates(events, NOW)] == ["Dentist"]


def test_leave_time_is_start_minus_drive_minus_buffer():
    e = ev(1)
    assert leave_by.leave_at(e, {"duration_s": 1320}) == NOW + 3600 - 1320 - leave_by.BUFFER_S
    assert leave_by.leave_at(e, {"arrived": True}) is None
    assert leave_by.leave_at(e, {"duration_s": 1320, "moving": True}) is None


def test_reminder_text_and_once_key():
    e = ev(1, id="abc")
    leave_ts = leave_by.leave_at(e, {"duration_s": 1320})
    note = leave_by.reminder(e, {"duration_s": 1320}, leave_ts, NOW, TZ)
    assert note["title"] == "Leave by 1:28 pm for Dentist"
    assert note["body"] == "22 min drive to 12 Oak St, Springfield; starts at 2 pm."
    assert note["once"].startswith("leave_by:abc:")
    assert note["category"] == "leave_by" and note["tag"] == "leave_by:abc"
    late = leave_by.reminder(e, {"duration_s": 4000}, NOW - 60, NOW, TZ)
    assert late["title"] == "Leave now for Dentist" and late["body"].startswith("1 h 07 min drive")


def test_only_your_own_events_remind_you():
    shared = {"credential_sources": {"skylight": "shared", "nextcloud": "absent"}}
    mine = ev(1, integration="skylight", person="Sam")
    robins = ev(1, integration="skylight", person="Robin", calendar="robin@example.com")
    household = ev(1, integration="skylight", person="The Rivera")
    assert leave_by.belongs_to(mine, "sam", shared)
    assert not leave_by.belongs_to(robins, "sam", shared)
    assert not leave_by.belongs_to(household, "sam", shared)
    # A calendar account mapped to you in calendar settings
    mapped = {**shared, "calendar_settings": {"people": [{"name": "Sam", "accounts": ["robin@example.com"]}]}}
    assert leave_by.belongs_to(robins, "sam", mapped)
    # Your own Nextcloud is yours; a borrowed one is not
    nc = ev(1, integration="nextcloud")
    assert leave_by.belongs_to(nc, "alex", {"credential_sources": {"nextcloud": "own"}})
    assert not leave_by.belongs_to(nc, "sam", {"credential_sources": {"nextcloud": "granted"}})


def test_flagged_all_day_events_and_calendar_coordinates():
    allday = ev(1, all_day=True)
    assert leave_by.candidates([allday], NOW) == []
    assert leave_by.all_day(allday) and leave_by.all_day(ev(-13)) and not leave_by.all_day(ev(1))
    assert leave_by.destination(ev(1, lat=39.74, lon=-104.99)) == "39.740000,-104.990000"
    assert leave_by.destination(ev(1)) == "12 Oak St, Springfield"
