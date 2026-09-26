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

The goal is stored as `geo:steps_goal:{user}`; the default remains 10,000.

## Surfaces

| Surface | File | Notes |
|---|---|---|
| Wander steps card | `pages/Wander.tsx` | ring + 7-day bars, **edit goal** inline |
| Dashboard Health widget | `components/widgets/HealthActivityWidget.tsx` | today + 7-day average, best day, estimated miles; all values real |
| Android home-screen widget | `android/.../widgets/HealthWidget.java` | today, progress, 7-day average; tints from the active theme |
| Settings → Sensors | `pages/Settings.tsx` | enable/disable step + location sensors |

Widget tiles are derived from real data only: **7-day average**, **best day**,
and **estimated miles** (steps × 0.7 m stride, labelled "est."). Stairs/active
minutes/calories are deliberately *not* shown — there is no data source, and
inventing numbers would make the dashboard disagree with Wander.

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
- `services/ui/src/test/WanderAnalysisOptIn.test.tsx` — analysis stays opt-in
- Typecheck/lint cover the widget metric math

## Midnight rollover (and the inflated-count bug)

Android's `TYPE_STEP_COUNTER` only reports **steps since boot**, so every
"today" number is app arithmetic. Two rules matter:

**Plugin (`StepCounterPlugin.computeTodaySteps`).** When the local date changes,
the delta since the previous reading is credited to the new day **only if the
previous reading was within the last hour** (`MIDNIGHT_CREDIT_WINDOW_MS`).
That covers the normal case (a reading just before midnight and one just after,
e.g. overnight) while refusing to attribute a long gap to today.

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
