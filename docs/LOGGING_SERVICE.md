# Logging Service

Redis-backed log ingestion, storage, and real-time streaming for the SharedLLM SOA architecture.

## Architecture

```text
+-------------+     POST /api/logs     +-------------+
|  Gateway    | ---------------------> |  Logging    |
|  Execution  |                        |  Service    |
|  Identity   |                        |             |
+-------------+                        +------+------+
                                              |
                            +-----------------+-----------------+
                            v                 v                 v
                     +-------------+   +-------------+   +-------------+
                     | Redis       |   | Redis       |   | WebSocket   |
                     | Sorted Set  |   | PubSub      |   | Clients     |
                     | logs:entries|   | logs:stream |   | (UI)        |
                     +-------------+   +-------------+   +-------------+
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

Defaults are in `services/config.py`. The service reads the Identity settings
once at startup (`resolve_runtime_config()` in its lifespan), so a change takes
effect when the logging container restarts.

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
GET /logs?limit=50&service=gateway&user_id=...   (same as GET /api/logs)
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
- `level` (string, optional, `/api/admin/logs`): comma-separated levels, e.g.
  `warning,error` (`warning` also matches `WARN`). Scans the whole buffer
  rather than the newest `limit × 10` lines.

What is in the log: almost entirely the gateway's request lines
(`GET /path` and `RESPONSE GET /path -> 200`). A response line is `ERROR` for a
5xx, `WARNING` for a 4xx and `INFO` otherwise, so failed requests can be
found with `level=warning,error`. With the default `LOG_MAX_ENTRIES` the buffer
covers roughly the last one to two hours.

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

Streams each new log entry (a JSON object in the log entry format) from Redis
PubSub. The client's heartbeat `{"type": "ping"}` is answered with
`{"ping": true}` (the UI's `lib/wsManager.ts` reconnects when a ping goes
unanswered), and the subscription ends as soon as the client leaves.
Authenticated by `?token=`: the internal secret or an **admin's** API key
(Identity's `validate-api-key`); anything else is refused before the socket is
accepted. Caddy routes `/api/logs/stream*` straight to this service.

Used by Jarvis Lab → Admin view → Live Telemetry. Admin → Database & Audit →
Audit Trail reads `GET /api/admin/logs`; the dashboard's admin-only **Recent
Problems** panel reads `GET /api/admin/logs?level=warning,error` (its Clear
button, after a confirm, empties the whole log). The header bell does **not**
read logs: it shows the user's own notices from the telemetry outbox.

## Retention Cleanup

- A background task removes entries older than `LOG_RETENTION_DAYS` (hourly).
- Every write trims the set to the newest `LOG_MAX_ENTRIES`.

Logs are purged automatically; nothing needs doing by hand.

## Security

- Every HTTP endpoint requires the `X-Internal-Secret` header (compared in constant time)
- Sensitive fields are redacted: `api_key`, `authorization`, `cookie`, `password`, `secret`, `token`, the GitHub/GitLab/Home Assistant/Nextcloud credential fields, etc.
- Bearer tokens, GitHub PATs, and GitLab tokens are pattern-matched and redacted
- Log messages and context fields are truncated to 4000 characters

## Migration from SQLite

The previous SQLite backend has been replaced with Redis. Key benefits:

- **No single-writer bottleneck**: Redis handles concurrent writes
- **Automatic rotation**: the hourly retention sweep plus the per-write cap, no manual pruning
- **Real-time streaming**: PubSub replaces manual WebSocket connection management
- **Persistence**: the logs live in Redis, which runs with `appendonly yes`; the logging container itself mounts no volume
- **Scalability**: sorted set queries are O(log(N)+M) vs SQLite's O(N) full scans

The old SQLite logs were not migrated.
