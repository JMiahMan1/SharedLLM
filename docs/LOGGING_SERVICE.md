# Logging Service

Redis-backed log ingestion, storage, and real-time streaming for the SharedLLM SOA architecture.

## Architecture

```ascii
┌─────────────┐     POST /api/logs     ┌─────────────┐
│  Gateway    │ ──────────────────────▶ │  Logging    │
│  Execution  │                        │  Service    │
│  Identity   │                        │             │
└─────────────┘                        └──────┬──────┘
                                               │
                             ┌─────────────────┼─────────────────┐
                             ▼                 ▼                 ▼
                      ┌─────────────┐   ┌─────────────┐   ┌─────────────┐
                      │ Redis       │   │ Redis       │   │ WebSocket   │
                      │ Sorted Set  │   │ PubSub      │   │ Clients     │
                      │ logs:entries│   │ logs:stream │   │ (UI)        │
                      └─────────────┘   └─────────────┘   └─────────────┘
```

## Storage Model

**Redis Sorted Set** (`logs:entries`)

- Score: Unix timestamp of log entry
- Member: JSON-serialized log entry
- Enables efficient range queries by time and automatic retention cleanup

**Redis PubSub** (`logs:stream`)

- Real-time broadcast channel for WebSocket clients
- Every ingested log is published to this channel

## Configuration

| Setting | Default | Description |
| --- | --- | --- |
| `REDIS_URL` (env) | `redis://redis:6379/0` | Redis connection string |
| `log_retention_days` (Identity setting → `LOG_RETENTION_DAYS`) | `30` | Days kept before deletion |
| `log_max_entries` (Identity setting → `LOG_MAX_ENTRIES`) | `10000` | Hard cap; oldest entries are trimmed on write |
| `INTERNAL_SECRET` (env) | *(required)* | Service-to-service authentication |

Defaults are in `services/config.py`. Note: `services/logging/main.py` imports
`LOG_RETENTION_DAYS` / `LOG_MAX_ENTRIES` by value at import time, before
`resolve_runtime_config()` runs in its lifespan, so changing the Identity
settings currently has no effect — the defaults (30 days, 10000 entries)
always apply.

## API Endpoints

### Ingest a Log Entry

```text
POST /api/logs
POST /log
POST /logs
```

**Headers:**

- `X-Internal-Secret: <secret>`

**Body:**

```json
{
  "user_id": "system",
  "service": "gateway",
  "level": "INFO",
  "message": "Request processed successfully",
  "context": {"request_id": "abc123"}
}
```

### Fetch Recent Logs

```text
GET /logs?limit=50&service=gateway&user_id=…     (same as GET /api/logs)
GET /api/admin/logs?limit=100&service=execution
```

All reads require `X-Internal-Secret`. From the browser the only route is the
gateway's **admin-only** `GET /api/admin/logs`; the gateway's old
unauthenticated `GET`/`DELETE /api/logs` were removed on 2026-10-15
(`411e51d3`) because anyone could read or wipe the system logs through them.

**Query Parameters:**

- `limit` (int, default 100, max 5000): Number of entries to return
- `service` (string, optional): Filter by service name
- `user_id` (string, optional): Filter by user ID (admins see all)

**Response:**

```json
[
  {
    "user_id": "system",
    "timestamp": "2026-05-15 17:36:42",
    "service": "gateway",
    "level": "INFO",
    "message": "Request processed successfully",
    "context": {"request_id": "abc123"}
  }
]
```

### Clear Logs (Admin Only)

```text
DELETE /api/admin/logs      (same handler as DELETE /api/logs)
```

Requires `X-Internal-Secret`; from the browser, the gateway's admin-only
`DELETE /api/admin/logs`.

### WebSocket Stream

```text
WS /api/logs/stream
WS /logs/stream
```

Connects to Redis PubSub and streams log entries in real time. Each message is
a JSON object matching the log entry format, with a `{"ping": true}` keepalive.
Authenticated by `?token=` — the internal secret or a valid user API key
(checked against Identity); otherwise the socket is closed with code 4401.
Caddy routes `/api/logs/stream*` straight to this service.

## Retention Cleanup

- A background task removes entries older than `LOG_RETENTION_DAYS`.
- Every write trims the set to the newest `LOG_MAX_ENTRIES`.

Logs are automatically purged — no manual intervention required.

## Security

- All ingestion endpoints require `X-Internal-Secret` header
- Sensitive fields are automatically redacted: `api_key`, `authorization`, `cookie`, `password`, `token`, etc.
- Bearer tokens, GitHub PATs, and GitLab tokens are pattern-matched and redacted
- Log messages and context fields are truncated to 4000 characters

## Migration from SQLite

The previous SQLite backend has been replaced with Redis. Key benefits:

- **No single-writer bottleneck** — Redis handles concurrent writes efficiently
- **Automatic rotation** — TTL-based cleanup, no manual pruning needed
- **Real-time streaming** — PubSub replaces manual WebSocket connection management
- **Persistence** — the logs live in Redis, which runs with `appendonly yes`; the logging container itself mounts no volume
- **Scalability** — Sorted set queries are O(log(N)+M) vs SQLite's O(N) full scans

The old SQLite logs were not migrated.
