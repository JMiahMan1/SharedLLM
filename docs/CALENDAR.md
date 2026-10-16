# Calendar

One agenda over every family calendar: Nextcloud (CalDAV), Skylight and iCal
subscriptions. Route `/calendar` (`services/ui/src/pages/Calendar.tsx` →
`components/calendar/CalendarApp.tsx`); the dashboard `UpcomingEventsWidget`
reads the same endpoint.

## Path

| Piece | Where |
|---|---|
| Events | `GET/POST/PUT/DELETE /api/communication/calendar/events` (gateway) → `POST /execute/calendar` |
| Handler | `services/execution/handlers/calendar.py` |
| Settings (default, disabled sources, iCal URLs, people) | `GET/PUT /api/calendar/settings` → identity |

## Reading

- `GET …/events?start=YYYY-MM-DD&end=YYYY-MM-DD` loads that window (capped at
  120 days). The page asks for exactly what is on screen: agenda (60 days),
  day, week, or the 42-day month grid. Without `start`/`end` a read is
  yesterday to +7 days (widget, voice, leave-by reminders).
- Nextcloud: every VEVENT in the user's calendars (not birthday/contact/holiday
  calendars). Repeating events (RRULE/RDATE minus EXDATE) are expanded into the
  window, one entry per occurrence, keeping the event's length. Each entry
  carries the event's UID as `id`, plus `all_day` and `recurring`.
  Not handled: per-occurrence overrides (RECURRENCE-ID); the series is shown.
- Skylight: its own API, already expanded; `id` is the Skylight event id.
- iCal: fetched and parsed per read; read-only.
- Times are made aware with pytz `localize` in `TIMEZONE` (never
  `replace(tzinfo=…)`, which used local mean time and put events added from
  the app 28 minutes late in America/Phoenix).

Each source reports `available`/`soft` so the page can say "temporarily
unreachable" and poll every 30 s instead of every 5 minutes.

## Changing events

- **Add**: title + free-text time ("tomorrow at 2pm"), into the chosen or
  default writable source; one hour long.
- **Edit / delete** act on the event's `id` (Nextcloud UID or Skylight id).
  The old title-substring match is kept only for callers without an id
  (voice/LLM tools), and it changes every event whose title contains the words.
  An edit that moves the time keeps the event's length; an untouched time is
  not sent (re-saving it turned all-day events into midnight appointments).
  A repeating Nextcloud event is edited or deleted as a whole series, and the
  dialog says so. iCal events open read-only.
- Sources: enable/disable, add or remove iCal feeds, map calendar accounts to
  people (colours and names), and phone reminders (off or 10 to 120 minutes
  before; saving also records this device's time zone).

## Background

`services/execution/leave_by.py` (every 5 minutes) reads each person's next
few hours and sends a "leave by" notice for events with a location (see
`docs/BACKGROUND_JOBS.md`).

`services/execution/calendar_reminders.py` (every 2 minutes) sends a
`calendar` phone notice before each of a person's own events, using the same
reading and the same "whose event is this" rule as leave-by
(`leave_by.events_for`, `belongs_to`): a household event with nobody's name
on it reminds nobody. Per-person calendar settings
(`PUT /api/calendar/settings`):

| Key | Meaning |
|---|---|
| `reminder_minutes` | Minutes before an event the notice goes out. Unset = 30, 0 = off, at most 1440. |
| `timezone` | IANA zone the reminder's clock times and the 8:00 morning use. Unset = the household zone (`timezone` setting). |

A timed event reminds once, when its start is within `reminder_minutes`
(title "Dentist at 1:25 pm", body "In 25 min at 12 Oak St."). An all-day
event (flagged, or starting at local midnight) reminds at 8:00 on each day it
covers, until 10:00; later than that the day's notice is skipped. Telemetry's
`once` key (`calendar:<event id>:<start>`, or `:<date>` for all-day) keeps a
reminder from repeating across rounds and restarts.

## Tests

- `services/execution/tests/test_calendar_handlers.py`,
  `test_calendar_window_and_uid.py`: merge, failures, noise calendars,
  window, recurrence, EXDATE, local time, edit/delete by UID, read-only iCal
- `services/execution/tests/test_calendar_reminders.py`: lead, off, all-day
  mornings, per-person zone
- `services/identity/tests/test_calendar_reminder_settings.py`: the reminder
  keys are stored and validated
- `services/gateway/tests/test_calendar_proxy.py`: window forwarding
- `services/ui/src/components/calendar/CalendarApp.test.tsx`: range per
  view, edit by id, read-only events, reminder setting
