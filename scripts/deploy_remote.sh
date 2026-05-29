#!/bin/bash
# deploy_remote.sh
# Usage: ./deploy_remote.sh [user@machine_ip] [path_to_app]

# Load variables from .env
if [ -f .env ]; then
    set -a
    # shellcheck disable=SC1091
    source .env
    set +a
fi

# Fallback or Override
# Prioritize Argument -> RAG_ADDRESS -> Hardcoded Default
ARG_HOST=$1
# Default to RAG_ADDRESS from env, but fail if not set
if [ -z "$RAG_ADDRESS" ] && [ -z "$ARG_HOST" ]; then
    echo "ERROR: RAG_ADDRESS not set in .env and no host argument provided."
    echo "Usage: ./deploy_remote.sh [user@machine_ip] [path_to_app]"
    exit 1
fi
TARGET_IP=${RAG_ADDRESS}
HOST="${ARG_HOST:-jeremiah@$TARGET_IP}"
DIR="${2:-/home/jeremiah/SharedLLM}"

echo "Deploying to $HOST:$DIR"

# Sync local .env to remote to ensure config match
if [ -f .env ]; then
    echo "Syncing local .env to remote..."
    scp .env "$HOST:$DIR/.env"
fi

# Detect current branch locally
BRANCH=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "main")
echo "Branch: $BRANCH"

# shellcheck disable=SC2087
if ssh "$HOST" << EOF
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

    echo "Rebuilding and starting Docker containers..."
    docker compose up -d --build

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
