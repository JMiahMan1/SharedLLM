# Media Player & Development Workflow Status

## Media Player Status: OPERATIONAL

### Test Results
- All 8/8 Playwright live tests passing against production (192.168.2.205:8080)
- Confirmed: login, auto-connect, Sendspin WebSocket, JSON-RPC WebSocket, player initialization
- MA events received: player_updated for Sendspin JS Client + 5 other devices

### Fixes Deployed (microservices branch)
1. **Sendspin infinite reconnect loop** (commit d5015731)
   - Added `connectAttemptedRef` guard to Media.tsx auto-connect useEffect
   - Prevents rapid reconnect cycles when connection fails

2. **SendspinPlayer import runtime error** (commit bef230a3)
   - Changed `import type { SendspinPlayer }` to `import { SendspinPlayer, type PlayerState }`
   - Fixes minified ReferenceError in production build

### Connection Flow
1. User navigates to `/media` page
2. Auto-connect triggers on mount (if localMode active)
3. `maPlayer.connect()` → `initPlayer()` → Sendspin WebSocket → JSON-RPC WebSocket
4. Both WS connections established → Player initialized
5. Volume synced, MA events received, ready to play media

### Current Architecture
- **UI** (sharedllm_ui): Served via Caddy proxy on port 8080
- **Gateway** (sharedllm_gateway): Listens on 11435, handles Sendspin WebSocket + identity resolution
- **Identity** (sharedllm_identity): User/device/API key management
- **Caddy** (sharedllm_caddy): Reverse proxy (8080→UI, WebSocket upgrade for `/api/sendspin`)
- **MA** (Music Assistant): https://ha.sumemail.com:8095

---

## Development Workflow

### Branch Strategy
- Only two branches: `main` and `microservices`
- All feature work goes to `microservices`
- `main` is never pushed to unless explicitly requested
- Deleted branches: `timer`, `main2`, `annoucements`

### Deployment Pipeline
1. Push to `microservices` → GitHub Actions CI builds
2. CI publishes to `ghcr.io/jmiahman1/sharedllm-ui:latest`
3. Verify deployment: `gh api repos/{repo}/commits/{sha}` → `gh run list` → `docker inspect` digest
4. Deployment confirmed via container image digest match

### Control Plane API
- `GET /api/admin/services/updates` — compare local images to registry
- `POST /api/containers/{service}/pull` — trigger docker pull
- `POST /api/restart/{service}` — trigger docker restart
- Requires header: `X-Internal-Secret: RAVEN_SECURE_2026`

### Credentials & Secrets
- `.env` — real production creds, ignored by git
- `.env.test` — local test credentials (JARVIS_USER, JARVIS_PASS), ignored by git
- `.env.test.example` — empty template for reference, committed to git
- `DEFAULT_ADMIN_PASSWORD=changeme` in `.env`
- Never hardcode usernames/passwords in code

### Testing
- **Playwright test**: `scripts/test-media-player.js`
  - Run: `NODE_PATH="services/ui/node_modules" node scripts/test-media-player.js --user USERNAME --pass PASSWORD --host URL`
  - Tests: login, media page, play button, initPlayer, WebSocket connection, UI state
  - Credentials from `.env.test` or CLI args

### Rules
- No SCP/docker cp to production
- No hardcoded credentials
- Always verify changes (docker logs, container inspection, CI results)
- Trust CI unless proven otherwise
- Direct tool calls only — no subtasks or delegation
- Max 3 retries per tool call before stopping
