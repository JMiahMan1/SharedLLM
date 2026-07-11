import types
from datetime import UTC, datetime

from services.execution.handlers.calendar import handle_calendar
from services.execution.schemas import CalendarRequest, UserContext


def _make_event(summary, dt):
    ve = types.SimpleNamespace(
        summary=types.SimpleNamespace(value=summary),
        dtstart=types.SimpleNamespace(value=dt),
    )
    return types.SimpleNamespace(vobject_instance=types.SimpleNamespace(vevent=ve))


def _make_fake_provider(calendars):
    # calendars: list of (url, name, events)
    class FakeProvider:
        def __init__(self, cals):
            self._cals = cals

        def calendar_client(self):
            dav = types.SimpleNamespace()
            principal = types.SimpleNamespace()
            principal.calendars = lambda: [
                types.SimpleNamespace(url=u, name=n) for u, n, _ in self._cals
            ]
            dav.principal = lambda: principal
            return dav

    return FakeProvider(calendars)


def _make_fake_caldav(calendars):
    # Map url -> events for the fake Calendar.search()
    cal_map = {u: evs for u, _, evs in calendars}

    class FakeCaldav:
        class Calendar:
            def __init__(self, client=None, url=None):
                self.url = url

            def search(self, *args, **kwargs):
                return list(cal_map.get(self.url, []))

    return FakeCaldav


async def test_calendar_read_merges_and_sorts_all_calendars():
    now = datetime.now(UTC)
    provider = _make_fake_provider([
        ("https://nc/calA", "Personal", [_make_event("Alpha", now)]),
        ("https://nc/calB", "Work", [_make_event("Beta", now)]),
    ])
    fake_caldav = _make_fake_caldav(provider._cals)

    import unittest.mock as mock

    with mock.patch(
        "services.execution.handlers.calendar.resolve_personal_data_provider",
        return_value=provider,
    ), mock.patch("services.execution.handlers.calendar.caldav", fake_caldav):
        result = await handle_calendar(
            CalendarRequest(user_context=UserContext(user="u1"), action="read")
        )

    assert result.status == "SUCCESS"
    assert "Upcoming Events:" in result.message
    assert "Alpha" in result.message
    assert "Beta" in result.message


async def test_calendar_read_survives_one_failing_calendar():
    now = datetime.now(UTC)

    class FailingCaldav:
        class Calendar:
            def __init__(self, client=None, url=None):
                self.url = url

            def search(self, *args, **kwargs):
                raise RuntimeError("caldav down")

    provider = _make_fake_provider([
        ("https://nc/calA", "Personal", [_make_event("Alpha", now)]),
        ("https://nc/calB", "Work", []),
    ])

    import unittest.mock as mock

    with mock.patch(
        "services.execution.handlers.calendar.resolve_personal_data_provider",
        return_value=provider,
    ), mock.patch("services.execution.handlers.calendar.caldav", FailingCaldav):
        result = await handle_calendar(
            CalendarRequest(user_context=UserContext(user="u1"), action="read")
        )

    assert result.status == "SUCCESS"
    assert "No events found." in result.message


async def test_calendar_read_skips_noise_calendars():
    now = datetime.now(UTC)
    provider = _make_fake_provider([
        ("https://nc/birthdays", "Birthdays", [_make_event("Should Skip", now)]),
        ("https://nc/personal", "Personal", [_make_event("Keep", now)]),
    ])
    fake_caldav = _make_fake_caldav(provider._cals)

    import unittest.mock as mock

    with mock.patch(
        "services.execution.handlers.calendar.resolve_personal_data_provider",
        return_value=provider,
    ), mock.patch("services.execution.handlers.calendar.caldav", fake_caldav):
        result = await handle_calendar(
            CalendarRequest(user_context=UserContext(user="u1"), action="read")
        )

    assert result.status == "SUCCESS"
    assert "Keep" in result.message
    assert "Should Skip" not in result.message
