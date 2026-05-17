# Raven Mission Management

## Overview
Raven missions are long-running autonomous tasks dispatched through the gateway's background worker. Each mission is tracked in the Identity service database and streamed via Redis PubSub for live monitoring.

## Architecture

### Mission Lifecycle
1. **Create** — `POST /api/raven/missions` (gateway) → stores in Identity DB → enqueues job
2. **Execute** — Background worker picks up job → runs `AgentLoop` → streams events to Redis
3. **Complete** — Worker patches mission status to `completed`/`failed` with result
4. **Learn** — On success, mission summary is persisted to RAG `system_learnings` collection

### Data Flow
```
UI/API → Gateway POST /api/raven/missions → Identity DB (mission record)
                                              ↓
                                    Background Worker (job_queue)
                                              ↓
                                    AgentLoop (tool calls, inference)
                                              ↓
                              Redis PubSub (raven:mission:stream:{id})
                              Redis List   (raven:mission:history:{id})
                                              ↓
                              Identity PATCH (status + result + output_log)
                                              ↓
                              RAG Ingest (system_learnings collection)
```

## Learning System

### How Raven Learns
After each successful mission, `_persist_learning()` in `agent_loop.py` sends a summary to the execution service's learning handler, which ingests it into the RAG `system_learnings` collection with tags:
- `raven`, `autonomous`, `repair` (always)
- `workspace`, `git`, `deployment` (contextual)

This means future RAG queries can surface past mission outcomes when similar tasks are requested.

### What Gets Stored
- **Mission record** (Identity DB): `proposed_mission`, `status`, `result`, `output_log` (full audit JSON)
- **RAG learning entry**: topic, content (actions taken + final answer), tags

## Mission Cleanup

### Delete Endpoints
| Endpoint | Description |
|----------|-------------|
| `DELETE /api/raven/missions/{id}` | Delete mission by ID (Identity service) |
| `DELETE /api/raven/missions/{slug}` | Delete mission by slug |

### Clearing Completed Missions
To bulk-clean completed/failed missions while preserving learning context:

```bash
# Delete all completed missions
curl -X DELETE http://localhost:8080/api/raven/missions/{id}

# Or via Python script for bulk cleanup
import httpx
resp = httpx.get("http://localhost:8080/api/raven/missions", headers={"Authorization": "Bearer admin"})
for m in resp.json():
    if m["status"] in ("completed", "failed"):
        httpx.delete(f"http://localhost:8080/api/raven/missions/{m['id']}", headers={"Authorization": "Bearer admin"})
```

### Keep Context Option
When deleting a mission, the RAG learning entry is **not** deleted — it persists independently. The `output_log` (full audit trail) is stored on the mission record and will be lost on deletion, but the learning summary in RAG remains for future context retrieval.

**To preserve everything**: Don't delete. The mission record and RAG entry are separate.

**To clean UI but keep learning**: Delete the mission record — RAG `system_learnings` entries survive.

**To fully erase**: Delete the mission AND remove from RAG:
```bash
curl -X POST http://localhost:8004/rag/purge \
  -H "X-Internal-Secret: RAVEN_SECURE_2026" \
  -d '{"collection_name": "system_learnings", "filter": {"topic": "Raven repair: <query>"}}'
```

## Redis Stream Keys

| Key | Type | TTL | Purpose |
|-----|------|-----|---------|
| `raven:mission:history:{id}` | List | 24h | Ordered event log for WebSocket replay |
| `raven:mission:stream:{id}` | PubSub channel | N/A | Live event broadcast |
| `raven:mission:kill:{id}` | PubSub channel | N/A | Kill signal channel |

## WebSocket Live Trace

Endpoint: `ws://host/api/raven/missions/{id}/stream?token={api_key}`

Event types:
- `system` — Iteration start/end, mission state changes
- `reasoning` — LLM reasoning tokens
- `action` — Tool being executed
- `action_payload` — Tool payload (credentials redacted)
- `result_success` — Tool succeeded
- `result_error` — Tool failed

## UI Integration

The Jarvis Lab page (`/lab` → Missions tab) provides:
- Mission dispatch with quick action templates
- Status filter (all, executing, completed, failed, pending)
- Watch Live button (WebSocket stream modal)
- Stop button (sends kill signal via Redis PubSub)

## Security

All mission endpoints require authentication. The WebSocket stream validates the API token. Execution results and action payloads are sanitized via `sanitize_for_llm()` before streaming or feeding to the LLM — credentials (tokens, passwords, keys) are redacted at three layers:
1. AgentLoop (`sanitize_for_llm()` on exec results and 422 errors)
2. `emit_log()` (sanitizes before sending to logging service)
3. Logging service (ingest-time sanitization)
