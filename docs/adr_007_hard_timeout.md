# ADR 007: Hard Timeout Policy for Raven Jobs

## Status
Proposed

## Context
Raven's `AgentLoop` iterates up to `MAX_TOOL_ITERATIONS = 30`. There is no **total elapsed time** cap. A single iteration — LLM inference + tool execution — can take 2-5 minutes (Ollama ~40 tokens/s on 7B, plus tool latency). In pathological cases (model hallucination, infinite reasoning), a job could run for hours, holding the `INFERENCE_LOCK` and blocking all other LLM usage.

Additionally, background worker `_process_inference_job` does not implement a hard timeout around the entire `AgentLoop` call.

## Decision

Enforce `MAX_TOTAL_SECONDS` per Raven job.

### Implementation

1. Add to `gateway/config.py`:
```python
import os
RAVEN_MAX_TOTAL_SECONDS = int(os.getenv("RAVEN_MAX_TOTAL_SECONDS", "600"))  # 10 min
RAVEN_ITERATION_TIMEOUT = int(os.getenv("RAVEN_ITERATION_TIMEOUT", "180"))  # 3 min per iteration
```

2. In `agent_loop.py`, at loop start:
```python
loop_start = asyncio.get_event_loop().time()
MAX_SECONDS = RAVEN_MAX_TOTAL_SECONDS
```

3. Inside the iteration loop, before each LLM call:
```python
elapsed = asyncio.get_event_loop().time() - loop_start
if elapsed > MAX_SECONDS:
    log.error(f"[AgentLoop] JOB TIMEOUT after {elapsed:.0f}s at iteration {agent_iter+1}")
    return f"ERROR: Raven job exceeded time limit of {MAX_SECONDS}s. Partial result: {ans or 'No output yet'}"
```

4. Per-iteration guard (optional but recommended):
   - Wrap `execute_inference` + tool execution in `asyncio.wait_for(..., timeout=RAVEN_ITERATION_TIMEOUT)`
   - Catch `asyncio.TimeoutError` → log warning → treat as iteration failure → retry once with shorter prompt

5. Background worker timeout wrapper:
```python
try:
    async with asyncio.timeout(RAVEN_MAX_TOTAL_SECONDS + 60):  # buffer
        await self._process_inference_job(job)
except asyncio.TimeoutError:
    log.error(f"Job {job_id} exceeded hard timeout; marking failed")
    await self.job_queue.fail_job(job_id, "Hard timeout: job exceeded maximum duration")
```

**Python version note:** `asyncio.timeout` introduced in 3.11; for 3.10 use `asyncio.wait_for`.

## Consequences

- ✅ No runaway jobs; guarantees lock release within bounded time
- ✅ Predictable maximum latency for Librarian queue
- ⚠️ Jobs may terminate mid-fix; user receives partial result with explanation
- ⚠️ Need to tune `MAX_TOTAL_SECONDS` — long enough for complex debugging, short enough to block minimally
- ✅ Enables SLA: "Raven repair tasks complete within 10 minutes 95% of the time"

## Validation

- Craft query requiring >10 iterations → verify timeout at ~10m mark
- Monitor logs: `[AgentLoop] JOB TIMEOUT` appears cleanly with context
- Ensure `INFERENCE_LOCK` is released on timeout (use `async with` context manager guarantees this)

---

**Tunable Parameters (environment variables):**

| Variable | Default | Recommended | Notes |
|----------|---------|-------------|-------|
| `RAVEN_MAX_TOTAL_SECONDS` | 600 (10m) | 600–1800 | Based on task complexity |
| `RAVEN_ITERATION_TIMEOUT` | 180 (3m) | 120–300 | Prevent single step stall |
| `RAVEN_HEARTBEAT_INTERVAL` | 15s | 10–30s | Already configurable |

**Metrics to Alert:**

- `gateway_raven_jobs_timed_out_total` — count of timeout terminations
- `gateway_raven_avg_duration_seconds` — p50, p95, p99

If timeout rate >5% → increase limits or improve model efficiency.
