"""Calendar reads follow the window on screen, repeat events, and edit by UID."""
import types
import unittest.mock as mock
from datetime import date, datetime, timedelta

import pytest
import vobject

from services.execution.handlers import calendar as cal_mod
from services.execution.handlers.calendar import handle_calendar
from services.execution.schemas import CalendarRequest, UserContext


@pytest.fixture(autouse=True)
def _phoenix(monkeypatch):
    # A real zone with a local-mean-time entry (-07:28) -- the trap a pytz
    # ``replace(tzinfo=...)`` falls into.
    monkeypatch.setattr(cal_mod, "LOCAL_TZ_NAME", "America/Phoenix")


def _vevent(ics_body: str):
    cal = vobject.readOne("BEGIN:VCALENDAR\r\nVERSION:2.0\r\n" + ics_body + "END:VCALENDAR\r\n")
    return cal.vevent


def test_all_day_dates_are_local_midnight_not_lmt():
    start = cal_mod._normalize_event_time(date(2026, 10, 16))
    assert start.isoformat() == "2026-10-16T00:00:00-07:00"


def test_localize_uses_real_offset():
    assert cal_mod._localize(datetime(2026, 10, 16, 14, 0)).isoformat() == "2026-10-16T14:00:00-07:00"


def test_weekly_event_is_expanded_into_the_window():
    ve = _vevent(
        "BEGIN:VEVENT\r\nUID:practice-1\r\nSUMMARY:Practice\r\n"
        "DTSTART:20260901T010000Z\r\nDTEND:20260901T020000Z\r\nRRULE:FREQ=WEEKLY\r\nEND:VEVENT\r\n"
    )
    lo = cal_mod._localize(datetime(2026, 10, 1))
    hi = cal_mod._localize(datetime(2026, 10, 22))
    occ = cal_mod._occurrences(ve, lo, hi)
    assert [s.date().isoformat() for s, _ in occ] == ["2026-10-05", "2026-10-12", "2026-10-19"]  # 01:00Z is 18:00 the evening before in Phoenix
    # Each occurrence keeps the event's hour-long length.
    assert all(e - s == timedelta(hours=1) for s, e in occ)


def test_exdate_is_skipped_and_all_day_repeats_stay_all_day():
    ve = _vevent(
        "BEGIN:VEVENT\r\nUID:trash\r\nSUMMARY:Trash day\r\n"
        "DTSTART;VALUE=DATE:20261005\r\nRRULE:FREQ=WEEKLY\r\nEXDATE;VALUE=DATE:20261012\r\nEND:VEVENT\r\n"
    )
    lo = cal_mod._localize(datetime(2026, 10, 1))
    hi = cal_mod._localize(datetime(2026, 10, 20))
    starts = [s.isoformat() for s, _ in cal_mod._occurrences(ve, lo, hi)]
    assert starts == ["2026-10-05T00:00:00-07:00", "2026-10-19T00:00:00-07:00"]


def test_read_window_honours_range_and_caps_it():
    req = CalendarRequest(user_context=UserContext(user="u"), action="read",
                          range_start="2026-10-01", range_end="2027-12-01")
    lo, hi = cal_mod._read_window(req)
    assert lo.isoformat() == "2026-10-01T00:00:00-07:00"
    assert hi - lo == timedelta(days=cal_mod.MAX_READ_DAYS)
    # Garbage falls back to the default week.
    lo2, hi2 = cal_mod._read_window(CalendarRequest(user_context=UserContext(user="u"), action="read",
                                                    range_start="nope", range_end="2026-10-01"))
    assert timedelta(days=7) < hi2 - lo2 <= timedelta(days=8, seconds=1)


class _FakeEvent:
    def __init__(self, ve):
        self.vobject_instance = types.SimpleNamespace(vevent=ve)
        self.saved = False
        self.deleted = False

    def save(self):
        self.saved = True

    def delete(self):
        self.deleted = True


def _provider_with(events_by_uid, search_events=()):
    class Cal:
        name = "Personal"

        def event_by_uid(self, uid):
            if uid not in events_by_uid:
                raise LookupError(uid)
            return events_by_uid[uid]

        def search(self, *a, **k):
            return list(search_events)

    principal = types.SimpleNamespace(calendars=lambda: [Cal()])
    client = types.SimpleNamespace(principal=lambda: principal)
    return types.SimpleNamespace(calendar_client=lambda: client)


async def _run(req, provider):
    with mock.patch.object(cal_mod, "resolve_personal_data_provider", return_value=provider), \
         mock.patch.object(cal_mod, "_resolve_calendar_integrations", mock.AsyncMock(return_value=[])):
        return await handle_calendar(req)


async def test_delete_by_uid_removes_only_that_event():
    target = _FakeEvent(_vevent("BEGIN:VEVENT\r\nUID:a\r\nSUMMARY:Dinner\r\nDTSTART:20261016T010000Z\r\nEND:VEVENT\r\n"))
    namesake = _FakeEvent(_vevent("BEGIN:VEVENT\r\nUID:b\r\nSUMMARY:Dinner with Mom\r\nDTSTART:20261017T010000Z\r\nEND:VEVENT\r\n"))
    provider = _provider_with({"a": target}, search_events=[target, namesake])
    res = await _run(CalendarRequest(user_context=UserContext(user="u"), action="delete",
                                     integration="nextcloud", event_id="a", query="Dinner"), provider)
    assert res.status == "SUCCESS"
    assert target.deleted and not namesake.deleted


async def test_update_by_uid_moves_event_and_keeps_length():
    ev = _FakeEvent(_vevent(
        "BEGIN:VEVENT\r\nUID:a\r\nSUMMARY:Dentist\r\nDTSTART:20261016T160000Z\r\nDTEND:20261016T173000Z\r\nEND:VEVENT\r\n"
    ))
    res = await _run(CalendarRequest(user_context=UserContext(user="u"), action="update", integration="nextcloud",
                                     event_id="a", summary="Dentist (moved)", start_time="2026-10-20 14:00"),
                     _provider_with({"a": ev}))
    assert res.status == "SUCCESS", res.message
    ve = ev.vobject_instance.vevent
    assert ev.saved
    assert ve.summary.value == "Dentist (moved)"
    assert ve.dtstart.value.isoformat() == "2026-10-20T14:00:00-07:00"
    assert ve.dtend.value - ve.dtstart.value == timedelta(minutes=90)


async def test_update_unknown_uid_fails_instead_of_matching_titles():
    res = await _run(CalendarRequest(user_context=UserContext(user="u"), action="update", integration="nextcloud",
                                     event_id="gone", summary="x"), _provider_with({}))
    assert res.status == "FAILURE"


async def test_ical_events_cannot_be_deleted():
    res = await _run(CalendarRequest(user_context=UserContext(user="u"), action="delete", integration="ical",
                                     query="anything"), _provider_with({}))
    assert res.status == "FAILURE"
    assert "read-only" in res.message
