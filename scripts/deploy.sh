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
# Ensure log dir exists
mkdir -p "$REPO_DIR/data"

log() {
    local msg="[$(date '+%Y-%m-%d %H:%M:%S')] $*"
    echo "$msg"
    echo "$msg" >> "$LOG_FILE"
}

# Detect IDs for Docker
export PUID=$(id -u)
export PGID=$(id -g)
export DOCKER_GID=$(getent group docker | cut -d: -f3)

# Fallback if DOCKER_GID is empty (e.g. group not found)
if [ -z "$DOCKER_GID" ]; then
    DOCKER_GID=$(stat -c '%g' /var/run/docker.sock 2>/dev/null || echo 980)
fi

# Pre-flight check: ensure critical environment variables are injected
if [ -z "${INTERNAL_SECRET:-}" ]; then
    if [ -f "$REPO_DIR/.env" ]; then
        set -a
        source "$REPO_DIR/.env"
        set +a
    fi
fi

if [ -z "${INTERNAL_SECRET:-}" ]; then
    echo "FATAL: INTERNAL_SECRET is not set in the environment or .env file!"
    exit 1
fi

COMPOSE="docker compose"

# --- Volume Permission Guard ---
# If running as a non-root user, ensure named volumes are writable by that user.
if [ "$PUID" != "0" ]; then
    log "Ensuring volume permissions for user $PUID:$PGID..."
    # Project prefix is usually the lowercase directory name
    PROJECT_PREFIX="sharedllm"
    VOLUMES=("identity_db" "chroma_data" "logging_data" "workspace_runtime_data" "redis_data")
    for VOL in "${VOLUMES[@]}"; do
        FULL_VOL_NAME="${PROJECT_PREFIX}_${VOL}"
        log "Fixing permissions for volume: $FULL_VOL_NAME"
        docker run --rm -v "$FULL_VOL_NAME:/data" busybox sh -c "chown -R $PUID:$PGID /data && chmod -R 775 /data" 2>/dev/null || true
    done
fi



cd "$REPO_DIR"

log "========================================="
log "SharedLLM Deployment System"
log "Branch: $(git rev-parse --abbrev-ref HEAD)"
log "Commit: $(git rev-parse --short HEAD)"
log "========================================="

# If arguments are passed, act as a docker compose wrapper (like up.sh)
if [ "$#" -gt 0 ]; then
    log "Custom command detected: $COMPOSE $*"
    $COMPOSE "$@"
    exit 0
fi

# --- Step 1: Check what changed ---
CHANGED_FILES=$(git diff --name-only HEAD@{1} HEAD 2>/dev/null || true)
CADDY_CHANGE=$(printf '%s\n' "$CHANGED_FILES" | grep -E "^Caddyfile$" || true)
INFRA_CHANGE=$(printf '%s\n' "$CHANGED_FILES" | grep -E "^docker-compose|^scripts/|^Dockerfile" || true)

# Identify specific services that changed
MODIFIED_SERVICES=""
SHARED_FILE_CHANGE=false
if [ -n "$CHANGED_FILES" ]; then
    # Check for shared files that affect all services
    if printf '%s\n' "$CHANGED_FILES" | grep -qE "^services/config\.py$"; then
        SHARED_FILE_CHANGE=true
    fi
    # Extract service names from paths like services/gateway/...
    # Only match known service directories
    KNOWN_SERVICES="automation|control_plane|dns_sync|execution|gateway|identity|logging|rag|storage|ui|workspace_runtime"
    MODIFIED_SERVICES=$(printf '%s\n' "$CHANGED_FILES" | grep "^services/" | cut -d'/' -f2 | grep -E "^(${KNOWN_SERVICES})$" | sort | uniq || true)
fi

log "Changes detected in: $CHANGED_FILES"

# --- Step 2: Pull and Restart ---
if [ -n "$INFRA_CHANGE" ]; then
    log "Infrastructure changes detected — pulling all images."
    log "Running: $COMPOSE pull && $COMPOSE up -d"
    $COMPOSE pull 2>&1 | tee -a "$LOG_FILE"
    $COMPOSE up -d 2>&1 | tee -a "$LOG_FILE"
elif [ "$SHARED_FILE_CHANGE" = true ]; then
    log "Shared config change detected — pulling all images."
    log "Running: $COMPOSE pull && $COMPOSE up -d"
    $COMPOSE pull 2>&1 | tee -a "$LOG_FILE"
    $COMPOSE up -d 2>&1 | tee -a "$LOG_FILE"
elif [ -n "$MODIFIED_SERVICES" ]; then
    log "Service changes detected: $MODIFIED_SERVICES"
    # Always pull all since base image changes affect all services
    log "Pulling all images (base image may affect all services)."
    $COMPOSE pull 2>&1 | tee -a "$LOG_FILE"
    for SVC in $MODIFIED_SERVICES; do
        log "Restarting: $SVC"
        $COMPOSE up -d "$SVC" 2>&1 | tee -a "$LOG_FILE"
    done
else
    log "No service or infra changes detected."
fi

if [ -n "$CADDY_CHANGE" ]; then
    log "Caddy configuration change detected — restarting caddy."
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
     -d '{"rag_user": "default"}' \
     | tee -a "$LOG_FILE"

log "========================================="
log "Deploy complete."
log "========================================="
