#!/usr/bin/env bash
# =============================================================================
# SharedLLM Deploy Script
# Runs after every successful git pull to restart the application stack.
# Can also be run manually: bash scripts/deploy.sh
# Server: ai.local (ai.local)
# =============================================================================
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_FILE="$REPO_DIR/data/deploy.log"
COMPOSE="docker compose"

# Ensure log dir exists
mkdir -p "$REPO_DIR/data"

log() {
    local msg="[$(date '+%Y-%m-%d %H:%M:%S')] $*"
    echo "$msg"
    echo "$msg" >> "$LOG_FILE"
}

cd "$REPO_DIR"

log "========================================="
log "SharedLLM Auto-Deploy Started"
log "Branch: $(git rev-parse --abbrev-ref HEAD)"
log "Commit: $(git rev-parse --short HEAD)"
log "========================================="

# --- Step 1: Check what changed ---
CHANGED_FILES=$(git diff --name-only HEAD@{1} HEAD 2>/dev/null || true)
CODE_CHANGE=$(printf '%s\n' "$CHANGED_FILES" | grep -E "^services/|^docker-compose|^scripts/|^Dockerfile" || true)
CADDY_CHANGE=$(printf '%s\n' "$CHANGED_FILES" | grep -E "^Caddyfile$" || true)
NEEDS_REBUILD=false

if [ -n "$CODE_CHANGE" ]; then
    NEEDS_REBUILD=true
    log "Code or infra changes detected — full rebuild required."
else
    log "No infrastructure changes — fast restart only."
fi

if [ -n "$CADDY_CHANGE" ]; then
    log "Caddy configuration change detected."
fi

# --- Step 2: Restart or Rebuild ---
if [ "$NEEDS_REBUILD" = true ]; then
    log "Running: $COMPOSE up -d --build"
    $COMPOSE up -d --build 2>&1 | tee -a "$LOG_FILE"
else
    log "Running: $COMPOSE restart"
    $COMPOSE restart 2>&1 | tee -a "$LOG_FILE"
fi

if [ -n "$CADDY_CHANGE" ]; then
    log "Running: $COMPOSE restart caddy"
    $COMPOSE restart caddy 2>&1 | tee -a "$LOG_FILE"
fi

log "Waiting 15s for SOA stack to initialize..."
sleep 15

# --- Step 3: Verify the API is healthy inside the Docker boundary ---
# Using docker exec ensures we hit the container internally, avoiding port blockages.
HEALTH_URL="http://localhost:11435/health/ready"
log "Checking SOA Readiness at $HEALTH_URL via Docker internal network..."

MAX_ATTEMPTS=12
ATTEMPT=0
HEALTHY=false

while [ $ATTEMPT -lt $MAX_ATTEMPTS ]; do
    ATTEMPT=$((ATTEMPT + 1))
    
    # Execute the curl command INSIDE the gateway container
    HTTP_CODE=$($COMPOSE exec -T gateway curl -s -o /dev/null -w "%{http_code}" "$HEALTH_URL" 2>/dev/null || echo "000")
    
    if [ "$HTTP_CODE" = "200" ]; then
        HEALTHY=true
        log "API healthy (HTTP $HTTP_CODE) after attempt $ATTEMPT."
        break
    fi
    log "Attempt $ATTEMPT/$MAX_ATTEMPTS: API not ready yet (HTTP $HTTP_CODE). Retrying in 5s..."
    sleep 5
done

if [ "$HEALTHY" = false ]; then
    log "ERROR: API did not become healthy after $MAX_ATTEMPTS attempts. Check logs."
    log "Run: docker compose logs --tail 50 gateway"
    exit 1
fi

# --- Step 4: Re-ingest HA devices ---
log "Re-ingesting Home Assistant devices via Gateway..."
# Execute the POST request INSIDE the gateway container
$COMPOSE exec -T gateway curl -s -X POST "http://localhost:11435/api/discovery/sync" \
     -H "Content-Type: application/json" \
     -d '{"user": "admin"}' \
     | tee -a "$LOG_FILE"

log "========================================="
log "Deploy complete."
log "========================================="
