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
