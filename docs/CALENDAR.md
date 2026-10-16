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
  Not handled: per-occurrence overrides (RECURRENCE-ID) — the series is shown.
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
  (voice/LLM tools) — it changes every event whose title contains the words.
  An edit that moves the time keeps the event's length; an untouched time is
  not sent (re-saving it turned all-day events into midnight appointments).
  A repeating Nextcloud event is edited or deleted as a whole series, and the
  dialog says so. iCal events open read-only.
- Sources: enable/disable, add or remove iCal feeds, map calendar accounts to
  people (colours and names).

## Background

`services/execution/leave_by.py` (every 5 minutes) reads each person's next
few hours and sends a "leave by" notice for events with a location — see
`docs/BACKGROUND_JOBS.md`. There are no other calendar reminders.

## Tests

- `services/execution/tests/test_calendar_handlers.py`,
  `test_calendar_window_and_uid.py` — merge, failures, noise calendars,
  window, recurrence, EXDATE, local time, edit/delete by UID, read-only iCal
- `services/gateway/tests/test_calendar_proxy.py` — window forwarding
- `services/ui/src/components/calendar/CalendarApp.test.tsx` — range per
  view, edit by id, read-only events
