# ADR 006: Circuit Breaker Policy for Inter-Service Calls

## Status
Proposed

## Context
Raven's `AgentLoop` makes frequent HTTP calls to Execution, WorkspaceRuntime, and other microservices. When a downstream service degrades (high latency, errors), the agent wastes time retrying before eventually failing. In the worst case, this can cause a chain reaction: 30 iterations × 3 retries = 90 calls to a down service, each blocking for 30s.

We need **fail-fast** behavior when services are unhealthy, and automatic recovery when they heal.

## Decision

Integrate `aiobreaker` (already listed in `requirements.txt`) around all outbound HTTP calls from:
- `gateway/agent_loop.py` (tool dispatch)
- `gateway/orchestrator.py` (RAG fetch)
- `gateway/background_worker.py` (health check probes)
- `workspace_runtime/main.py` (storage provider calls)

### Breaker Configuration (tunable via env):

```python
import aiobreaker

 breaker = aiobreaker.CircuitBreaker(
    fail_max=5,                  # Open after 5 consecutive failures
    timeout_duration=30,         # Stay open for 30s before half-open
    expected_exception=(
        httpx.RequestError,
        httpx.HTTPStatusError,
    )
)
```

### Scope of Protection

| Service Call | Breaker Name | Fail Threshold |
|--------------|--------------|----------------|
| `execution:8003/execute/*` | `breaker_execution` | 5 |
| `workspace_runtime:8007/*` | `breaker_workspace` | 5 |
| `rag:8004/rag/search` | `breaker_rag` | 3 (RAG is soft dependency) |
| `storage:8005/providers/*` | `breaker_storage` | 5 |
| `identity:8001/api/*` | `breaker_identity` | 3 (identity resolution critical but fast) |

### Fallback Behavior

When breaker is **OPEN**:
1. **Execution/Storage/Workspace** → return `ExecutionResult(status="FAILURE", message="Service temporarily unavailable (circuit open)")`
2. **RAG** → skip context injection, log warning, continue without RAG
3. **Identity** → use cached credentials if available; else fail request with 503

AgentLoop interprets these as tool failures and will attempt self-repair (which will hit same breaker → propagate failure fast).

### Monitoring

Expose `/metrics` endpoint on each service:
```
circuit_breaker_state{target="execution"} 1  # 0=closed,1=open,2=half-open
circuit_breaker_failures_total{target="execution"} 5
```

## Consequences

- ✅ Fast failure (milliseconds) instead of 30s timeout when service down
- ✅ Prevents log flooding and wasted LLM tokens on futile retries
- ✅ Automatic recovery probe after 30s
- ⚠️ Introduces external dependency (`aiobreaker`)
- ⚠️ Requires tuning: thresholds too low → false trips; too high → slow draining

## Validation

- Simulate Execution service returning 500 for 6 consecutive calls → verify breaker opens → subsequent calls immediate failure
- Wait 30s → verify half-open → single test call → if succeeds, breaker closes

---

**Tuning Plan:**

1. Initial values as above
2. After 48h production, review:
   - `circuit_breaker_failures_total` counts
   - Mean time to recovery (MTTR) for each downstream
   - False positive rate (open during brief blip)

3. Adjust:
   - `fail_max`: lower for flaky services, higher for stable
   - `timeout_duration`: longer for deep failures requiring manual intervention
