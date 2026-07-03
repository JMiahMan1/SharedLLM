# AGENTS.md

## CRITICAL RULES

### NO LOCAL DOCKER
- **NEVER EVER** run Docker commands locally on your machine
- **ONLY** run Docker commands on the remote server: `192.168.2.205`
- Use `ssh jeremiah@192.168.2.205 "docker compose ..." ` for Docker operations
- This includes: `docker build`, `docker run`, `docker compose up`, `docker compose stop`, etc.
- Violating this will crash everything - Docker does not run locally

### NO HARDCODED IPs
- **NEVER** hardcode IP addresses like `192.168.1.1`, `192.168.2.205`, etc.
- Use network discovery to find IPs dynamically
- DNS sync service discovers network configuration via Docker API
- Services should use hostnames or discovered gateway IPs

## Deployment Workflow

### Build and Deploy Process

1. **Commit and Push**: All changes are pushed to the `microservices` branch
2. **Wait for Build**: Use `gh run list --branch=microservices --limit=5` to verify build completion before deploying
3. **Verify Build Status**: Check that the build workflow shows "completed" status (not just "running")
4. **Deploy**: Run `./scripts/deploy_remote.sh jeremiah@192.168.2.205` to pull latest images and start containers

### Critical Rules

- **NEVER** run deploy before build completes
- **ALWAYS** verify `gh run list` shows "completed" status before deploying
- **ALWAYS** read AGENTS.md after every compaction to stay updated on current workflows
- All work goes to `microservices` branch only (never push `main` unless explicitly requested)

### Build Workflow

- Workflow file: `.github/workflows/build-images.yml`
- Triggered on: push to `main`, `master`, or `microservices` branches
- Detects changed services and builds only those (plus base if changed)
- Uses `latest` tag for GitHub Container Registry (GHCR)

### Deploy Script

- Script: `scripts/deploy_remote.sh`
- Usage: `./scripts/deploy_remote.sh [user@machine_ip]`
- Default path: `/home/jeremiah/SharedLLM`
- Actions: git fetch → pull → docker compose pull → docker compose up

## Testing Workflow

### Playwright E2E Tests

- Test file: `services/ui/e2e/web-player-sendspin.spec.ts`
- Login function: `loginAsDefault(page)` - handles biometric auth fallback
- Test user: `testuser` with credentials from environment variables
- **IMPORTANT**: Always use `page.on('websocket')` listener instead of `waitForEvent('websocket')` - events may fire before listener is attached

### MA Credentials Fallback

- Users without MA credentials fall back to admin (ID 1) credentials
- Fallback logic in: `services/identity/main.py`
- Admin credentials are decrypted from `mass_token_enc` field
- Logs show: `[resolve] Returning credentials for user={username}, mass_token={set|NOT SET}`

## Key Services

- **Identity Service**: Resolves user credentials (port 8001)
- **Gateway**: Proxies requests to MA/ABS, handles sendspin protocol (port 11435)
- **UI**: Web player interface (port 8080)
- **Caddy**: Reverse proxy in front of services
- **MA Server**: Music Assistant at `https://ha.sumemail.com:8095`

## Common Issues

- WebSocket events may fire before `waitForEvent` listener is attached → use `page.on('websocket')` instead
- Identity service logs show `mass_token=NOT SET` when fallback fails → check if `sys_user.mass_token_enc` exists
- Deploy script may pull old images if build hasn't completed → always verify build status first
