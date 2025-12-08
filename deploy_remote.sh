#!/bin/bash
# deploy_remote.sh
# Usage: ./deploy_remote.sh [user@machine_ip] [path_to_app]

# Load variables from .env
if [ -f .env ]; then
    export $(cat .env | grep -v '^#' | xargs)
fi

# Fallback or Override
# Prioritize Argument -> RAG_ADDRESS -> Hardcoded Default
ARG_HOST=$1
TARGET_IP=${RAG_ADDRESS:-192.168.2.211}
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

ssh "$HOST" << EOF
    cd "$DIR"
    echo "Fetching latest code..."
    git fetch origin
    
    # Ensure we are on the correct branch and sync hard
    git checkout $BRANCH || git checkout -b $BRANCH origin/$BRANCH
    git reset --hard origin/$BRANCH
    git pull origin $BRANCH
    
    # Prune pycache to prevent lingering issues
    find app -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null

    echo "Restarting Docker container..."
    docker compose restart

    echo "Waiting for application startup..."
    # Monitor logs for success or failure
    # Timeout after 60 seconds
    TIMEOUT=60
    ELAPSED=0
    SUCCESS=0
    
    # Check logs until success message or timeout
    while [ \$ELAPSED -lt \$TIMEOUT ]; do
        if docker logs --tail 20 unified_rag_api 2>&1 | grep -q "Application startup complete"; then
            echo "[OK] Application started successfully!"
            SUCCESS=1
            break
        fi
        
        # Check for immediate failure (Traceback)
        if docker logs --tail 20 unified_rag_api 2>&1 | grep -q "Traceback"; then
            echo "[FAIL] Application failed to start! Traceback detected."
            docker logs --tail 20 unified_rag_api
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
        docker logs --tail 20 unified_rag_api
        exit 1
    fi
EOF

if [ $? -eq 0 ]; then
    echo "[OK] Deployment Verification Successful."
else
    echo "[FAIL] Deployment Failed."
    exit 1
fi
