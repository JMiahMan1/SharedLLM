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
    echo "Waiting for latest 'Build & Push Images' on microservices to finish..."
    local max_attempts=90
    local attempt=0
    local wait_time=10

    while [ $attempt -lt $max_attempts ]; do
        local latest_status latest_conclusion latest_sha
        latest_status=$(gh run list --branch=microservices --limit 5 --json status,name \
            --jq '.[] | select(.name=="Build & Push Images") | .status' | head -1)
        latest_conclusion=$(gh run list --branch=microservices --limit 5 --json conclusion,name \
            --jq '.[] | select(.name=="Build & Push Images") | .conclusion' | head -1)
        latest_sha=$(gh run list --branch=microservices --limit 5 --json headSha,name \
            --jq '.[] | select(.name=="Build & Push Images") | .headSha' | head -1)

        if [ "$latest_status" = "completed" ] && [ "$latest_conclusion" = "success" ]; then
            echo "[OK] Build & Push Images (${latest_sha:0:8}) completed successfully."
            return 0
        fi

        if [ "$latest_status" = "completed" ] && [ "$latest_conclusion" != "success" ]; then
            echo "[FAIL] Build & Push Images (${latest_sha:0:8}) failed with conclusion: $latest_conclusion"
            exit 1
        fi

        echo "Build & Push Images (${latest_sha:0:8}) status: ${latest_status:-waiting}... (${attempt}/${max_attempts})"
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

# Check if a latest Android APK artifact is available from CI and sync it to update directory
if command -v gh >/dev/null 2>&1; then
    echo "Checking for latest Android APK artifact from CI..."
    APK_RUN_ID=$(gh run list --workflow=android-build.yml --branch="$BRANCH" --limit 1 --json databaseId,status,conclusion --jq '.[] | select(.conclusion=="success") | .databaseId' 2>/dev/null || true)
    if [ -n "$APK_RUN_ID" ]; then
        echo "Downloading APK artifact from CI run $APK_RUN_ID..."
        mkdir -p .tmp/apk
        rm -rf .tmp/apk/*
        if gh run download "$APK_RUN_ID" -n jarvis-os-debug-apk -D .tmp/apk 2>/dev/null; then
            echo "Syncing APK to remote $HOST:$DIR/data/app_updates/..."
            ssh $SSH_OPTS "$HOST" "mkdir -p '$DIR/data/app_updates'"
            rsync -a -e "ssh $SSH_OPTS" .tmp/apk/app-debug.apk "$HOST:$DIR/data/app_updates/app-debug.apk"
            echo "[OK] Latest APK synced to remote update server."
        fi
    fi
fi

# shellcheck disable=SC2087
if ssh $SSH_OPTS "$HOST" << EOF
    cd "$DIR"

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
    # NOTE: this heredoc uses unquoted EOF, so EVERY dollar below must be backslash-escaped
    # to evaluate on the REMOTE host. Bare expansions evaluate LOCALLY and break the deploy.
    GATEWAY_CONTAINER=\$(docker ps --filter 'name=sharedllm_gateway' --format '{{.Names}}' | head -1)
    if [ -z "\$GATEWAY_CONTAINER" ]; then
        echo "[WARN] Gateway container not found via 'docker ps'; defaulting to 'sharedllm_gateway'."
        GATEWAY_CONTAINER="sharedllm_gateway"
    fi
    while [ \$ELAPSED -lt \$TIMEOUT ]; do
        if docker logs --tail 200 "\$GATEWAY_CONTAINER" 2>&1 | grep -q "Application startup complete"; then
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

    # Stage OTA web update bundle and version info for in-app mobile updates.
    #
    # The advertised git_sha MUST be the SHA baked into the bundle we publish.
    # Deriving it from the server's git HEAD instead lets metadata and bundle
    # drift apart, and the mobile app then downloads, applies, restarts, and
    # still sees itself as out of date — an endless reload loop.
    UI_CONTAINER=\$(docker ps --filter 'name=sharedllm_ui' --format '{{.Names}}' | head -1)
    if [ -n "\$UI_CONTAINER" ]; then
        echo "Staging OTA update bundle from \$UI_CONTAINER to data/app_updates..."
        mkdir -p data/app_updates
        if ! docker cp "\$UI_CONTAINER:/usr/share/nginx/html/bundle.zip" data/app_updates/bundle.zip; then
            echo "[FAIL] Could not copy bundle.zip out of \$UI_CONTAINER."
            echo "       Refusing to publish stale OTA metadata (would reload-loop the mobile app)."
            exit 1
        fi
        if ! docker cp "\$UI_CONTAINER:/usr/share/nginx/html/version.json" data/app_updates/.bundle_version.json; then
            echo "[FAIL] Could not copy version.json out of \$UI_CONTAINER."
            exit 1
        fi

        BUNDLE_SHA=\$(python3 -c "import json;print(json.load(open('data/app_updates/.bundle_version.json')).get('git_sha') or json.load(open('data/app_updates/.bundle_version.json')).get('gitSha') or 'unknown')")
        BUNDLE_VERSION=\$(python3 -c "import json;print(json.load(open('data/app_updates/.bundle_version.json')).get('version') or '1.2.0')")
        if [ "\$BUNDLE_SHA" = "unknown" ] || [ -z "\$BUNDLE_SHA" ]; then
            echo "[FAIL] Built UI bundle has no git_sha (was GIT_SHA passed as a build arg?)."
            echo "       Refusing to publish an unidentifiable bundle."
            exit 1
        fi
        CURRENT_SHA=\$(git rev-parse --short HEAD 2>/dev/null || echo "unknown")
        if [ "\$BUNDLE_SHA" != "\$CURRENT_SHA" ]; then
            echo "[WARN] Deployed UI bundle is \$BUNDLE_SHA but the checkout is at \$CURRENT_SHA."
            echo "       Publishing \$BUNDLE_SHA (what clients actually receive)."
        fi

        BUILD_TIME=\$(date -u +"%Y-%m-%dT%H:%M:%SZ")
        cat << JSON_EOF > data/app_updates/version.json
{
  "version": "\$BUNDLE_VERSION",
  "git_sha": "\$BUNDLE_SHA",
  "build_timestamp": "\$BUILD_TIME",
  "release_notes": "Jarvis OS Over-The-Air Update"
}
JSON_EOF
        rm -f data/app_updates/.bundle_version.json
        echo "[OK] Published OTA bundle \$BUNDLE_SHA (version \$BUNDLE_VERSION)."
    fi
EOF
then
    echo "[OK] Deployment Verification Successful."
else
    echo "[FAIL] Deployment Failed."
    exit 1
fi
