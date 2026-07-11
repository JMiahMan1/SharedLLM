# Automation Service

## Overview

The Automation service is a background scheduler that polls Redis for timed events (timers) and dispatches them to the Execution service for action. It runs continuously in a loop, checking for timers whose `expires_at` timestamp has passed.

**Container:** `sharedllm_automation`
**No public port** — communicates internally via HTTP and Redis.

## How It Works

### Architecture

```
User (via Execution/Gateway) → Execution Service
                               └─ Stores timer in Redis as `timer:{user_id}:{timer_id}`

Automation Service ──polls──> Redis keys matching `timer:*`
                               └─ Expires found? ──> POST /execute/trigger → Execution Service
                                                              └─ Execution dispatches media alert via Home Assistant
```

### Scheduler Loop

1. Connects to Redis on startup.
2. Every **5 seconds** (`SCHEDULER_INTERVAL`), scans for keys matching `timer:*`.
3. For each active timer:
   - Parses the JSON payload and compares `expires_at` against `datetime.now()`.
   - If the timer has expired, sends a `POST` to `http://execution.local:8003/execute/trigger` with the timer data.
   - Deletes one-time timers (those without `recurrence`); recurring timers are re-processed on their schedule (`expires_at` is left as-is and the timer fires again).
4. Catches and logs errors without restarting the loop.

### Timer Payload Schema

Timers stored in Redis (set by Execution service timer handler):

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | UUID |
| `user_id` | string/int | Resolved user identifier |
| `type` | string | Timer type (e.g. "alarm", "countdown") |
| `title` | string | Human-readable label |
| `expires_at` | string (ISO 8601) | When the timer should fire |
| `active` | boolean | Whether the timer is currently active (paused timers set to `false`) |
| `recurrence` | string/undefined | Recurrence rule (e.g. `FREQ=DAILY`); recurring timers are re-processed when they fire |
| `target_device` | string/undefined | HA entity ID to play the alert on |

### Trigger Execution Flow

When the automation service fires a timer, the Execution service's `/execute/trigger` endpoint:

1. Extracts the user ID from the payload.
2. Resolves credentials via `resolve_internal_user()` (fetches HA token).
3. If a `target_device` is specified, dispatches a media announcement to Home Assistant via `media.handle_media_play()` with `media_type="announcement"`.

## Configuration

### Environment Variables

All variables come from `.env` via `env_file` and are resolved at runtime through `resolve_runtime_config()`.

| Variable | Source | Purpose |
|----------|--------|---------|
| `INTERNAL_SECRET` | `.env` | Authenticates HTTP calls to Execution service |
| `FERNET_KEY` | `.env` | Encryption key (required by config resolution) |
| `REDIS_URL` | `.env` | Redis connection string (`redis://redis:6379/0`) |
| `EXECUTION_SVC_URL` | `.env` | Execution service URL (`http://execution.local:8003`) |
| `TZ` | `.env` | Timezone (`America/Phoenix`) |

Other environment variables are set but not used by this service: `OLLAMA_URL`, `IDENTITY_SVC_URL`, `RAG_SVC_URL`, `STORAGE_SVC_URL`, `LOGGING_SVC_URL`, `WORKSPACE_RUNTIME_SVC_URL`.

### Hardcoded Constants

| Constant | Value | Purpose |
|----------|-------|---------|
| `SCHEDULER_INTERVAL` | `5` | Seconds between Redis scans |

## Dependencies

| Package | Purpose |
|---------|---------|
| `httpx` | Async HTTP client for calling Execution service |
| `redis` | Async Redis client for timer polling |

Built from base image `sharedllm-base`, with `jq` installed for debugging.

## Integration with Other Services

| Service | Relationship |
|---------|-------------|
| **Execution** | Receives timer trigger events via `POST /execute/trigger`. Execution handles the actual action (HA announcements, audio playback). |
| **Redis** | Shared key-value store. Automation reads from `timer:*` keys; Execution writes them via `timer:{user_id}:{timer_id}` pattern. |

### Docker Dependencies

```yaml
depends_on:
  - redis
  - execution
```

### Network

- Uses Docker network `sharedllm` for inter-service communication.
- Uses `http://execution.local:8003` to reach the Execution service (host network, resolved via DNS).

## Operations

### Running Locally

```bash
python services/automation/main.py
```

### Docker Build

```bash
docker compose build automation
```

### Restart

```bash
docker compose restart automation
```

### Logs

```bash
docker logs -f sharedllm_automation
```

## Limitations & Known Gaps

- **No recurring timer support:** The scheduler identifies recurring timers but has no logic to update `expires_at` after firing (marked `pass` in code).
- **Broad Redis scan:** Uses `KEYS timer:*` which is a blocking operation. For large timer counts, this could cause Redis latency spikes.
- **No health check endpoint:** The service has no HTTP API, so it cannot be health-checked via HTTP probes.
- **No timer management API:** Timer creation, deletion, pausing, and resuming are all handled by the Execution service, not this one.
