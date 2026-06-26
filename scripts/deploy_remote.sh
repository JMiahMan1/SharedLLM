#!/bin/bash
# deploy_remote.sh
# Usage: ./deploy_remote.sh [user@machine_ip] [path_to_app]

set -euo pipefail

ARG_HOST=$1
if [ -z "$ARG_HOST" ]; then
    echo "ERROR: No host argument provided."
    echo "Usage: ./deploy_remote.sh [user@machine_ip] [path_to_app]"
    exit 1
fi
HOST="$ARG_HOST"
DIR="${2:-/home/jeremiah/SharedLLM}"

# SSH options for robustness: auto-accept new host keys, fail on broken pipe
SSH_OPTS="-o StrictHostKeyChecking=accept-new -o BatchMode=yes -o ConnectTimeout=10"

# Sync local .env to remote to ensure config match (use ssh pipe instead of scp for reliability)
if [ -f .env ]; then
    echo "Syncing local .env to remote..."
    ssh $SSH_OPTS "$HOST" "mkdir -p '$DIR' && cat > '$DIR/.env'" < .env
fi

echo "Deploying to $HOST:$DIR"

# Detect current branch locally
BRANCH=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "main")
echo "Branch: $BRANCH"

# shellcheck disable=SC2087
if ssh $SSH_OPTS "$HOST" << EOF
    cd "$DIR"

    # Detect Docker user/group IDs dynamically (not hardcoded)
    export PUID=\$(id -u)
    export PGID=\$(id -g)
    export DOCKER_GID=\$(getent group docker | cut -d: -f3)
    if [ -z "\$DOCKER_GID" ]; then
        export DOCKER_GID=\$(stat -c '%g' /var/run/docker.sock 2>/dev/null || echo 980)
    fi

    # Prune pycache using Docker to bypass root permission issues BEFORE git ops
    echo "Pruning __pycache__ via Docker..."
    if [ -d "app" ]; then
        docker run --rm -v "\$(pwd)/app:/app" -w /app alpine find . -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null
    fi
    # Prune root-owned test reports/directory that block git reset
    echo "Pruning root-owned test reports..."
    docker run --rm -v "\$(pwd)/data:/data" alpine sh -c "rm -rf /data/tests" 2>/dev/null

    echo "Fetching latest code..."
    git fetch origin

    # Ensure we are on the correct branch and sync hard
    git checkout $BRANCH || git checkout -b $BRANCH origin/$BRANCH
    git reset --hard origin/$BRANCH
    git pull origin $BRANCH

    echo "Pulling latest images from GHCR and starting Docker containers..."
    docker compose pull
    docker compose up -d

    echo "Waiting for application startup..."
    # Monitor logs for success or failure
    # Timeout after 120 seconds
    TIMEOUT=120
    ELAPSED=0
    SUCCESS=0

    # Check logs until success message or timeout
    while [ \$ELAPSED -lt \$TIMEOUT ]; do
        if docker logs --tail 200 sharedllm_gateway 2>&1 | grep -q "Application startup complete"; then
            echo "[OK] Application started successfully!"
            SUCCESS=1
            break
        fi

        # Check for immediate failure (Traceback)
        if docker logs --tail 20 sharedllm_gateway 2>&1 | grep -q "Traceback"; then
            echo "[FAIL] Application failed to start! Traceback detected."
            docker logs --tail 20 sharedllm_gateway
            exit 1
        fi

        sleep 2
        let ELAPSED=ELAPSED+2
        echo -n "."
    done

    echo ""
    if [ \$SUCCESS -eq 0 ]; then
        echo "[FAIL] Timeout waiting for application startup."
        echo "Last 20 lines of logs:"
        docker logs --tail 20 sharedllm_gateway
        exit 1
    fi
EOF
then
    echo "[OK] Deployment Verification Successful."
else
    echo "[FAIL] Deployment Failed."
    exit 1
fi
