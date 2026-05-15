# Logging Service

Redis-backed log ingestion, storage, and real-time streaming for the SharedLLM SOA architecture.

## Architecture

```
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

| Environment Variable | Default | Description |
|---------------------|---------|-------------|
| `REDIS_URL` | `redis://redis:6379/0` | Redis connection string |
| `LOG_RETENTION_DAYS` | `30` | Days to retain logs before automatic deletion |
| `LOG_MAX_ENTRIES` | `50000` | Maximum number of log entries to keep (hard cap) |
| `INTERNAL_SECRET` | *(required)* | Service-to-service authentication token |

### Setting Retention Period

Configure log retention in `.env`:
```bash
LOG_RETENTION_DAYS=30
```

Or via the UI System Matrix → Global Settings (if exposed as a configurable setting).

## API Endpoints

### Ingest a Log Entry
```
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
```
GET /api/logs?limit=50&service=gateway&user_id=admin
GET /api/admin/logs?limit=100&service=execution
```

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
```
DELETE /api/logs
DELETE /api/admin/logs
```

**Headers:**
- `X-Internal-Secret: <secret>`

### WebSocket Stream
```
WS /api/logs/stream
WS /logs/stream
```

Connects to Redis PubSub and streams log entries in real-time. Each message is a JSON object matching the log entry format.

## Retention Cleanup

A background task runs every hour to:
1. Remove entries older than `LOG_RETENTION_DAYS`
2. Enforce `LOG_MAX_ENTRIES` cap (removes oldest entries if exceeded)

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
- **Persistence** — Redis AOF (`appendonly yes`) survives restarts
- **Scalability** — Sorted set queries are O(log(N)+M) vs SQLite's O(N) full scans

Existing SQLite data is not automatically migrated. Historical logs remain in `/app/data/logs.db` within the logging container if needed for archival.
