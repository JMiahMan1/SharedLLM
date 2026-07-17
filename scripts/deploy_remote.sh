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

# Function to wait ONLY for the image build (Build & Push Images) to finish.
# It does NOT wait for E2E/test pipelines — those run independently and do not
# block deployment.
wait_for_build() {
    echo "Waiting for 'Build & Push Images' to finish (not waiting on E2E/tests)..."
    local max_attempts=60
    local attempt=0
    local wait_time=10  # 10 seconds between checks

    while [ $attempt -lt $max_attempts ]; do
        # Latest Build & Push Images run for this branch
        local bpi_status
        bpi_status=$(gh run list --branch=microservices --json status,name \
            --jq '.[] | select(.name=="Build & Push Images") | .status' | head -1)

        case "$bpi_status" in
            completed)
                local bpi_conclusion
                bpi_conclusion=$(gh run list --branch=microservices --json conclusion,name \
                    --jq '.[] | select(.name=="Build & Push Images") | .conclusion' | head -1)
                if [ "$bpi_conclusion" = "success" ]; then
                    echo "[OK] Build & Push Images completed successfully."
                    return 0
                fi
                echo "[FAIL] Build & Push Images finished with conclusion: $bpi_conclusion."
                gh run list --branch=microservices --json name,conclusion \
                    --jq '.[] | select(.name=="Build & Push Images")'
                exit 1
                ;;
            failure|cancelled|timed_out)
                echo "[FAIL] Build & Push Images $bpi_status."
                exit 1
                ;;
            "")
                # No run found yet (e.g. still queued) — keep waiting
                echo "Build & Push Images not started yet... (${attempt}/${max_attempts})"
                ;;
            *)
                # queued / in_progress / requested — keep waiting
                echo "Build & Push Images status: $bpi_status... (${attempt}/${max_attempts})"
                ;;
        esac

        sleep $wait_time
        attempt=$((attempt + 1))
    done

    echo "[FAIL] Timeout waiting for Build & Push Images."
    exit 1
}

# SSH options for robustness: auto-accept new host keys, fail on broken pipe
SSH_OPTS="-o StrictHostKeyChecking=accept-new -o BatchMode=yes -o ConnectTimeout=10"

# Wait for GitHub Actions build to complete before deploying
wait_for_build

# Sync non-git files to remote to ensure config match
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
    docker compose up -d --force-recreate --remove-orphans

    echo "Waiting for application startup..."
    # Monitor logs for success or failure
    # Timeout after 120 seconds
    TIMEOUT=120
    ELAPSED=0
    SUCCESS=0

    # Check logs until success message or timeout
    GATEWAY_CONTAINER=\$(docker ps --filter 'name=sharedllm_gateway' --format '{{.Names}}' | head -1 || echo "sharedllm_gateway")
    while [ \$ELAPSED -lt \$TIMEOUT ]; do
        if docker logs --tail 200 \$GATEWAY_CONTAINER 2>&1 | grep -q "Application startup complete"; then
            echo "[OK] Application started successfully!"
            SUCCESS=1
            break
        fi

        # Check for immediate failure (Traceback)
        if docker logs --tail 20 \$GATEWAY_CONTAINER 2>&1 | grep -q "Traceback"; then
            echo "[FAIL] Application failed to start! Traceback detected."
            docker logs --tail 20 \$GATEWAY_CONTAINER
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
        docker logs --tail 20 \$GATEWAY_CONTAINER
        exit 1
    fi

    # Verify execution container is running (not stuck in Created)
    EXEC_CONTAINER=\$(docker ps --filter 'name=sharedllm_execution' --format '{{.Names}}' | head -1)
    if [ -z "\$EXEC_CONTAINER" ]; then
        echo "[FAIL] Execution container not found! Compose may have left it in Created state."
        docker ps -a --filter 'name=execution' --format '{{.Names}} {{.Status}}'
        exit 1
    fi
    echo "[OK] Execution container (\$EXEC_CONTAINER) is running."
EOF
then
    echo "[OK] Deployment Verification Successful."
else
    echo "[FAIL] Deployment Failed."
    exit 1
fi
