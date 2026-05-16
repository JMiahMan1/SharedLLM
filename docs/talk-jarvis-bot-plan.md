# Nextcloud Talk Jarvis Bot — Feature Plan

## Goal
Replace the fragile polling-based `@jarvis` mention detector with a configurable, per-conversation Jarvis bot for Nextcloud Talk that integrates with the same LLM chat backend used by the web UI.

## Current State (Broken)
- Polling loop in `background_worker.py` (10s interval)
- Detects `@jarvis` via string matching
- Uses user account credentials (not a bot identity)
- Multiple failure points: credential resolution, room listing, message fetching, Redis dedup
- No UI to enable/disable per conversation
- Tests are skipped

## Architecture

### Phase 1: Native Talk Bot Registration
- Register a proper bot via Nextcloud Talk Bot API (`/ocs/v2.php/apps/spreed/api/v1/bot`)
- Bot gets its own identity in Talk (not impersonating a user)
- Store bot ID and API token in Identity service settings
- One-time registration script or UI button

### Phase 2: Webhook Receiver
- Add webhook endpoint in gateway: `POST /api/talk/webhook`
- Nextcloud POSTs events to this URL when messages arrive
- Parse event payload, detect bot mentions
- Enqueue job into existing inference queue (reuse `_talk_token` flow)
- Real-time, no polling delay

### Phase 3: Per-Conversation Toggle (UI)
- Add "Jarvis Bot" toggle to each Talk conversation in the UI
- Store enabled/disabled state per conversation token in Identity or Redis
- Webhook checks toggle before enqueuing jobs
- Global on/off switch in LLM Settings page

### Phase 4: Unified Chat Backend
- Both Talk bot and web UI chat use the same `process_full_orchestration` pipeline
- Session/history management via Redis (keyed by conversation token or user session)
- Conversation context persists across platforms
- Future: streaming responses in Talk (via chunked messages)

## Key Files to Modify

| File | Change |
|------|--------|
| `services/gateway/main.py` | Add `POST /api/talk/webhook` endpoint |
| `services/gateway/background_worker.py` | Deprecate `_talk_monitor_loop`, keep `_trigger_talk_callback` |
| `services/execution/handlers/talk.py` | Add bot registration, use bot API token for replies |
| `services/identity/models.py` | Store bot ID, token, per-conversation toggles |
| `services/ui/src/components/settings/LLMSettings.tsx` | Add global Jarvis Bot toggle |
| `services/ui/src/pages/Communication.tsx` | Add per-conversation Jarvis toggle |

## Configuration Settings
- `jarvis_talk_bot_enabled` (global on/off)
- `jarvis_talk_bot_id` (registered bot ID)
- `jarvis_talk_bot_secret` (bot API token)
- `jarvis_talk_conversations` (map of token → enabled/disabled)

## Fallback
- Keep existing polling as fallback when bot is not registered
- Auto-detect if webhook is unreachable and switch back to polling
