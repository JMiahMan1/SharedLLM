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

# Which services this deploy is for. Accepts them as extra args; otherwise
# derives them from the last two commits, same as deploy_local_build.sh.
#
# This matters for more than tidiness: `docker compose up --force-recreate`
# with no service names recreates every container in the project, including
# unrelated wsbox-* ones, so each deploy briefly takes the whole stack down.
shift $(( $# > 2 ? 2 : $# ))
if [ $# -ge 1 ]; then
    SERVICES="$@"
else
    SERVICES=$(git diff --name-only HEAD~2..HEAD -- 'services/*/*.py' 2>/dev/null \
        | sed -n 's|^services/\([^/]*\)/.*|\1|p' | sort -u | tr '\n' ' ')
fi
if [ -z "$SERVICES" ]; then
    # Never fall back to "recreate everything" -- that is the behaviour this
    # variable exists to prevent.
    echo "[FAIL] Could not determine which services to deploy. Pass them explicitly:"
    echo "       $0 <host> <path> <service> [service...]"
    exit 1
fi
echo "[OK] Services to deploy on ${HOST}: $SERVICES"

# Function to wait ONLY for the image build (Build & Push Images) to finish.
# It does NOT wait for E2E/test pipelines — those run independently and do not
# block deployment.
wait_for_build() {
    local branch="$1"
    local expect_sha="$2"
    echo "Waiting for 'Build & Push Images' ($expect_sha) on $branch to finish..."
    local max_attempts=90
    local attempt=0
    local wait_time=10

    while [ $attempt -lt $max_attempts ]; do
        # One call for all three fields. Asking for status, conclusion and
        # headSha separately meant reading them from three different moments:
        # GitHub flips status to "completed" before it populates conclusion, so
        # the script could pair a completed status with an empty conclusion and
        # declare the build failed while it was still being finalised.
        # Query the workflow itself: filtering the newest N branch runs missed
        # this workflow entirely on commits that also trigger docs/UI/APK/E2E.
        local run
        run=$(gh run list --workflow=build-images.yml --branch="$branch" --limit 5 \
            --json headSha,status,conclusion 2>/dev/null \
            | jq -c --arg sha "$expect_sha" '[.[] | select(.headSha == $sha)][0] // {}')

        local run_status run_conclusion
        run_status=$(printf '%s' "$run" | jq -r '.status // ""')
        run_conclusion=$(printf '%s' "$run" | jq -r '.conclusion // ""')

        if [ "$run_status" = "completed" ] && [ "$run_conclusion" = "success" ]; then
            echo "[OK] Build & Push Images (${expect_sha:0:8}) completed successfully."
            return 0
        fi

        # An empty conclusion on a completed run is the API settling, not a
        # verdict. Treating it as failure aborted deploys that were about to
        # succeed.
        if [ "$run_status" = "completed" ] && [ -n "$run_conclusion" ] && [ "$run_conclusion" != "success" ]; then
            echo "[FAIL] Build & Push Images (${expect_sha:0:8}) failed with conclusion: $run_conclusion"
            exit 1
        fi

        if [ -z "$run_status" ]; then
            echo "No 'Build & Push Images' run for ${expect_sha:0:8} on $branch yet... (${attempt}/${max_attempts})"
        else
            echo "Build & Push Images (${expect_sha:0:8}) status: ${run_status}${run_conclusion:+ ($run_conclusion)}... (${attempt}/${max_attempts})"
        fi
        sleep $wait_time
        attempt=$((attempt + 1))
    done

    echo "[FAIL] Timeout waiting for Build & Push Images (${expect_sha:0:8})."
    exit 1
}

# SSH options for robustness: auto-accept new host keys, fail on broken pipe
SSH_OPTS="-o StrictHostKeyChecking=accept-new -o BatchMode=yes -o ConnectTimeout=10"

# Sync non-git files to remote to ensure config match
for NON_GIT_FILE in "prompts/"; do
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

# .env is SEED-ONLY: services read it once at boot to learn their settings, and
# from then on Identity's GlobalSetting rows are the runtime source of truth
# (editable in Admin > Settings). It is still synced, because a fresh host has
# never seen it and INTERNAL_SECRET, FERNET_KEY, the network URLs and PUID/PGID
# have no other source.
#
# The merge is strictly additive. A key the host already defines is left exactly
# as it is: that copy is the only record of anything deliberately set there, and
# a deploy must not revert it. Only missing keys are appended, verbatim.
if [ -e ".env" ]; then
    echo "Merging .env into the host copy (existing host values are preserved)..."
    MERGE_SCRIPT="scripts/merge_env.py"
    REMOTE_MERGE="'$DIR'/.tmp/merge_env.py"
    ssh $SSH_OPTS "$HOST" "mkdir -p '$DIR/.tmp'"
    ssh $SSH_OPTS "$HOST" "cat > $REMOTE_MERGE" < "$MERGE_SCRIPT"
    if ! ssh $SSH_OPTS "$HOST" "cd '$DIR' && python3 $REMOTE_MERGE '$DIR/.env'" < ".env"; then
        echo "[FAIL] Could not merge .env on the host. The host's copy was left alone."
        echo "       Everything set there is still intact; fix the merge and redeploy."
        exit 1
    fi
    ssh $SSH_OPTS "$HOST" "rm -f $REMOTE_MERGE" || true
else
    echo "[WARN] No local .env to merge. The host keeps whatever it already has."
fi

echo "Deploying to $HOST:$DIR"

# Detect current branch locally.
# A detached HEAD (CI checkout, git worktree) resolves to the literal string
# "HEAD", which turns every remote git op into a silent no-op and leaves the
# server on a stale commit. Fall back to the real branch in that case.
if [ "$(git rev-parse --abbrev-ref HEAD 2>/dev/null)" = "HEAD" ]; then
    BRANCH="${DEPLOY_BRANCH:-microservices}"
    echo "WARN: detached HEAD — deploying branch '$BRANCH' explicitly."
else
    BRANCH=$(git rev-parse --abbrev-ref HEAD)
fi
echo "Branch: $BRANCH"

# Wait for GitHub Actions to build the commit we are about to deploy. This runs
# after the branch is resolved so it honours DEPLOY_BRANCH and a detached HEAD,
# and it names the SHA so a newer push landing mid-deploy cannot be mistaken
# for this one.
#
# That is the newest commit touching what build-images.yml builds from (its
# `paths` filter), not HEAD: a docs-only HEAD gets no image build at all, and
# waiting for one hung every deploy for 15 minutes before failing.
EXPECT_SHA=$(git log -1 --format=%H HEAD -- services docker docker-compose.yml Caddyfile \
    requirements.txt tools .github/workflows/build-images.yml 2>/dev/null || echo "")
[ -n "$EXPECT_SHA" ] || EXPECT_SHA=$(git rev-parse HEAD 2>/dev/null || echo "")
if [ -n "$EXPECT_SHA" ]; then
    wait_for_build "$BRANCH" "$EXPECT_SHA"
else
    echo "[FAIL] Could not resolve the commit being deployed; refusing to deploy an unverified build."
    exit 1
fi

# Check if a latest Android APK artifact is available from CI and sync it to update directory
if command -v gh >/dev/null 2>&1; then
    echo "Checking for latest Android APK artifact from CI..."
    APK_RUN_ID=$(gh run list --workflow=android-build.yml --branch="$BRANCH" --limit 5 --json databaseId,status,conclusion --jq '.[] | select(.conclusion=="success") | .databaseId' 2>/dev/null | head -1 || true)
    if [ -n "$APK_RUN_ID" ]; then
    echo "Downloading APK artifact from CI run $APK_RUN_ID..."
    mkdir -p .tmp/apk
    rm -rf .tmp/apk/*
    # Prefer signed release APK; fall back to debug (also signed with release.keystore)
    if gh run download "$APK_RUN_ID" -n jarvis-os-release-apk -D .tmp/apk 2>/dev/null; then
        APK_SRC=$(ls .tmp/apk/*.apk 2>/dev/null | head -1)
    elif gh run download "$APK_RUN_ID" -n jarvis-os-debug-apk -D .tmp/apk 2>/dev/null; then
        APK_SRC=$(ls .tmp/apk/*.apk 2>/dev/null | head -1)
    else
        APK_SRC=""
    fi
    if [ -n "${APK_SRC:-}" ] && [ -f "$APK_SRC" ]; then
        echo "Syncing APK ($APK_SRC) to remote $HOST:$DIR/data/app_updates/..."
        ssh $SSH_OPTS "$HOST" "mkdir -p '$DIR/data/app_updates'"
        rsync -a -e "ssh $SSH_OPTS" "$APK_SRC" "$HOST:$DIR/data/app_updates/jarvis-os.apk"
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

    # Say the resulting commit out loud. A silent no-op here is what let the
    # server sit on a months-old commit while deploys reported success.
    DEPLOYED_SHA=\$(git rev-parse --short HEAD 2>/dev/null || echo "unknown")
    echo "Server now at commit: \$DEPLOYED_SHA"

    echo "Pulling latest images from GHCR and starting Docker containers..."
    # Scoped to the services this deploy rebuilt. With no service names this
    # recreates every container in the project, including unrelated wsbox-*
    # ones, so each deploy briefly takes the whole stack down and services that
    # had not changed at all throw connection errors while they restart.
    # shellcheck disable=SC2086
    docker compose pull $SERVICES

    # Pin each named service to the image CI built for this exact commit, when
    # that tag exists. Tagging is not ordered: a build for an older commit can
    # finish after this commit's build and leave ":latest" pointing at the
    # older image, which is how a deploy can pull the previous build while
    # reporting success. The SHA tag cannot be overwritten that way, so it is
    # what we go by; "latest" is only a fallback for services this commit did
    # not change, which CI therefore never retagged.
    SHA_TAG=\$(git rev-parse --short=8 HEAD 2>/dev/null || true)
    if [ -n "\$SHA_TAG" ] && [ -n "$SERVICES" ]; then
        for service in $SERVICES; do
            repo="ghcr.io/jmiahman1/sharedllm-\$service"
            if docker pull -q "\$repo:\$SHA_TAG" >/dev/null 2>&1; then
                docker tag "\$repo:\$SHA_TAG" "\$repo:latest"
                echo "Pinned \$service to \$SHA_TAG"
            else
                echo "[WARN] No image tagged \$SHA_TAG for \$service; using whatever :latest holds."
            fi
        done
    fi

    # shellcheck disable=SC2086
    # NO --remove-orphans: see deploy_local_build.sh. It deletes any container
    # missing from this checkout's compose file, including another author's
    # uncommitted service.
    docker compose up -d --force-recreate $SERVICES

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

    # A recreate removes the old container before the new one registers, so
    # wait for it to exist again instead of reading logs from a name that is
    # (briefly) gone — that read used to abort the whole deploy.
    CONTAINER_WAIT=0
    while [ \$CONTAINER_WAIT -lt 120 ]; do
        if docker ps -a --filter "name=\$GATEWAY_CONTAINER" --format '{{.Names}}' | grep -q .; then
            break
        fi
        sleep 2
        let CONTAINER_WAIT=CONTAINER_WAIT+2
    done

    # Poll the gateway's own HTTP health endpoint, not its logs.
    #
    # Grepping \`docker logs --tail 200\` for "Application startup complete"
    # only works if the gateway was just created: the banner is printed once at
    # boot and scrolls out of the tail after a few hundred lines of request
    # logging. Now that the recreate is scoped to \$SERVICES, a gateway-only
    # change is the common case and the banner may be long gone -- so the gate
    # could never pass. Port 11435 is published on the host, so /health is
    # answerable whether or not the gateway was recreated in this deploy.
    GATEWAY_HEALTH_URL="http://127.0.0.1:11435/health"

    while [ \$ELAPSED -lt \$TIMEOUT ]; do
        # 000 means the port is not accepting yet, which is exactly the
        # "still starting" case -- keep waiting rather than failing.
        if [ "\$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 \$GATEWAY_HEALTH_URL 2>/dev/null)" = "200" ]; then
            echo "[OK] Application started successfully!"
            SUCCESS=1
            break
        fi

        # Check for immediate failure (Traceback)
        if docker logs --tail 20 \$GATEWAY_CONTAINER 2>&1 | grep -q "Traceback"; then
            echo "[FAIL] Application failed to start! Traceback detected."
            docker logs --tail 20 \$GATEWAY_CONTAINER 2>&1 || true
            exit 1
        fi

        sleep 2
        let ELAPSED=ELAPSED+2
        echo -n "."
    done

    echo ""
    if [ \$SUCCESS -eq 0 ]; then
        echo "[FAIL] Timeout waiting for application startup at \$GATEWAY_HEALTH_URL."
        echo "Last 20 lines of logs:"
        docker logs --tail 20 \$GATEWAY_CONTAINER 2>&1 || echo "(no logs available for \$GATEWAY_CONTAINER)"
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

    # Reclaim space from the images this deploy just superseded.
    #
    # Every build on the server leaves the previous image behind as an untagged
    # layer, and on the production host that accumulated to 484 images / 529 GB:
    # the disk hit 100% and a build failed with "No space left on device". Only
    # *dangling* images are removed, so every image a running container uses is
    # untouched. The build cache is pruned the same way, but only once it is a
    # day old so a follow-up deploy can still reuse recent layers.
    echo "Pruning dangling images and stale build cache..."
    docker image prune -f
    docker builder prune -f --filter until=24h || true
    df -h / | tail -1

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
        BUNDLE_VERSION=\$(python3 -c "import json;print(json.load(open('data/app_updates/.bundle_version.json')).get('version') or json.load(open('services/ui/package.json')).get('version') or '0.0.0')")
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

        BUILD_TIME=\$(date -u +%Y-%m-%dT%H:%M:%SZ)
        # Deliberately NO apk_version_code here. The gateway reads the version
        # code out of the APK it actually serves, so writing a number parsed
        # from build.gradle would just reintroduce the drift that made a
        # correctly-installed app look permanently out of date.
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
