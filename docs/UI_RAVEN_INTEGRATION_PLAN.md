# UI/UX Integration Plan for Raven Stabilization

## Last Updated: 2026-05-15

## Current State (Completed)

### Architecture

Raven Ops has been fully integrated into the Admin page (`/admin`) with a **tabbed layout** that separates admin concerns:

|Tab|Content|Admin Only|
|---|-------|----------|
|Users & Devices|User CRUD, Discovery Import, Device Assignments|✅ Yes|
|Raven Ops|Sentinel control, triage queue, active missions, live trace, audit log|✅ Yes|
|LLM & Settings|LLM model config, Global Settings|✅ Yes|
|Database & Audit|RAG stats, collection inspection, service log audit trail|✅ Yes|

### Component Inventory

|Component|Location|Purpose|Status|
|---------|--------|---------|------|
|`RavenOpsPanel`|`components/settings/RavenOpsPanel.tsx`|Master control panel: suspend/resume, scan frequency, error threshold, TTS config, triage queue, active missions|✅ Done|
|`RavenAuditLog`|`components/settings/RavenAuditLog.tsx`|Modal showing historical completed/failed missions with execution logs and results|✅ Done|
|`RavenLiveTrace`|`components/settings/RavenLiveTrace.tsx`|WebSocket streaming terminal for live mission telemetry|✅ Done|
|`Admin.tsx`|`pages/Admin.tsx`|Tabbed admin layout (Users, Raven, Settings, Database)|✅ Done|

### Existing Backend Endpoints

|Method|Path|Function|Status|
|------|----|--------|------|
|GET|`/api/admin/raven/config`|Get Raven config (suspend, interval, threshold)|✅ Working|
|PATCH|`/api/admin/raven/config`|Update Raven config|✅ Working|
|GET|`/api/admin/raven/tts/voices`|List available TTS voices|✅ Working|
|GET|`/api/admin/raven/queue`|Get pending + active missions for triage|✅ Working|
|POST|`/api/admin/raven/queue/{id}/execute`|Dispatch pending mission|✅ Working|
|GET|`/api/raven/missions`|List all missions (user-facing)|✅ Working|
|POST|`/api/raven/missions`|Create user mission|✅ Working|
|GET|`/api/raven/missions/{id}`|Get mission detail|✅ Working|
|POST|`/api/raven/missions/{id}/kill`|Kill running mission|✅ Working|
|PATCH|`/api/raven/missions/{id}`|Update mission|✅ Working|
|WS|`/api/raven/missions/{id}/stream`|WebSocket live stream|✅ Working|

### RavenOpsPanel Capabilities

**Sentinel Control:**

- Suspend/Resume background worker (`raven_suspended`)
- Scan frequency: 1min, 5min, hourly, daily
- Error threshold adjustment

**Local TTS Hardware:**

- Default engine display (kokoro)
- Voice style selector
- Kokoro model provisioning (320MB download)

**Pending Triage Queue:**

- Lists `admin_fix` missions with `status === 'pending'`
- Shows target container, error summary, detection timestamp
- "Run Fix Now" button to dispatch

**Active Missions Monitor:**

- Shows missions with `status === 'running' | 'executing' | 'queued'`
- Progress bar, mission type, dispatch timestamp
- "Watch" button → opens RavenLiveTrace modal
- "Stop" button → kills mission

**Audit Log Modal:**

- Lists completed/failed missions sorted by date
- Click to view execution log (color-coded: reasoning=blue, action=yellow, success=green, error=red)
- Shows final result payload

**Live Trace Modal:**

- WebSocket connection to `/api/raven/missions/{id}/stream`
- Auto-scrolling terminal output
- Connection status indicator
- Color-coded log types

### API Service Methods (`services/api.ts`)

```typescript
// Raven admin endpoints
getRavenConfig() → RavenConfig
updateRavenConfig(config) → { status }
getRavenVoices() → { voices[] }
downloadRavenModels() → { results[] }
getAdminRavenQueue() → RavenMission[]
executeAdminRavenMission(id) → { status, message }

// Raven user endpoints
getUserMissions() → RavenMission[]
createUserMission(query, priority) → { status, mission }
killRavenMission(id) → { status, message }
```

### Data Types

```typescript
interface RavenMission {
  id: number;
  mission_type: string;        // 'admin_fix' | 'user_task' | 'media_conversion'
  priority: number;
  target_container?: string | null;
  error_summary?: string | null;
  proposed_mission: string;
  coding_model?: string | null;
  status: string;              // 'pending' | 'scheduled' | 'executing' | 'completed' | 'failed' | 'dismissed'
  progress: number;            // 0-100
  scheduled_for?: string | null;
  created_at: string;
  output_log?: string | null;  // JSON array of log entries or raw string
  result?: string | null;
  user_id?: number | null;
}

interface RavenConfig {
  raven_suspended: boolean;
  raven_scan_interval: number;   // seconds
  raven_error_threshold: number;
  active_coding_model: string | null;
  system_default_tts_voice: string;
  system_default_tts_engine: string;
}
```

## What's Working

- ✅ RavenOpsPanel fully integrated into Admin page (tabbed layout)
- ✅ Sentinel suspend/resume toggle
- ✅ Dynamic scan frequency configuration
- ✅ Error threshold control
- ✅ TTS voice selection and model provisioning
- ✅ Pending triage queue with dispatch control
- ✅ Active missions monitor with progress bars
- ✅ Live WebSocket trace for running missions
- ✅ Audit log modal for historical missions
- ✅ Kill mission functionality
- ✅ RBAC: Admin page requires `is_admin`
- ✅ Sidebar filters admin-only routes for non-admin users

## What's Next (Planned)

### Phase 2 — User-Facing Raven Dashboard

- Non-admin users should see a simplified view of their own missions
- No config changes, no dispatch control, no kill capability
- Read-only mission status and log viewing

### Phase 3 — Workspace Quarantine Integration

- Quarantine badges in Workspaces.tsx for broken workspaces
- Rollback button to revert last Raven patch via git history

### Phase 4 — Enhanced Observability

- Mission duration metrics
- Top failing actions chart
- Lock contention rate display
- Inference queue depth indicator

## Test Coverage

### Backend Tests

- `services/tests/test_raven_hardening.py` — Queue reclaim, dead letter, secret sanitization, shell blocklist, git safety
- `services/tests/test_raven_routing.py` — Model/prompt routing for Raven queries
- `services/tests/test_raven_timeout_behavior.py` — Timeout thresholds, heartbeat scheduling
- `services/gateway/tests/test_raven_streaming.py` — WebSocket stream endpoint
- `services/gateway/tests/test_talk_monitor.py` — Talk monitor logic

### Frontend Tests

- `services/ui/src/pages/Admin.test.tsx` — Admin page rendering, user CRUD, device assignment
- `services/ui/src/services/api.test.ts` — API service unit tests

### CI Configuration

- `soa_tests.yml` — Runs Raven tests in GitHub Actions with `qwen3:8b` assistant model, `qwen2.5-coder:7b` coding model
- `ui-tests.yml` — Runs Vitest tests for UI components

## Workspace Path Configuration

Workspace paths are **never hardcoded**. They are:

- Configured by users in the UI via workspace settings
- Resolved per-request from the workspace registry
- `WORKSPACE_ROOT` defaults to empty string in execution handlers (must be set via env or resolved from workspace config)
- Tests use `tmp_path` fixtures to isolate workspace operations
- Live testing uses `~/workspace` on the server (user-owned directory)

## Model Configuration

- **Assistant model:** `qwen3:8b` (tested, stable)
- **Coding model:** `qwen2.5-coder:7b` (proven for code tasks)
- **Librarian model:** `qwen3:8b`
- Models are configurable in the UI under LLM & Settings tab
