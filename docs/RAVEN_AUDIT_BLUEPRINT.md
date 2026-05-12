# Raven Agent: Architectural Audit & Hardening Blueprint

**Date:** 2026-05-11  
**Status:** Draft — For Review  
**Scope:** Raven autonomous loop stabilization, anti-fragility, and self-editing guardrails  

---

## 1. Executive Summary

Raven is the autonomous repair agent within the Jarvis microservices ecosystem. It operates via a long-running multi-turn LLM loop (`AgentLoop`) that can execute tools, run tests, and self-modify code. While functional, several fragility points risk silent failures, resource exhaustion, and secret leakage under production load.

**Primary Risk Vectors:**
- Single-threaded inference lock blocks all other requests during multi-turn loops (up to 30 iterations × 2-3 min each)
- No hard timeout on total loop execution → runaway jobs
- Memory growth via unbounded `action_log` accumulation
- Manual secret redaction scattered across code paths
- No circuit breakers on downstream service calls
- Redis job state lacks idempotency guarantees
- Streaming support exists but is disabled in production (stream=False)

---

## 2. Current Architecture Inventory

### Core Components

| Component | File | Role |
|-----------|------|------|
| `AgentLoop` | `services/gateway/agent_loop.py` | Multi-turn autonomous reasoning engine |
| `Orchestrator` | `services/gateway/orchestrator.py` | Routes to Raven vs Librarian paths |
| `BackgroundWorker` | `services/gateway/background_worker.py` | Singleton FIFO job processor with health monitoring |
| `InferenceJobQueue` | `services/gateway/messaging.py` | Redis-backed FIFO queue with lease-based processing |
| `WorkspaceRuntime` | `services/workspace_runtime/main.py` | Sandboxed code edit, lint, pytest, git workflow |
| `ExecutionBridge` | `services/execution/main.py` | Tool dispatcher to Home Assistant, Docker, Git |
| `OllamaProvider` | `services/gateway/llm_providers.py` | Local LLM inference with streaming support |

### Existing Hardening (from ADRs)

- **ADR 01 (Hardening Slice):** Redis job leases, server-side log sanitization, workspace runtime review workflow
- **ADR 02 (Branch Push Guardrails):** Protected branch enforcement, write→lint→pytest→commit→push ordering, review packet generation

### Deployed Services (docker-compose)

```
Gateway (8002) → Orchestrator → [Librarian | Raven(AgentLoop)]
Execution (8003) → HA, Docker, Git handlers
WorkspaceRuntime (8007) → File ops, lint, pytest, git workflows
Identity (8001) → Credential resolution
RAG (8004) → Semantic search
Storage (8005) → Nextcloud bridge
Logging (8006) → Centralized log aggregation
Redis (6379) → Job queue, state, pubsub
```

---

## 3. Fragility Assessment

### 3.1 Critical Path Analysis

```
User Request → Gateway /api/chat
  ↓
Orchestrator.process_full_orchestration()
  ↓
AgentLoop (if autonomous) — holds INFERENCE_LOCK for entire loop
  ├─ Iteration 1: Ollama call → tool extraction → tool execution
  ├─ Iteration 2: Ollama call → tool execution
  └─ … up to 30 iterations
  ↓
Learning persistence → Execution /execute/learning
```

**Problem:** The `INFERENCE_LOCK` (a global `asyncio.Lock()`) is acquired before entering the loop and released after the final iteration. During this time (often 5-30 minutes for repair tasks), **no other LLM request can be processed**. This violates the requirement that core Jarvis functions (lighting, climate) remain operational.

### 3.2 Specific Failure Modes

| # | Failure Mode | Location | Impact |
|---|--------------|----------|--------|
| 1 | **Stalled loop** — LLM hangs or enters infinite generation | `agent_loop.py:268-346` | Blocks inference indefinitely |
| 2 | **Memory creep** — `action_log` list grows without bound | `agent_loop.py:266` | OOM after many long tasks |
| 3 | **Lost job on restart** — Worker dies mid-job, Redis TTL expires, job may be re-queued but local state lost | `background_worker.py:85-122` | No progress continuity |
| 4 | **Secret leakage** — New endpoint forgets redaction | Any new handler | Credentials in logs |
| 5 | **Cascading failures** — Execution service down → unhandled exception → worker crashes | `background_worker.py:110` | Queue stops |
| 6 | **No request-level timeout** — Loop can run forever | `agent_loop.py:257` (MAX_TOOL_ITERATIONS only) | Resource starvation |
| 7 | **Pay-level streaming disabled** — stream=False forces full buffering | `agent_loop.py:254` | Higher VRAM usage |
| 8 | **VRAM pressure not proactive** — Only checked per-inference, not cumulative | `agent_loop.py:162-192` | OOM kills Ollama |
| 9 | **No idle detection** — Worker polls every 1s even when queue empty | `background_worker.py:80` | Unnecessary Redis traffic |
| 10 | **Log flooding** — Heartbeat logs every 15s per job | `agent_loop.py:276-285` | Log storage bloat |

### 3.3 Security Gaps

- **Sanitization Scope:** Redaction in `agent_loop.py:500-512` only covers the immediate tool payload. If a tool returns secrets in its response (e.g., `GitOperationRequest` showing a URL with embedded token), those are **not redacted** before being fed back into the next LLM turn.
- **User Context Injection:** `creds` dictionary containing HA/Nextcloud tokens is logged in flight (`[AgentLoop] Sending payload`) but redaction is manual and key-list is hardcoded.
- **Internal Secret Transmission:** `X-Internal-Secret` header used everywhere; if any service forgets to validate, SSRF/privilege escalation possible.

---

## 4. High-Level Architectural Blueprint

### 4.1 Design Principles

1. **Isolation by Default** — Raven's long-running work must never block synchronous Librarian requests.
2. **Bounded Resources** — Every loop iteration must have hard CPU/time/memory caps.
3. **Observability-First** — Structured logs, traces, and metrics for every phase.
4. **Zero-Trust Secrets** — Redaction enforced at service boundaries, not call sites.
5. **Graceful Degradation** — External service failures degrade quality, not availability.
6. **Idempotent State** — Jobs can be safely retried without side-effect duplication.

### 4.2 Target Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                             REQUEST INGEST                                  │
│  Gateway /api/chat  →  Intent Engine  →  Route: [Librarian | Raven]        │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                ┌───────────────────┼───────────────────┐
                │                   │                   │
                ▼                   ▼                   ▼
    ┌───────────────────┐  ┌──────────────────┐  ┌──────────────────┐
    │  Librarian Fast   │  │  Raven Queue     │  │  Raven Worker    │
    │  Path (single-    │  │  (Priority:      │  │  (Background)    │
    │   turn, no lock)  │  │   High/Normal/   │  │                  │
    │                   │  │   Low)           │  │  Per-job pod/    │
    │  1. Context fetch │  │                  │  │  container?      │
    │  2. RAG search    │  │  1. Validate job │  │                  │
    │  3. LLM infer     │  │  2. Persist to   │  │  Acquires        │
    │  4. Tool call     │  │     Redis state  │  │  INFERENCE_LOCK  │
    │  5. Response      │  │  3. Enqueue      │  │  per job         │
    └───────────────────┘  └──────────────────┘  └──────────────────┘
                                    │
                ┌───────────────────┼───────────────────┐
                │                   │                   │
                ▼                   ▼                   ▼
    ┌───────────────────┐  ┌──────────────────┐  ┌──────────────────┐
    │  Tool Execution   │  │  State Store     │  │  Job Scrubber    │
    │  (Execution Svc)  │  │  (PostgreSQL/   │  │  (Periodic)      │
    │                   │  │   Redis)        │  │                  │
    │  - Timeout: 30s   │  │                  │  │  Reaps expired   │
    │  - CircuitBreaker │  │  Job metadata,  │  │  leases, moves  │
    │  - Retry: 3×      │  │  iteration state│  │  to DLQ         │
    └───────────────────┘  └──────────────────┘  └──────────────────┘
```

**Key Changes:**

1. **Request-tier routing:** Librarian queries (non-autonomous) bypass the singleton inference lock entirely, using a separate lock-free FastAPI endpoint.
2. **Job Queue Priorities:** Three-tier queue (High=immediate, Normal=default, Low=off-hours).
3. **Per-job context propagation:** Each job gets a UUID, logged in all downstream calls.
4. **Circuit breakers:** On Execution/Storage failures, trip breaker → pause → half-open probe.
5. **Streaming-first:** All Ollama calls use `stream=True`; gateway streams chunks back to client (even if UI buffers).
6. **VRAM guard:** Pre-iteration VRAM check via `/api/ps`; if >80% used, spill context to Redis-side scratchpad.
7. **Action log eviction:** Keep only last 20 entries in in-memory `action_log`; older summaries stored in Redis Set.
8. **Hard timeout:** Total loop time capped at 10 minutes (configurable); partial results returned via `Step N: TIMEOUT`.

---

## 5. Implementation Roadmap (Iterative Slices)

### Slice 1 — Decouple Librarian from Inference Lock (Immediate, <2 hrs)

**Problem:** Currently `orchestrator.py` uses `AgentLoop` for autonomous queries, but `AgentLoop` acquires `INFERENCE_LOCK`. Librarian (`_single_turn_inference`) also acquires the same lock through `call_ollama`. This blocks all concurrent usage.

**Solution:** Librarian path must bypass the global lock entirely.

**Changes:**

1. `gateway/orchestrator.py:199-246`  
   - Create separate lock for librarian: `LIBRARIAN_LOCK = asyncio.Lock()`  
   - Conditionally acquire: `async with LIBRARIAN_LOCK:`  
   - Keep `INFERENCE_LOCK` exclusively for `AgentLoop`

2. Update `gateway/main.py` readiness check to monitor both locks' queue lengths.

**Test:** Concurrent 10 librarian requests + 1 raven job → librarian latency <200ms.

---

### Slice 2 — Job Persistence & Resumability (High Priority, 4-6 hrs)

**Problem:** If worker crashes mid-job, Redis state has TTL but no persistent history. Restart loses iteration progress.

**Solution:** Add PostgreSQL table `raven_job_runs` (or extend Redis with longer TTL) to checkpoint iteration number, last tool result, and cumulative `action_log` every iteration.

**Schema:**

```sql
CREATE TABLE raven_job_runs (
    job_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    iteration INTEGER NOT NULL DEFAULT 0,
    action_log_json JSONB NOT NULL DEFAULT '[]',
    last_exec_data_json JSONB,
    current_scratchpad TEXT,
    status TEXT CHECK(status IN ('queued','processing','completed','failed','timeout')) NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    completed_at TIMESTAMPTZ
);
```

**Integration:**
- At start of each AgentLoop iteration: `UPDATE raven_job_runs SET iteration=$1, action_log_json=$2 WHERE job_id=$3`
- On worker restart: Look up in-progress jobs from past 1 hour → re-queue.

**Migration:** Start with Redis-only checkpoint (key `raven:checkpoint:{job_id}`) to avoid DB dependency.

**Test:** Simulate crash after iteration 5 → restart worker → verify resume from iter 6.

---

### Slice 3 — Hard Timeout & Forced Termination (High Priority, 2 hrs)

**Problem:** Loop can theoretically run 30 iterations × 3 min = 90 min; no hard stop.

**Solution:**  
1. Add `MAX_TOTAL_SECONDS = int(os.getenv("RAVEN_MAX_TOTAL_SECONDS", "600"))` (10 min default)
2. Track `loop_start = asyncio.get_event_loop().time()` at `agent_loop.py:263`
3. Check on each iteration: if `elapsed > MAX_TOTAL_SECONDS` → `return "ERROR: Timeout after {elapsed}s"`
4. Worker catches this → marks job as `FAILED` with `error="timeout"` (not dead-lettered, as it's a legitimate constraint)

**UI/API:** Streaming clients get `Step N: TIMEOUT — Agent stopped after 10m` as final chunk.

---

### Slice 4 — Secret Redaction Enforcement at Service Boundary (Critical, 3 hrs)

**Problem:** Manual redaction in agent loop is error-prone; any new endpoint that logs `user_context` can leak.

**Solution:** Implement centralized sanitization middleware in **Execution** and **WorkspaceRuntime** services.

**Implementation:**

1. `services/execution/sanitize.py` — new module
```python
SECRET_KEYS = {"api_key","ha_token","nextcloud_pass","github_token","gitlab_token","git_token","fernet_key"}

def sanitize_dict(d: dict) -> dict:
    if not isinstance(d, dict): return d
    return {
        k: "[REDACTED]" if k.lower() in SECRET_KEYS else sanitize_dict(v)
        for k,v in d.items()
    }
```

2. Apply to all response models via Pydantic `@validator` pre=True, or FastAPI `BaseResponse` middleware.

3. Add log filter: `logging.Filter` that redacts JSON strings before emission.

**Test:** Send request with `nextcloud_pass="secret"` in `user_context` → verify logs show `[REDACTED]`, response payload also redacted for non-admin roles.

---

### Slice 5 — Circuit Breaker on Downstream Calls (Medium Priority, 4 hrs)

**Problem:** If Execution service is down, AgentLoop's tool call `httpx.post` throws → caught but floods logs and wastes time.

**Solution:** Integrate `aiobreaker` (already in requirements.txt) around all inter-service HTTP calls.

**Scope:**  
- `AgentLoop` tool dispatch (line 499)  
- `BackgroundWorker._get_errors` (line 170)  
- `Orchestrator._fetch_rag_context` (line 184)  
- `WorkspaceRuntime` storage calls (line 1291)

**Config:**  
- `failure_threshold = 5`  
- `recovery_timeout = 30s`  
- `expected_exception = (httpx.RequestError, httpx.HTTPStatusError)`

**Test:** Force 5 consecutive 500s from Execution → breaker opens → subsequent calls fail fast with `CircuitOpen` message.

---

### Slice 6 — Structured Request Tracing (Medium Priority, 3 hrs)

**Problem:** Logs are flat strings; no way to correlate "job 12345" across gateway → execution → workspace.

**Solution:**

1. Generate `request_id = str(uuid4())` at gateway entry point (`main.py:chat` endpoint).
2. Propagate via `X-Request-ID` header to all downstream calls.
3. Use Python's `structlog` or custom `logging.Filter` to inject `request_id` into every log record.
4. Change log format to JSON:  
   `{"timestamp":"...","level":"INFO","service":"gateway","request_id":"...","message":"..."}`

**Test:** Make chat request → grep logs for request_id → should appear in gateway, execution, workspace logs.

---

### Slice 7 — Micro-Prompt Optimization for Local Ollama (Continuous)

**Problem:** Current prompts are long (500-1500 tokens) — expensive for 7B/9B models and slow.

**Action Items:**

1. Compress system prompts via distillation (use OpenRouter to summarize → human review).
2. Add `token_budget=512` to `AgentLoop` preamble to instruct model: "Be concise; limit reasoning to 3 sentences."
3. Create model-specific prompt variants in `Identity` settings:  
   - `ollama_coding_prompt_v7b` (shorter, fewer examples)  
   - `ollama_coding_prompt_v9b` (full detail)

---

### Slice 8 — Sandboxed Self-Editing Pipeline (Critical, 6-8 hrs)

**Current State:** `WorkspaceRuntime /workflow/write-sync-commit` already implements:
- Write file
- Lint on that file
- Run targeted pytest (if targets provided)
- Create review branch if on protected branch
- Commit + push
- Provider sync

**Gaps:**
- If lint fails but pytest is not run → job marked as success anyway (bug)
- No rate-limit on retries
- No quarantine for repeatedly failing files

**Hardening:**

1. **Atomic Review Branch Creation** — Already done.
2. **Pre-commit Gated Sync** — Only push to protected branches via PR; never direct.
3. **Failure Quarantine** — If file fails lint ×3 in 10 attempts → auto-flag as `quarantined` in DB, require admin override.
4. **Audit Trail Table** — `raven_edits` log: `{job_id, workspace_id, file_path, lint_pass, pytest_pass, commit_sha, created_at}`.

---

## 6. Secure Logging Blueprint

### 6.1 Centralized Sanitizer Module

**File:** `services/gateway/sanitizer.py`

```python
import re
from typing import Any

SECRET_PATTERNS = [
    re.compile(r'(?i)(api_key|token|password|secret|key)\s*[:=]\s*([^\s,]{8,})'),
    re.compile(r'(github_pat_[a-zA-Z0-9_]+)'),
    re.compile(r'(ghp_[a-zA-Z0-9]{36,})'),
    re.compile(r'(glpat-[a-zA-Z0-9\-]+)'),
]

def sanitize_value(key: str, value: Any) -> Any:
    """Redact values for known sensitive keys."""
    key_lower = str(key).lower()
    if any(s in key_lower for s in ["token", "key", "pass", "secret"]):
        return "[REDACTED]"
    if isinstance(value, str):
        for pattern in SECRET_PATTERNS:
            if pattern.search(value):
                return "[REDACTED]"
    return value

def sanitize_dict(data: dict) -> dict:
    return {k: sanitize_value(k, v) for k, v in data.items()}
```

**Integration Points:**
- All FastAPI route handlers → call `sanitize_dict(payload)` before logging
- `logging.config.dictConfig` with custom `Filter` that redacts `record.msg` if it looks like JSON with secrets
- Pydantic serialization hook: `model_dump(mode="json")` → sanitize → log

### 6.2 Required Log Fields

Every structured log line must include:

```json
{
  "timestamp": "2025-05-11T22:22:45.000Z",
  "level": "INFO",
  "service": "gateway|execution|workspace_runtime|rag|...",
  "request_id": "uuid",
  "job_id": "uuid (if raven job)",
  "iteration": 3,
  "user_id": "jeremiah",
  "action": "WorkspaceFileReadRequest",
  "duration_ms": 145,
  "status": "success|failure|timeout",
  "error_code": "TIMEOUT|SCHEMA_ERROR|... (optional)"
}
```

**Implementation:**  
- Custom `logging.Filter` attaches `request_id` from asyncio context var `current_request_id`
- Worker loop sets `job_id` in context var
- FastAPI middleware sets/clears request-scoped vars

---

## 7. Automated Test & Linting Guardrails for Self-Editing

### 7.1 Current State

`WorkspaceRuntime /workflow/write-sync-commit` does:
1. `files.write`
2. `lint` (single file)
3. `pytest` (if targets specified)
4. `git commit`
5. `git push` (optionally)
6. `provider sync`

**Issues:**
- Lint and pytest results are returned but workflow continues even if lint fails and `push=True`
- No protection against pushing failing code to protected branches (relies on workspace runtime branch check, which is correct but should be validated)

### 7.2 Hardened Workflow Specification

**Endpoint:** `POST /workflow/write-sync-commit`

**Invariant:** `push=True` requires **ALL** of:
- `lint_passed == True` for every file in `lint_paths` + auto-detected related files
- `pytest_passed == True` if `pytest_targets` non-empty
- `branch_name` NOT in `protected_branch_patterns(identity)` → if on protected, auto-create review branch

**Implementation Updates:**

```python
# In workspace_runtime/main.py: WorkflowWriteSyncCommitRequest handler

# After lint and pytest:
if push and (not lint_results_all_passed or (pytest_run and not pytest_passed)):
    result = {
        "status": "FAILURE",
        "message": "Verification failed — will not push to remote",
        "lint": lint_summary,
        "pytest": pytest_summary,
    }
    # DO NOT commit, DO NOT push
    return JSONResponse(result, status_code=400)

# Proceed to commit & push
```

**Quarantine Logic:**
```python
QUARANTINE_THRESHOLD = int(os.getenv("RAVEN_QUARANTINE_THRESHOLD", "3"))

if file_path in quarantine_db:
    count = quarantine_db[file_path]
    if count >= QUARANTINE_THRESHOLD:
        raise HTTPException(409, f"File {file_path} is quarantined after {count} failures")
    quarantine_db[file_path] = count + 1
```

---

## 8. Architectural Decision Records (ADRs) to Generate

**New ADRs Required:**

1. **ADR 003 — Global INFERENCE_LOCK Replacement with Tiered Queues**  
   *Decision:* Replace singleton lock with per-tier queues (Librarian lock-free, Raven serialized). Rationale: Prevent blocking household automations.

2. **ADR 004 — Request-Scoped Tracing & Structured Logging**  
   *Decision:* All logs JSON with `request_id`/`job_id`. Sanitizer module mandated.

3. **ADR 005 — Job Persistence Layer**  
   *Decision:* Introduce PostgreSQL `raven_job_runs` table for resumability and audit.

4. **ADR 006 — Circuit Breaker Policy**  
   *Decision:* All inter-service HTTP calls wrapped in `aiobreaker`; failures >5 → open for 30s.

5. **ADR 007 — Hard Timeout Policy**  
   *Decision:* Raven jobs terminate after 10 minutes (configurable) with structured timeout error.

6. **ADR 008 — Streaming-First Inference**  
   *Decision:* Always use `stream=True` for Ollama; gateway streams NDJSON to client. Reduces VRAM peak.

7. **ADR 009 — Automated Quarantine of Unstable Workspace Files**  
   *Decision:* Files failing lint/pytest >3 times in 10 window are flagged `quarantined` until admin review.

**Existing ADRs to Update:**
- ADR 001 (Hardening Slice) — append note: "Job persistence layer added"
- ADR 002 (Branch Push Guardrails) — append: "Push now blocked if lint/pytest fail regardless of branch protection level"

---

## 9. Testing & Linting Guardrails — Checklist

### Unit Tests (pytest)

| Module | Coverage Target | Key Tests |
|--------|----------------|-----------|
| `agent_loop.py` | 80% | Timeout cutoffs, iteration state checkpoint/restore, secret redaction on tool results |
| `background_worker.py` | 75% | Job reclaim logic, heartbeat TTL expiry, DLQ moves |
| `workspace_runtime/main.py` | Already high — add: | Quarantine enforcement, branch auto-creation from protected base |
| `messaging.py` | Already 2 tests — add: | Priority enqueue, job cancellation, lease renewal race |
| `sanitizer.py` (new) | 100% | Pattern matching, nested dict traversal |

### Integration Tests

- `test_raven_hardening.py` + new file `test_raven_lifecycle.py`:
  1. End-to-end: enqueue job → run → timeout → verify cleanup
  2. Crash recovery: kill worker mid-job → restart → resume from checkpoint
  3. Concurrent librarian + raven → verify librarian not blocked
  4. Circuit breaker trip → verify graceful degradation message

### Linting

**Python:** `flake8` with existing config (max-line-length 150). Add `flake8-bugbear` plugin.  
**JS/TS:** `eslint` (already used for workspace files). Ensure `services/ui/` passes.

**Pre-commit Hook Recommendation:**  
Add `.pre-commit-config.yaml`:
```yaml
repos:
  - repo: https://github.com/pre-commit/pre-commit-hooks
    rev: v4.5.0
    hooks:
      - id: trailing-whitespace
      - id: check-yaml
      - id: check-added-large-files
  - repo: https://github.com/psf/black
    rev: 24.2.0
    hooks: [{id: black}]
  - repo: https://github.com/pycqa/flake8
    rev: 7.0.0
    hooks: [{id: flake8}]
```

---

## 10. Risk Mitigation Matrix

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| Raven self-edit corrupts critical code | Medium | Critical | Workspace runtime → lint+pytest mandatory; review branch workflow; auto-quarantine |
| Inference lock starvation | High | High | Librarian decoupled; Raven jobs time-boxed |
| Secret leakage via logs | Medium | Critical | Sanitizer module enforced at all service boundaries |
| Memory leak in long job | Medium | High | Action log capped; per-iteration checkpoint GC |
| Cascade failure from Execution outage | Medium | Medium | Circuit breaker + fallback message |
| Dead-letter queue fills | Low | Medium | Retry backoff; alert human |
| Huge LLM response causes OOM | Medium | High | VRAM guard + streaming + response size cap |

---

## 11. Metrics & Monitoring

**Prometheus-style metrics (expose via `/metrics` on each service):**

```
# Gateway
gateway_requests_total{endpoint="/api/chat",intent="raven"} 152
gateway_request_duration_seconds{endpoint="/api/chat",intent="raven"} 0.87
gateway_inference_lock_contention_total 12
gateway_raven_jobs_active 2
gateway_raven_jobs_completed_total 1047
gateway_raven_jobs_failed_total{reason="timeout"} 5
gateway_raven_jobs_failed_total{reason="exception"} 3

# Execution
execution_tool_calls_total{tool="WorkspaceFileRead"} 2345
execution_tool_duration_seconds{tool="WorkspaceLint"} 0.45
execution_circuit_breaker_state{state="closed"} 1

# Workspace
workspace_lint_total{result="pass"} 987
workspace_lint_total{result="fail"} 43
workspace_pytest_total{result="pass"} 892
workspace_pytest_total{result="fail"} 31
workspace_quarantined_files_current 2
```

**Grafana Dashboard Panels:**
- Raven job queue depth over time
- Inference lock hold time histogram
- Top failing files (pytest)
- Secret redaction count per hour

---

## 12. Implementation Timeline (2-Week Sprints)

**Sprint 1 (Days 1-4):**  
- Slice 1 (Librarian lock decoupling)  
- Slice 3 (Hard timeout)  
- Structured logging + Request ID propagation  
- Unit tests for timeout & tracing  

**Sprint 2 (Days 5-8):**  
- Slice 2 (Job persistence/resumability)  
- Slice 4 (Sanitizer module + log filter)  
- Slice 5 (Circuit breakers)  
- Integration tests for crash recovery  

**Sprint 3 (Days 9-10):**  
- Slice 6 (Streaming-first inference, if VRAM allows)  
- Slice 8 (Quarantine logic)  
- ADR authoring + documentation  
- Full pytests + flake8 compliance  

**Sprint 4 (Days 11-14):**  
- End-to-end validation on staging  
- Load test: 100 concurrent librarian + 1 Raven job  
- Prepare production rollout with feature flag (`RAVEN_HARDENING_SLICE_02_ENABLED`)

---

## 13. Appendix — Code Snippets

### A.1 Per-Tier Inference Lock Refactor

```python
# In gateway/main.py
INFERENCE_LOCK_RAVEN = asyncio.Lock()   # Exclusive, single holder
INFERENCE_LOCK_LIBRARIAN = asyncio.Semaphore(4)  # Up to 4 concurrent

async def get_inference_lock(is_autonomous: bool):
    return INFERENCE_LOCK_RAVEN if is_autonomous else INFERENCE_LOCK_LIBRARIAN
```

### A.2 Agent Loop With Checkpointing

```python
async def AgentLoop(job_id: str, ...):
    checkpoint = await redis.get(f"raven:checkpoint:{job_id}")
    start_iter = 0
    action_log = []
    if checkpoint:
        state = json.loads(checkpoint)
        start_iter = state["iteration"]
        action_log = state["action_log"]
    
    for agent_iter in range(start_iter, MAX_TOOL_ITERATIONS):
        # ... existing logic ...
        
        # At end of iteration (before next LLM call):
        await redis.setex(
            f"raven:checkpoint:{job_id}",
            ttl=3600,
            value=json.dumps({
                "iteration": agent_iter + 1,
                "action_log": action_log[-20:],  # keep last 20
                "last_result": safe_exec_data
            })
        )
```

### A.3 Sanitizer FastAPI Middleware

```python
@app.middleware("http")
async def sanitize_response_middleware(request: Request, call_next):
    response = await call_next(request)
    # Only sanitize JSON bodies
    if response.headers.get("content-type", "").startswith("application/json"):
        body = b""
        async for chunk in response.body_iterator:
            body += chunk
        try:
            data = json.loads(body)
            sanitized = sanitize_dict(data)
            return JSONResponse(content=sanitized, status_code=response.status_code)
        except:
            pass
    return response
```

---

**END OF BLUEPRINT DOCUMENT**

Next steps:  
1. Review this blueprint with human lead  
2. Prioritize slices  
3. Begin implementation in isolated branch `raven/hardening-slice-02`  
4. Once implemented, run full test suite + lint + typecheck before merge
