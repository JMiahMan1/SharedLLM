# Health & Steps

## Capture chain

1. **Native pedometer** — `StepCounterPlugin.java` (Android `TYPE_STEP_COUNTER`
   sensor, `ACTIVITY_RECOGNITION` permission) tracks steps since midnight,
   handling midnight rollover and reboots. Exposed to JS by
   `services/ui/src/plugins/stepCounter.ts` (`isAvailable`, `getTodaySteps`,
   `startPolling`, `stepUpdate`). On web the plugin reports unavailable and
   never fabricates a count.
2. **Client sync** — `context/LocationContext.tsx` posts
   `POST /api/geo/steps` (`{user_id, steps, timestamp}`) on a single 30 s
   cadence (`ensureStepSyncTimer`). Daily steps are also piggybacked on
   location updates, and a midnight timer re-baselines the count.
3. **Geo storage** — `services/geo/main.py` writes a Redis hash
   `geo:steps:{user}` (field = local day, value = max seen), with a 30-day
   read window and `America/Phoenix` day boundaries.

## Endpoints

| Endpoint | Purpose |
|---|---|
| `POST /api/geo/steps` | ingest a pedometer reading (validated 0–200000) |
| `GET /api/geo/steps?days=N` | `{daily_steps, today, goal}` |
| `GET /api/geo/steps/goal` | per-user daily goal |
| `PUT /api/geo/steps/goal` | set the goal (1000–100000) |
| `GET/POST /api/geo/workouts`, `GET /workouts/{id}/route` | workout sessions and routes |
| `GET /api/geo/activity/summary?window=today\|week\|month` | your own activity totals for a window |
| `GET /api/geo/activity/feed?window=…` | opt-in activity of people who share with you |
| `GET/PUT /api/users/me/activity-sharing` | private-by-default sharing settings (audience + scopes) |

The goal is stored as `geo:steps_goal:{user}`; the default remains 10,000.

## Surfaces

| Surface | File | Notes |
|---|---|---|
| **Health page** | `pages/Health.tsx` | ring + 7-day bars, goal editor, achievements, opt-in AI trends, workout start/stop + history. Reached at `/fitness` (sidebar + mobile bottom bar) — `/health` is reserved for service health checks. |
| Wander | `pages/Wander.tsx` | family presence, places and trips only — steps/workouts moved to Health |
| Dashboard Health widget | `components/widgets/HealthActivityWidget.tsx` | today + 7-day average, best day, estimated miles; all values real; tapping opens `/health` |
| Android home-screen widget | `android/.../widgets/HealthWidget.java` | today, progress, 7-day average; tints from the active theme |
| Settings → Sensors | `pages/Settings.tsx` | enable/disable step + location sensors |

Widget tiles are derived from real data only: **7-day average**, **best day**,
and **estimated miles** (steps × 0.7 m stride, labelled "est."). Stairs/active
minutes/calories are deliberately *not* shown — there is no data source, and
inventing numbers would make the dashboard disagree with Wander.

## Family activity feed

Sharing is opt-in and private by default: Settings → *Activity sharing*
(`components/settings/ActivitySharingPanel.tsx`) picks an audience (everyone in
the circle, or specific people) and scopes (*totals*, *workouts*,
*achievements*). Identity stores the row (`UserActivitySharing`); geo enforces
it when serving `GET /activity/summary` (your own data — never requires
sharing) and `GET /activity/feed` (only enabled rows whose audience includes
the viewer; each entry is projected down to the scopes that person chose, and
an empty feed is a normal empty list, never an error).

The Health page renders both as **Family Activity**
(`components/health/FamilyActivityCard.tsx`): Today/Week/Month toggle, your
row first, then everyone else ranked by shared steps, with only their chosen
scopes shown (`data-testid="family-activity-card"`, `feed-window-*`,
`feed-user-{username}`, `feed-empty`).

## Navigation

The split also unloaded the mobile bottom bar. It now pins five destinations —
Home, Wander, Family, Media, Health — plus a **More** button that opens a
bottom sheet with Calendar, Notes, Lab (admins) and Settings
(`components/layout/BottomNav.tsx`, `data-testid="more-sheet"`). The sheet
closes on selection, backdrop tap or Escape.

## Known gaps

- **No background service.** The manifest declares background-location
  permissions but no foreground service exists, so while the app is suspended
  the native counter still advances (SharedPreferences) but the server is only
  updated when the app runs again. A foreground service or WorkManager sync is
  the next step.
- Distance is an estimate from stride length, not GPS.
- Health Connect (`ACTIVITY_RECOGNITION` runtime grant) is still pending.

## Tests

- `services/geo/tests` — steps storage, workouts
- `services/ui/src/test/HealthAnalysisOptIn.test.tsx` — analysis stays opt-in on the Health page
- `services/ui/src/test/BottomNav.test.tsx` — pinned tabs + More sheet
- Typecheck/lint cover the widget metric math

## Midnight rollover (and the inflated-count bug)

Android's `TYPE_STEP_COUNTER` only reports **steps since boot**, so every
"today" number is app arithmetic. Two rules matter:

**Plugin (`StepCounterPlugin.computeTodaySteps`).** When the local date changes,
the delta since the previous reading is credited to the new day **only if the
previous reading was within the last hour** (`MIDNIGHT_CREDIT_WINDOW_MS`).
That covers the normal case (a reading just before midnight and one just after,
e.g. overnight) while refusing to attribute a long gap to today. The new day's
total is **only that delta** — never the previous day's accumulated
`day_steps` added on top (a `+=` here once carried a full 10,000-step day into
the next morning whenever the app stayed awake across midnight; fixed in the
1.4.12 build). In-flight installs are repaired once by a `prefs_version`
migration that recognizes the buggy signature (`day_steps` ≥ yesterday's
ledger row by a small margin) and subtracts yesterday back out, rewriting both
prefs and the ledger row via `StepLedger.replaceDay`.

Before this rule, the delta was *always* credited to the new day. If the app
went a whole day without a reading (app never opened, so the plugin never
polled), every step from that missing day landed on today — which is exactly
the "it didn't reset at midnight" report: a missing 2026-09-25 bucket and an
inflated 2026-09-26 total. Steps taken during a long gap are now dropped rather
than misattributed; keeping them would require a background service (see gaps).

**Server (`_record_daily_steps`).** The day is derived from the reading's own
`timestamp` (not arrival time), and each day keeps the **max** value seen, so a
late-arriving post cannot seed a new day and a reboot (counter drops) cannot
reduce a recorded day. `services/geo/tests/test_steps.py` pins all of this.

## Device ledger (source of truth) and multi-source fusion

**On-device ledger.** `StepLedger.java` keeps a tiny SQLite database
(`step_ledger.db`): one row per day per source plus a small anchor table. It is
the source of truth for days the app never got to sync — the day rollup is
persisted on every reading, so history survives reboots (the raw
`TYPE_STEP_COUNTER` resets on boot), app kills and days when the app never
opened. The plugin exposes it as `getDayHistory` / `getDaysSince`, and
`LocationContext.backfillStepHistory()` reconciles any day newer than
`jarvis_steps_backfilled_through` back to the server, tagged with its source.

**Server sources + fusion.** Readings carry a `source` (`phone`, `watch`, `ha`,
`health_connect`, `intervals`). Per-source buckets live in
`geo:steps_src:{user}:{source}`; the fused view (`geo:steps:{user}`, what the
UI reads) is recomputed as the **max across sources** for that day:

- summing would double-count a walk seen by both phone and watch
- max never overstates a shared walk and still credits a watch for steps the
  phone missed (e.g. phone left on the table)

`GET /api/geo/steps` returns the fused value plus a `sources` breakdown for
today so the UI can explain a difference. `services/geo/tests/test_steps.py`
pins fidelity (timestamp-owned buckets, max-per-day, fusion, unknown-source
fallback).

**Online / other-app sync (planned).** Health Connect is the free on-device
hub other health apps read/write (Google Fit is discontinued); a watch or
Health Connect writer becomes another `source`. For an online target, the
candidate is intervals.icu (free, wellness API incl. steps, bridges to
Garmin/Strava). See `docs/ACHIEVEMENTS.md` for the sharing/points design.
