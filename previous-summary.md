## Objective
- Complete all UI dashboard enhancements and telemetry configuration improvements in SharedLLM

## Important Details
- Repository: `/home/jeremiah/Summers Drive/Code/SharedLLM`
- Branch: `microservices` (up to date with origin)
- Multi-service architecture: 10 services (dns, caddy, identity, ma, rag, storage, logging, control_plane, ui, workspace_runtime)
- Docker only on remote server (192.168.2.205) - SSH required for service checks
- UI service at `services/ui/` with React/TypeScript frontend
- Dashboard uses BentoBox layout with lazy-loaded widgets
- Telemetry system: three-tier architecture (Gateway → Identity Service → Execution Service)
- Telemetry data stored in GlobalSetting table (key-value store in SQLite)
- Three telemetry namespaces: `telemetry_enroll:{entity_id}`, `telemetry_data:{entity_id}`, `telemetry_insight:{entity_id}`
- No dedicated broad telemetry widget exists - EnergyInsightsWidget is the only telemetry-related widget
- Widget store at `services/ui/src/stores/widgetStore.ts` manages visibility, ordering, sizes
- Widget types defined at `services/ui/src/types/widget.ts`

## Work State
### Completed
- **Integration test fixes**:
  - Fixed env var names: `IDENTITY_URL`→`IDENTITY_SVC_URL`, `STORAGE_URL`→`STORAGE_SVC_URL`, `EXECUTION_URL`→`EXECUTION_SVC_URL`, `LOGGING_URL`→`LOGGING_SVC_URL`
  - Fixed REDIS_URL port: `localhost:6399`→`localhost:6379` in 3 files
  - Added `@pytest.mark.local_only` to all integration test classes (8 total)
- **UI lint fixes** (11 errors across 8 files):
  - Removed unused `expect` imports from 7 e2e test files
  - Fixed `any` types in `click-sequence-debug.spec.ts` and `crawler.spec.ts`
  - Removed unused `beforeUrl` variable in `ui-traversal.spec.ts`
  - Added missing `state.isConnected` dependency in `useCallback` in `maWebPlayer.ts`
- **Pydantic v1 deprecation**: Replaced `class Config: extra = "ignore"` with `model_config = ConfigDict(extra='ignore')` in `schemas.py`
- **HTTP client migration** (aiohttp with connection pooling):
  - Created `services/execution/http_client.py` with session pooling and timeout config
  - Migrated `nextcloud_client.py` from `requests` to `aiohttp`
  - Migrated `personal_data.py` from `requests` to `aiohttp`
  - Migrated `handlers/note.py`, `handlers/storage.py`, `handlers/talk.py`, `shim_users.py`, `gateway/ha_state_cache.py` to aiohttp
  - Removed urllib3 HTTP/3 disable patch from `main.py`
  - Updated PersonalDataProvider Protocol to use async methods
- **Talk handler test fixes**: Updated `provider.request`, `provider.upload_file`, `provider.ensure_directory` mocks to use `AsyncMock` instead of synchronous return values
- **Linting verified**: ruff check passes cleanly for all services
- **Commit & push**: Commit `b96ddaee` on branch `microservices` (talk handler test fixes)
- **All CI workflows passing**: Build & Push Images ✓, SOA Microservices CI ✓, SharedLLM E2E Pipeline ✓
- **Dashboard review findings**:
  - Settings button in WidgetContextMenu handles per-widget preferences (gear icon click)
  - WidgetContextMenu uses right-click/long-press for size options
  - Widget store syncs settings server-side via `syncWithServer()`
  - EnergyInsightsWidget fetches telemetry via `api.getTelemetryEnrollments()` and `api.getTelemetrySummary(entityId)`
- **UI enhancements applied**:
  - Replaced ⚙ emoji gear icon with lucide-react Settings2 icon in WidgetContextMenu
  - Improved telemetry error messaging in EnergyInsightsWidget with actionable messages for 404, connection refused, and generic errors
- **Global dashboard settings created**:
  - Created `DashboardSettingsPanel.tsx` component with:
    - Widget catalog (show/hide/toggle pins)
    - Import/export settings as JSON
    - Reset to defaults functionality
  - Integrated into Dashboard.tsx page
  - Added settings trigger button in dashboard header
- **Telemetry admin panel created**:
  - Created `TelemetryAdminPanel.tsx` component with:
    - Device enrollment form with entity search
    - Toggle checkboxes for power/availability/usage tracking
    - Offline alert threshold configuration
    - Group ID input for related device grouping
    - Enrolled devices list with expandable details
    - Edit enrollment settings (toggle tracking types, change threshold)
    - View data/summary/insights for enrolled entities
    - Trigger manual snapshot button
    - Unenroll device functionality
    - Summary stats (total devices, power tracking count, availability count, usage count, estimated data points)
  - Integrated into Admin.tsx page under telemetry tab
- **All lint errors fixed**: DashboardSettingsPanel.tsx and TelemetryAdminPanel.tsx pass ESLint

### Active
- None

### Blocked
- None

## Next Move
1. Commit the new telemetry admin panel and UI enhancements
2. Push to `microservices` branch
3. Verify CI workflows pass (Build & Push Images, SOA Microservices CI, SharedLLM E2E Pipeline)
4. Test telemetry admin panel functionality via SSH to 192.168.2.205

## Relevant Files
- `services/execution/http_client.py` - New shared aiohttp client with connection pooling
- `services/execution/nextcloud_client.py` - Migrated to aiohttp (complete)
- `services/execution/personal_data.py` - Migrated to aiohttp (complete)
- `services/execution/handlers/note.py` - Migrated to aiohttp (complete)
- `services/execution/handlers/storage.py` - Migrated to aiohttp (complete)
- `services/execution/handlers/talk.py` - Migrated (complete)
- `services/execution/main.py` - Removed urllib3 patch (complete)
- `services/execution/schemas.py` - Pydantic fix applied (complete)
- `services/ui/e2e/*.spec.ts` - UI lint fixes applied (8 files, complete)
- `services/ui/src/lib/maWebPlayer.ts` - React fix applied (complete)
- `services/shim_users.py` - Migrated to aiohttp (complete)
- `services/gateway/ha_state_cache.py` - Migrated to aiohttp (complete)
- `tests/integration/test_service_health_and_crud.py` - Fixed (env vars + markers)
- `tests/integration/test_execution_and_intent.py` - Fixed (env vars + markers)
- `tests/integration/test_redis_queues_and_logging.py` - Fixed (env vars + markers)
- `services/ui/src/pages/Dashboard.tsx` - Main dashboard page (updated with settings panel)
- `services/ui/src/components/dashboard/BentoBoxDashboard.tsx` - Grid layout component
- `services/ui/src/components/widgets/WidgetCard.tsx` - Widget wrapper
- `services/ui/src/components/widgets/WidgetContextMenu.tsx` - Context menu (updated with Settings2 icon)
- `services/ui/src/components/widgets/EnergyInsightsWidget.tsx` - Telemetry widget (updated error messaging)
- `services/ui/src/components/dashboard/DashboardSettingsPanel.tsx` - New global settings panel (created)
- `services/ui/src/components/settings/TelemetryAdminPanel.tsx` - New telemetry admin panel (created)
- `services/ui/src/pages/Admin.tsx` - Admin page (updated with TelemetryAdminPanel)
- `services/ui/src/stores/widgetStore.ts` - Widget state management
- `services/ui/src/types/widget.ts` - Widget type definitions
- `services/ui/src/services/api.ts` - API service (telemetry endpoints)
- `services/ui/src/types/api.ts` - API type definitions (TelemetryEnrollment, TelemetrySummary, etc.)