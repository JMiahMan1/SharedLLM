# UI/UX Integration Plan for Raven Stabilization

## Current State Assessment

### Existing UI Structure (services/ui/)

```
src/
├── App.tsx                    # Router + global layout
├── main.tsx                   # Entry point
├── services/
│   └── api.ts                 # Axios client (569 lines) — all endpoints
├── context/
│   └── AuthContext.tsx        # Auth state
├── components/
│   ├── layout/
│   │   ├── Sidebar.tsx        # Navigation with icon set
│   │   └── Header.tsx         # User info + theme toggle
│   ├── ui/
│   │   ├── Modal.tsx
│   │   └── HelpTooltip.tsx
│   └── MarkdownViewer.tsx
├── pages/
│   ├── Dashboard.tsx          # Home / device overview
│   ├── Admin.tsx              # User mgmt, system ops
│   ├── Identity.tsx           # Identity connections
│   ├── Communication.tsx      # Notes, calendar, talk
│   ├── JarvisLab.tsx          ← **Closest to Raven ops**
│   ├── KnowledgeHub.tsx       # RAG search, docs
│   ├── Workspaces.tsx         # Git workspace manager
│   └── Docs.tsx               # Markdown doc viewer
└── test/                      # Vitest tests
```

**Technology Stack:**
- React 19.2 + TypeScript
- Vite + React Router DOM v7
- TanStack Query v5 (data fetching & caching)
- Axios (HTTP client)
- Tailwind CSS v4 + custom glassmorphism UI
- Zustand? (no, uses React Query + context)

**Observability Gap:**  
The JarvisLab page shows:
- Health of all services (polling every 5s)
- Workspace list
- Smoke/unit test execution buttons
- Raw log tail via WebSocket (`/api/logs/stream`)

**Missing for Raven:**
- No real-time job queue visibility
- No per-job progress (iterations, action log)
- No circuit breaker status per service
- No inference lock contention metrics
- No streaming response viewer for Raven's work
- No job history / audit trail UI

---

## UI Refactoring Goals (Modular Architecture)

### Phase 1 — Backend-First (This Sprint)
Prepare backend endpoints for UI consumption. No frontend rewrite.

**Needed endpoints (new in gateway/main.py):**

1. **Raven Job Status API**
```
GET /api/raven/jobs?status=processing&limit=20
Response: {
  jobs: [
    {
      job_id: string,
      user_id: string,
      status: "queued"|"processing"|"completed"|"failed"|"timeout",
      iteration: 7,
      created_at: epoch,
      started_at?: epoch,
      duration_seconds?: float,
      last_action: "Step 6: WorkspaceFilePatchRequest -> Updated agent_loop.py",
      current_task?: string  // what the agent is working on right now
    }
  ],
  queue_depth: { high: 0, normal: 2, low: 0 },
  inference_lock_held_by?: string | null,
  circuit_breaker_states: {
    execution: "closed" | "open" | "half-open",
    workspace: "...",
    rag: "...",
    storage: "...",
  }
}
```

2. **Raven Job Detail (with streaming chunks)**
```
GET /api/raven/jobs/{job_id}
Response includes:
  - Full checkpoint data (iteration, action_log, result_summary)
  - Streaming chunks accumulated so far (if any)
  - Heartbeat history timestamps
```

3. **Raven Metrics (Prometheus or JSON)**
```
GET /api/raven/metrics
Returns application-level metrics for dashboard:
  - jobs_total{status}
  - jobs_duration_seconds{p50,p95,p99}
  - inference_lock_wait_seconds
  - circuit_breaker_failures_total{target}
  - timeouts_total
```

4. **Raven Control (admin only)**
```
POST /api/raven/jobs/{job_id}/cancel   → moves job to FAILED with "cancelled" error
POST /api/raven/jobs/{job_id}/replay   → re-queue failed job for retry
DELETE /api/raven/quarantine            → clear all quarantined files
```

**Implementation location:** Add to `gateway/main.py` near health endpoints.

---

## Phase 2 — Raven Operations Dashboard (new page: `/lab/raven`)

Augment existing JarvisLab with Raven-specific monitoring.

**New sub-tab:** `raven` alongside `overview`, `tests`, `logs`

### Dashboard Sections

#### A) Job Queue Monitor (Top Panel)

A Kanban-style board or table:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ ACTIVE RAVEN JOBS                    QUEUE DEPTH: 3    LOCK: Free (0s wait) │
├───────────────┬──────────────┬───────────────┬───────────────────────────────┤
│ Job ID        │ User         │ Iter  │ State   │ Action                      │
│ abc123        │ jeremiah     │ 7/30  │ Running │ Patching agent_loop.py      │
│ def456        │ admin        │ 2/30  │ Queued  │ waiting for lock...         │
│ …             │ …            │ …     │ …       │ …                           │
└───────────────┴──────────────┴───────┴─────────┴───────────────────────────────┘
```

**Columns:** Job ID (truncated), User, Iteration (progress), State (badge), Current Action (last tool name).

**Actions (per job):**
- 🔍 Click row → opens **Job Inspector** modal (full logs, action timeline, result)
- ⏹ Cancel (admin only)
- 🔄 Replay (failed jobs)

#### B) Live Agent Trace (Bottom Panel — Collapsible)

Shows **streaming output** of the currently active Raven job in real-time, similar to Tailwind CSS build logs.

```
[2026-05-11 22:15:42] Job abc123 — Iteration 7/30
  ▶ Iteration 6: WorkspaceFilePatchRequest → SUCCESS (423ms)
  ▶ Tool: DockerLogsRequest — fetching logs from sharedllm_gateway...
  ← LLM: "I see the error. Applying patch to fix timeout logic..."
  → Payload: {"action":"WorkspaceFilePatchRequest","payload":{...}}
  ← Response: {"status":"SUCCESS","message":"File patched","sha256":"a1b2..."}
```

Auto-scroll enabled. Color coding:
- Blue = agent reasoning
- Green = tool success
- Red = tool failure
- Yellow = warning (timeout, retry)

---

### Phase 3 — Workspace File Health & Quarantine Panel

Integrate into Workspaces page:

```
Workspace: /workspace/SharedLLM
  ├─ services/gateway/agent_loop.py  [✅ Lint OK] [✅ Tests 12/12]
  ├─ services/execution/main.py      [⚠ Lint fail: E302]  ← click for details
  └─ …
```

Add red badge with count: "Quarantined: 2" → opens quarantine list view with reason and admin override button.

---

### Phase 4 — Configuration UI for Raven Settings

Expose new environment flags via Admin page:

```
[ ] Enable hard timeout (currently 600s)
[✓] Use streaming inference (reduces VRAM)
[ ] Enable circuit breakers
    • Execution: threshold=5, timeout=30s
    • Workspace: threshold=5, timeout=30s
    • RAG: threshold=3, timeout=10s
[ ] Flash attention / GPU optimizations
```

Editable via toggles → calls `POST /api/settings/bulk` → persisted to Identity DB → live hot-reload in running services.

---

## Information Architecture — Component Breakdown

### New Components to Create

| Component | Location | Purpose |
|-----------|----------|---------|
| `RavenJobMonitor` | `components/raven/JobMonitor.tsx` | Top-level queue table + refresh |
| `RavenJobCard` | `components/raven/JobCard.tsx` | Single job row with status badge |
| `RavenJobInspector` | `components/raven/JobInspector.tsx` | Modal: full timeline, chunks, state |
| `RavenLiveTrace` | `components/raven/LiveTrace.tsx` | Real-time streaming log viewer |
| `CircuitBreakerPanel` | `components/raven/CircuitBreakerPanel.tsx` | Health of downstream services |
| `QuarantinePanel` | `components/raven/QuarantinePanel.tsx` | List of quarantined files + admin actions |
| `RavenMetricsChart` | `components/raven/MetricsChart.tsx` | Charts: job duration, wait time, timeouts |

### Routing Changes

```tsx
// App.tsx — add route
<Route path="/lab/raven" element={<ProtectedRoute requireAdmin={true}><RavenDashboard /></ProtectedRoute>} />
```

Update JarvisLab tabs to include `raven`:

```tsx
const tabs = [
  ['overview', 'Overview'],
  ['raven', 'Raven Ops'],
  ['tests', 'Tests'],
  ['logs', 'Logs'],
] as const;
```

---

## Data Flow & State Management

**React Query hooks** to encapsulate data fetching:

```typescript
// hooks/useRavenJobs.ts
export const useRavenJobs = (status?: string) => {
  return useQuery({
    queryKey: ['raven-jobs', status],
    queryFn: () => api.getRavenJobs(status),
    refetchInterval: 3000,  // Update every 3s for active jobs
  });
};

// hooks/useRavenMetrics.ts
export const useRavenMetrics = () => {
  return useQuery({
    queryKey: ['raven-metrics'],
    queryFn: () => api.getRavenMetrics(),
    refetchInterval: 10000, // Slow-refresh
  });
};
```

---

## API Extension Plan (gateway/main.py)

Add these routes to `gateway/main.py` near the health endpoints:

```python
@app.get("/api/raven/jobs")
async def get_raven_jobs(
    status: Optional[str] = None,
    limit: int = 20,
    x_internal_secret: Optional[str] = Header(default=None),
):
    _require_internal_secret(x_internal_secret)
    # Fetch from Redis: processing + queued lists
    # Return structured data for UI
    ...

@app.get("/api/raven/jobs/{job_id}")
async def get_raven_job_detail(job_id: str, ...):
    # Return checkpoint + action_log + accumulated chunks
    ...

@app.get("/api/raven/metrics")
async def get_raven_metrics(...):
    # Return dict of counters, gauges, histograms
    # Could integrate with prometheus_client if desired
    ...

@app.post("/api/raven/jobs/{job_id}/cancel")
async def cancel_raven_job(job_id: str, ...):
    # Mark job as failed, requeue if needed
    ...

@app.delete("/api/raven/quarantine")
async def clear_quarantine(workspace_id: Optional[str] = None, ...):
    # Clear quarantine flags; admin only
    ...
```

---

## Implementation Sequence (UI)

1. **Week 1:** backend endpoints for job status/metrics (non-blocking, parallel to Slice 1-3 backend work)
2. **Week 2:** `RavenJobMonitor` + `RavenJobCard` components (basic table)
3. **Week 2:** `RavenLiveTrace` component — WebSocket or SSE viewer
4. **Week 3:** `CircuitBreakerPanel` and quarantine UI
5. **Week 4:** Admin controls and settings UI
6. **Week 4:** Styling polish + testing + accessibility audit

---

## Styling & Theming

The UI uses **glassmorphism** design:
- Translucent backgrounds: `bg-white/5`, `bg-slate-950`
- Borders: `border border-white/10`
- Fonts: `font-mono text-xs` for logs/data
- Accent colors: `indigo-600` (primary), `emerald-300` (success), `red-300` (error)

New Raven components should use same tokens:
- Job status badges: 
  - `bg-blue-500/20 text-blue-300` = processing
  - `bg-yellow-500/20 text-yellow-300` = queued
  - `bg-emerald-500/20 text-emerald-300` = completed
  - `bg-red-500/20 text-red-300` = failed/timeout

---

## Testing Strategy

**Unit tests (Vitest):** each component renders without crash, handles loading/error states.
**Integration tests (Playwright):** 
- JarvisLab loads Raven tab
- Live trace updates in real-time
- Cancel button appears for admin and actually cancels job

Place tests alongside components or in `test/` mirroring structure.

---

## Metrics & Alerting Integration

Backend exposes `/api/raven/metrics`. UI charts can use:
- `recharts` library (already common in React ecosystem) or
- Simple CSS bar charts to avoid dependency bloat

JarvisLab → Raven tab will display:
- Job throughput (jobs/hour)
- Average iteration count
- Top failing actions (bar chart)
- Lock contention rate (if >10% of requests wait >30s → highlight in red)

---

## Backward Compatibility

All new backend endpoints:
- Require `X-Internal-Secret` header (same as other admin endpoints)
- Check `is_admin` from resolved identity when applicable
- Do not modify existing API responses → no breaking changes

---

## Success Metrics

After UI integration:
- Human operator can determine Raven's status at a glance (<2s)
- Drilling into a running job reveals exact iteration history
- Quarantined files visible and clearable without DB access
- Circuit breaker status visible before troubleshooting
- Job cancellation reduces wasted resources during runaway

---

## Next Steps

1. **Immediate:** Implement backend `/api/raven/jobs` and `/api/raven/metrics` in `gateway/main.py`
2. **Week 1:** Build `RavenJobMonitor` table in isolation
3. **Week 1:** Add WebSocket endpoint `/api/raven/jobs/{job_id}/stream` for live traces
4. **Week 2:** Integrate with existing JarvisLab layout
5. **Week 2-3:** Iterate on UX with human operator
6. **Week 4:** Full E2E tests + documentation
