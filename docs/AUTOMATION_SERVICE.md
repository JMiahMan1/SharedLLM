# Automation Service

The scheduler for timers, alarms and scheduled jobs: it watches Redis for
timers that are due and dispatches each one to an internal service.

**Container:** `sharedllm_automation` · **Code:** `services/automation/main.py`
· **Port:** 11437 (FastAPI; internal only, `AUTOMATION_PORT`)

## How it works

```text
Execution timer handler -- writes --> Redis  timer:{user_id}:{timer_id}
Automation scheduler   -- SCAN timer:* --> due?  -- dispatch --> execution /execute/trigger
                                                                (or an allowlisted target)
```

- **Loop**: scans `timer:*` with `SCAN` (never `KEYS`), fires everything due,
  then sleeps until the next timer is due (at least 1 s, at most 60 s), so new
  timers are still picked up promptly.
- **Dispatch**: a timer without `target` posts `{"timer": …}` to execution's
  `POST /execute/trigger` (which plays the alert on `target_device` through
  Home Assistant). A timer with `target: {service, path, method?, payload?}`
  is sent to that service instead (the payload gains `timer` and `user_id`);
  `service` must be one of `execution`,
  `identity`, `geo`, `rag` and `path` a plain absolute path, so a timer can
  never reach an arbitrary host. Calls carry `X-Internal-Secret`.
- **Recurring timers**: after firing, `expires_at` is advanced past the fire
  time from the `recurrence` text (`every N minutes/hours/days/weeks`,
  hourly, daily, weekly, monthly, yearly; unrecognised text advances one
  day, since wrong-but-bounded beats an endless refire). One-time timers are
  deleted after a successful run.
- **Failures**: a failed dispatch is retried with exponential backoff (30 s,
  doubling, capped at 30 min) up to `max_attempts` (default 5); then the timer
  is parked inactive (kept 7 days for inspection). Recurring timers still
  advance after a failed run. Each run is recorded in `timer:…:history`
  (last 20).

## Timer payload

| Field | Description |
|---|---|
| `id`, `user_id` | Identity of the timer and its owner |
| `type`, `title` | e.g. alarm/countdown, label |
| `expires_at` | ISO 8601 (UTC; legacy naive-local rows are still parsed) |
| `active` | `false` = paused or parked |
| `recurrence` | Optional rule text (above) |
| `target_device` | HA entity to play the alert on (default dispatch) |
| `target` | Optional `{service, path, method, payload}` (above) |
| `attempts`, `last_status`, `max_attempts` | Failure bookkeeping |

## HTTP API (all but `/health` need `X-Internal-Secret`)

| Route | What |
|---|---|
| `GET /health` | Liveness |
| `GET /api/automation/status` | Counts and next due timer |
| `GET /api/automation/timers?user_id=` | Timers, optionally for one user |
| `GET /api/automation/timers/{id}/history` | Run history for one timer |

Creating, pausing and deleting timers happens in the execution service's timer
handler, not here.

## Configuration

`INTERNAL_SECRET`, `REDIS_URL` and the network-scoped service URLs come from
`.env` / `resolve_runtime_config()` like every service (see `docs/CONFIG.md`).
Dependencies: `aiohttp`, `redis`, `fastapi`, `uvicorn`.

## Operations

```bash
docker logs -f sharedllm_automation
```

Tests: `services/automation/tests/`.
