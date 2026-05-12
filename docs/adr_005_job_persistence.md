# ADR 005: Job Persistence & Resumability Layer

## Status
Proposed

## Context
Raven jobs are stored in Redis with a 1-hour TTL. If the worker process crashes or the container restarts mid-job, the Redis entry survives but **in-memory state** (current iteration number, `action_log` history, partial results) is lost. The job is re-queued via lease reclamation, but it restarts from iteration 0 — repeating work already done and potentially causing side-effect duplication (e.g., running pytest twice).

For long-running repair sessions (10+ iterations), this waste is significant and risks idempotency violations.

## Decision

Introduce a lightweight **Job Persistence Layer** (JPL) that checkpoints job state to Redis (no external DB dependency initially).

### Data Model

Key: `raven:job:{job_id}` → hash:

| Field | Type | Description |
|-------|------|-------------|
| `job_id` | str | UUID |
| `user_id` | str | Identity |
| `status` | str | `queued|processing|completed|failed|timeout` |
| `iteration` | int | Current loop iteration (0-based) |
| `action_log` | JSON list | Last 20 action entries (truncated) |
| `last_exec_data` | JSON dict | Result of last tool execution |
| `scratchpad` | str | Agent's scratchpad text (if any) |
| `created_at` | float | epoch seconds |
| `updated_at` | float | epoch seconds |
| `completed_at` | float | optional |

TTL: 24 hours (`86400` seconds) for completed/failed jobs; 2 hours for processing.

### Checkpoint Cadence

At the **end of each AgentLoop iteration** (after tool execution, before next LLM call):

```python
# agent_loop.py
checkpoint = {
    "iteration": agent_iter + 1,
    "action_log": action_log[-20:],
    "last_exec_data": safe_exec_data if 'safe_exec_data' in locals() else None,
    "scratchpad": "",  # future: agent scratchpad
    "updated_at": time.time(),
}
await redis.hset(f"raven:job:{job_id}", mapping=checkpoint)
await redis.expire(f"raven:job:{job_id}", 7200)  # refresh TTL
```

### Resume Logic on Worker Start

On `RavenWorker.start()`:

```python
async def _rehydrate_pending_jobs(self):
    keys = await redis.keys("raven:job:*")
    for key in keys:
        data = await redis.hgetall(key)
        if data.get("status") == "processing":
            # Check lease: if lease missing → reclaim to queued
            lease_key = f"raven:lease:{job_id}"
            if not await redis.exists(lease_key):
                await self.job_queue.reclaim_expired_jobs()  # existing logic
            else:
                # Job was mid-run when worker died; re-queue for resume
                log.warning(f"Re-queuing interrupted job {job_id} for resume")
                await redis.lpush(self.job_queue.QUEUE_KEY, job_id)
```

### Worker Resume Semantics

When `background_worker._process_inference_job` picks up a re-queued job:
1. Read checkpoint from Redis
2. Reconstruct `action_log` and `exec_data`
3. Call `AgentLoop(job_id=job_id, resume_from=checkpoint, ...)`
4. `AgentLoop` starts at `iteration = checkpoint["iteration"]` (not 0)

**Idempotency Safety:** Tool handlers must be **idempotent** by design:
- `git commit` with same message is safe (git prevents duplicate commits)
- `pytest` re-running is safe
- `file write` with same content is idempotent
- Side-effectful tools (e.g., `announce`) should carry a `idempotency_key` derived from `job_id + iteration`

## Consequences

- ✅ Jobs survive worker restarts/crashes without repeating work
- ✅ Audit trail: checkpoint data shows exact iteration progression
- ✅ Can implement "resume later" feature for very long tasks
- ⚠️ Additional Redis memory usage (≈1KB per active job)
- ⚠️ Need to ensure all tool side-effects are truly idempotent or guarded by `--force` flags

## Validation

- Kill -9 worker container during iteration 7 → restart → verify resume from 8 (not 0)
- Re-run same Raven job twice → verify no duplicate commits or announcements

---

**Future:** PostgreSQL table can replace Redis hash for long-term persistence and analytics.
