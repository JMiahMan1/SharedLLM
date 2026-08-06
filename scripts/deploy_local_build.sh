#!/bin/bash
# deploy_local_build.sh
# Usage: ./deploy_local_build.sh [user@host] [service...]
#
# Builds docker images LOCALLY on the remote server (bypassing GHCR and
# GitHub Actions, which may be down) and redeploys the stack.
# All docker commands run on the remote host — nothing is built on localhost.
#
# Defaults to the services changed by the two most recent commits; pass
# explicit service names to override.

set -euo pipefail

HOST="${1:-jeremiah@192.168.2.205}"
DIR="/home/jeremiah/SharedLLM"
SSH_OPTS="-o StrictHostKeyChecking=accept-new -o BatchMode=yes -o ConnectTimeout=10"

if [ $# -ge 2 ]; then
    SERVICES="${@:2}"
else
    # Derive changed services from the last two commits (services/<name>/...)
    SERVICES=$(git diff --name-only HEAD~2..HEAD -- 'services/*/*.py' 2>/dev/null \
        | sed -n 's|^services/\([^/]*\)/.*|\1|p' | sort -u | tr '\n' ' ')
    if [ -z "$SERVICES" ]; then
        SERVICES="gateway identity workspace_runtime"
    fi
fi
echo "[OK] Services to build on ${HOST}: $SERVICES"

BRANCH=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "main")
echo "[OK] Branch: $BRANCH"

# Sync non-git files so config matches local (same as deploy_remote.sh)
for NON_GIT_FILE in ".env" "prompts/"; do
    if [ -e "$NON_GIT_FILE" ]; then
        echo "Syncing $NON_GIT_FILE to remote..."
        if [ -d "$NON_GIT_FILE" ]; then
            ssh $SSH_OPTS "$HOST" "mkdir -p '$DIR/$NON_GIT_FILE'"
            rsync -a --delete -e "ssh $SSH_OPTS" "$NON_GIT_FILE/" "$HOST:$DIR/$NON_GIT_FILE/"
        else
            ssh $SSH_OPTS "$HOST" "mkdir -p '$DIR' && cat > '$DIR/$NON_GIT_FILE'" < "$NON_GIT_FILE"
        fi
    fi
done

echo "Deploying to $HOST:$DIR"

# shellcheck disable=SC2087
ssh $SSH_OPTS "$HOST" << EOF
    cd "$DIR"
    set -euo pipefail

    # Detect Docker user/group IDs dynamically (not hardcoded)
    export PUID=\$(id -u)
    export PGID=\$(id -g)
    export DOCKER_GID=\$(getent group docker | cut -d: -f3)
    if [ -z "\$DOCKER_GID" ]; then
        export DOCKER_GID=\$(stat -c '%g' /var/run/docker.sock 2>/dev/null)
    fi
    if [ -z "\$DOCKER_GID" ]; then
        echo "[FAIL] Cannot determine Docker group GID. 'docker' group or socket missing?"
        exit 1
    fi
    echo "[OK] Detected PUID=\$PUID PGID=\$PGID DOCKER_GID=\$DOCKER_GID"

    # Write detected values into .env so docker-compose reads them
    if grep -q '^PUID=' .env; then
        sed -i "s/^PUID=.*/PUID=\$PUID/" .env
    else
        echo "PUID=\$PUID" >> .env
    fi
    if grep -q '^PGID=' .env; then
        sed -i "s/^PGID=.*/PGID=\$PGID/" .env
    else
        echo "PGID=\$PGID" >> .env
    fi
    if grep -q '^DOCKER_GID=' .env; then
        sed -i "s/^DOCKER_GID=.*/DOCKER_GID=\$DOCKER_GID/" .env
    else
        echo "DOCKER_GID=\$DOCKER_GID" >> .env
    fi
    echo "[OK] Updated .env with PUID=\$PUID PGID=\$PGID DOCKER_GID=\$DOCKER_GID"

    # Prune pycache using Docker to bypass root permission issues BEFORE git ops
    echo "Pruning __pycache__ via Docker..."
    if [ -d "app" ]; then
        docker run --rm -v "\$(pwd)/app:/app" -w /app alpine find . -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true
    fi
    # Prune root-owned test reports/directory that block git reset
    echo "Pruning root-owned test reports..."
    docker run --rm -v "\$(pwd)/data:/data" alpine sh -c "rm -rf /data/tests" 2>/dev/null || true

    echo "Fetching latest code..."
    git fetch origin

    # Ensure we are on the correct branch and sync hard
    git checkout $BRANCH || git checkout -b $BRANCH origin/$BRANCH
    git reset --hard origin/$BRANCH
    git pull origin $BRANCH

    echo "Building images locally on server..."
    for SVC in $SERVICES; do
        echo "=== Building sharedllm-\$SVC ==="
        docker build \
            --build-arg GIT_SHA=\$(git rev-parse --short HEAD) \
            --build-arg BUILD_DATE=\$(date -u +%Y-%m-%dT%H:%M:%SZ) \
            --build-arg SERVICE_NAME=\$SVC \
            -t ghcr.io/jmiahman1/sharedllm-\$SVC:latest \
            -f services/\$SVC/Dockerfile .
        echo "[OK] Built sharedllm-\$SVC"
    done

    echo "Starting containers (no GHCR pull)..."
    docker compose up -d --force-recreate --remove-orphans --pull never

    echo "Waiting for application startup..."
    TIMEOUT=180
    ELAPSED=0
    SUCCESS=0

    GATEWAY_CONTAINER=\$(docker ps --filter 'name=sharedllm_gateway' --format '{{.Names}}' | head -1 || echo "sharedllm_gateway")
    while [ \$ELAPSED -lt \$TIMEOUT ]; do
        if docker logs --tail 200 \$GATEWAY_CONTAINER 2>&1 | grep -q "Application startup complete"; then
            echo "[OK] Application started successfully!"
            SUCCESS=1
            break
        fi

        if docker logs --tail 20 \$GATEWAY_CONTAINER 2>&1 | grep -q "Traceback"; then
            echo "[FAIL] Application failed to start! Traceback detected."
            docker logs --tail 20 \$GATEWAY_CONTAINER
            exit 1
        fi

        sleep 2
        ELAPSED=\$((ELAPSED + 2))
        echo -n "."
    done

    echo ""
    if [ \$SUCCESS -eq 0 ]; then
        echo "[FAIL] Timeout waiting for application startup."
        echo "Last 20 lines of logs:"
        docker logs --tail 20 \$GATEWAY_CONTAINER
        exit 1
    fi
EOF

echo "[DONE] Local-build deployment to $HOST finished."
