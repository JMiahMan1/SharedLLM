# Calendar Integration Plan — Multi-Source Agenda (Nextcloud + Skylight + iCal)

## 1. Problem & Goal

**Bug (root cause of `ECONNABORTED` on `GET /api/communication/calendar/events`):**

The calendar `read` handler in `services/execution/handlers/calendar.py` does a
Nextcloud caldav `principal().calendars()` (≤10s) **and then** searches every
per-person sub-calendar concurrently (≤8s). Worst case ≈ 18s, which exceeds
the UI `apiClient` 15s timeout → axios aborts with `ECONNABORTED` and retries
3×. Compounding it, the caldav transport (niquests) negotiates **HTTP/3 via
the Nextcloud `Alt-Svc` header** it can't use, so *every* request pays a
`MustDowngradeError` retry (observed ~9s of warnings across 8 calendars).

**Goal (your direction):**
- One calendar endpoint that aggregates **multiple integrations**: Nextcloud (caldav),
  Skylight (private API), and iCal (`.ics` subscription).
- Runtime-derived config with **never-hardcoded defaults**: when only one calendar
  integration is enabled it is the automatic default; when a competing integration
  is added the user is **prompted** to pick a default and can change it later.
- Ability to **filter / switch / disable / prioritize** integrations.
- Front-end: a full **Calendar App** modeled on the Skylight / `lowerrygt/OpenSkyLight`
  look & feel, plus a merged agenda (with filter chips) and a per-integration view.

---

## 2. Architecture

```
UI (Calendar App + UpcomingEventsWidget)
        │  GET/POST /api/communication/calendar/{events,calendars,default}
        ▼
Gateway  (/api/communication/calendar/*)  ── _proxy_execution_with_identity ─▶
        ▼
Execution  /execute/calendar  (handlers/calendar.py)
        │  Integration Registry (runtime-derived)
        ├── nextcloud  → caldav (fixed: http3 off, 12s cap, concurrent)
        ├── skylight  → internal _get_skylight_session + _skylight_request
        │               (server-to-server, no new gateway route needed)
        └── ical      → fetch + parse .ics URL (read-only)
```

No new gateway routes are required: Skylight and iCal are fetched **inside**
execution, exactly like Nextcloud already is. This also keeps the 15s UI timeout
easy to honor (one hop, one hard cap).

---

## 3. Backend Design

### 3.1 Integration Registry (runtime-derived, never hardcoded)

A single `_resolve_calendar_integrations(user_context) -> list[Integration]` builds
the live list on every request:

| field            | derived from |
|-----------------|-------------|
| `type`          | `nextcloud` \| `skylight` \| `ical` |
| `enabled`       | nextcloud → personal_data provider configured; skylight → `_get_skylight_session(user)` succeeds **and** `skylight_enabled`; ical → ≥1 `.ics` URL configured |
| `provides_calendar` | all true here |
| `writable`      | nextcloud ✓, skylight ✓, ical ✗ (read-only) |
| `priority`       | from user prefs (`priority` map) else insertion order |
| `is_default`     | see 3.2 |

Config lives in a **per-user calendar settings blob** (see 3.5) — nothing is
hardcoded in code.

### 3.2 Default selection (NEVER hardcoded)

```
if user_prefs.default set and that integration is enabled:
    default = user_prefs.default
elif exactly one enabled calendar integration:
    default = that one                      # automatic, no prompt
else:  # multiple enabled, no stored choice
    default = first by priority
    needs_default_choice = true             # UI prompts
```

When a **new** calendar-providing integration becomes enabled (e.g. Skylight added
while Nextcloud was the only one), if no explicit default is set the next calendar
open sets `needs_default_choice=true` and the UI shows the "pick a default" prompt.

### 3.3 Endpoints (all under existing `/api/communication/calendar/*`)

- **`GET .../calendars`** (`action: list`) → returns
  `{ calendars:[nextcloud subs], integrations:[{type,enabled,writable,is_default,priority}], needs_default_choice, available_defaults }`.
- **`GET .../events`** (`action: read`) → params `integration` (`nextcloud|skylight|ical|all`,
  default `all`), `calendar_name` (legacy Nextcloud sub-calendar), optional `window`
  (days past/future). Aggregates enabled integrations (or just the filtered one),
  merges, sorts by start, returns **structured** `events`:
  `[{ integration, summary, start_time(ISO-8601), end_time?, location?, calendar? }]`.
  Wrapped in a **hard `asyncio.wait_for(timeout=12)`** so it can never exceed the
  15s UI timeout → eliminates `ECONNABORTED`. On timeout, returns what was
  gathered so far.
- **`POST .../events`** (`action: add`) → targets `integration` param or the
  resolved default; routes to Skylight create / Nextcloud caldav add; rejects for
  read-only iCal with a clear message.
- **`POST .../default`** (new) → sets the user's `calendar_default_integration`
  preference (drives 3.2). Replaces the implicit default.

### 3.4 Per-integration handlers (in `handlers/calendar.py`, or a new `handlers/calendar_integrations.py`)

- **nextcloud** (existing, FIXED):
  - Disable HTTP/3 on the caldav session — `client.session = niquests.Session(disable_http3=True)`
    (auth is applied per-request via `self.auth`, so replacing the session is safe).
    This removes the `MustDowngradeError` retry storm.
  - Lower/parallelize: run calendar enumeration and event search concurrently;
    cap sub-calendars searched; per-call `wait_for` timeouts ~5–8s.
  - Overall `read` wrapped in the 12s cap (3.3).
- **skylight** (new): lazily `from services.execution.main import _get_skylight_session, _skylight_request`
  (function-level import avoids a circular import; `main` already imports the handler).
  - read: `_skylight_request(session,"GET","/calendar_events", params={date_min,date_max,timezone})`;
    normalize each `attributes.{summary, starts_at, ends_at, location, timezone}` → event.
  - add: `_skylight_request(session,"POST","/calendar_events", body)`.
- **ical** (new, read-only): fetch each configured `.ics` URL, parse with
  `icalendar`, expand recurring instances into the window, normalize → event.

Normalization target shape (consumed by `UpcomingEventsWidget.parseEvent`):
`{ summary:str, start_time:ISO-8601, end_time?:ISO-8601, location?:str }`.

### 3.5 Where settings persist (NEVER hardcoded)

A per-user **calendar settings blob** in the Identity service user record, e.g.
`user.settings.calendar = { default: "nextcloud"|"skylight"|"ical"|null,
disabled: [...], priority: { nextcloud:0, skylight:1, ical:2 } }`.
Read by execution through `user_context`; written via the new `/default` endpoint and
enable/disable/priority controls. (Assumption — confirm Identity can store an
arbitrary `settings` JSON per user; fallback is a local JSON store in `storage`.)

---

## 4. Frontend Design

Reference visuals: **Skylight** family calendar + **`lowerrygt/OpenSkyLight`**
(GitHub) for look & feel — clean card-based agenda, soft gradients, large rounded
tiles, family-friendly. (Clean-room the visual language; check OpenSkyLight license
before reusing any code.)

### 4.1 Integration switcher (chips / dropdown)
- "All" (merged) + one chip per enabled integration.
- Per chip: enable/disable toggle, "Set as default" action, drag/▾ priority.
- When `needs_default_choice` → modal prompting the user to pick the default.

### 4.2 Agenda views
- **Merged** (default): single feed of all enabled integrations, each event tagged
  with its integration color; sort by start; respects default/priority for tie-breaks.
- **Per-integration**: selecting a single chip isolates that integration's feed
  (your "both 1 & 2" choice).
- Filter chips to isolate one integration from the merged view.

### 4.3 Add-event
- Integration picker defaulting to the user's default (overridable per event).
- Skylight/Nextcloud targets writable; iCal shows read-only notice.

### 4.4 UpcomingEventsWidget
- Already supports structured `events`; point it at the merged `events` response.
- Add a small integration color-dot so merged sources are distinguishable.

### 4.5 New Calendar App page
- Route in `Communication.tsx` (or a dedicated `/calendar` page) hosting the
  switcher + agenda + add modal, styled per OpenSkyLight.

---

## 5. Verification

- **Backend**: extend `test_skylight_proxy.py` and add `test_calendar_integrations.py`
  mocking the Nextcloud provider, `_get_skylight_session`, and an `.ics` fixture;
  assert merged ordering, default selection, `add` routing, and the 12s cap.
  Lint/typecheck: `py_compile` + `ruff --select E9,F63,F7,F82` + pytest.
- **Live (remote)**: `curl` the gateway `/api/communication/calendar/events`
  and confirm (a) response < 15s (no `ECONNABORTED`), (b) Skylight events
  merged with Nextcloud, (c) `?integration=skylight` isolates, (d) `add` routes
  to the default and to an explicit integration.
- **Frontend**: `eslint .` + `tsc --noEmit`; optional Playwright smoke of the
  switcher + agenda.

---

## 6. Open Items / Assumptions (please confirm)

1. **Persistence location** for the calendar settings blob — assumed Identity user
   `settings.calendar`; confirm or pick the `storage` JSON fallback.
2. **iCal** is read-only; `.ics` URLs are per-user config (proposed in the same
   settings blob). Confirm where the URL(s) come from.
3. **Priority semantics**: used for default selection + label order; merged list is
   still primarily sorted by event start time. Confirm if priority should instead
   win time conflicts.
4. **OpenSkyLight license** — verify permissive before borrowing visual code;
   otherwise clean-room the look & feel only.
5. **Frameo** dropped (photo frame, no calendar API). Re-add later only if a real
   calendar source is identified.
6. Implementation order: (a) fix `ECONNABORTED` + Skylight aggregation in
   `read`/`add` (unblocks the widget), (b) settings blob + default/prompt logic,
   (c) iCal, (d) full Calendar App UI.
