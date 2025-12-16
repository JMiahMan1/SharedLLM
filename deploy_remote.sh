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


# Sync local code to remote (Bypassing git to allow local dev testing)
echo "Syncing local code to remote..."
rsync -avz --exclude '__pycache__' --exclude '.git' --exclude 'temp' --exclude '.venv' ./ "$HOST:$DIR/"

ssh "$HOST" << EOF
    cd "$DIR"
    echo "Code synced. Restarting containers..."
    
    # Prune pycache to prevent lingering issues
    find app -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null

    echo "Recreating Docker container to apply config..."
    docker compose up -d --build --force-recreate

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
