#!/bin/bash
# deploy_remote.sh
# Usage: ./deploy_remote.sh [user@machine_ip] [path_to_app]

HOST="${1:-jeremiah@192.168.2.211}"
DIR="${2:-/home/jeremiah/SharedLLM}"

echo "Deploying to $HOST:$DIR..."

ssh "$HOST" << 'EOF'
    cd "/home/jeremiah/SharedLLM"
    echo "→ Fetching latest code..."
    git fetch origin
    git checkout timer
    git pull origin timer
    echo "→ Restarting Docker container..."
    docker compose restart
    echo "→ Waiting for container to be ready..."
    sleep 5
    docker ps --filter name=unified_rag_api
    echo "→ Container status:"
    docker logs --tail 3 unified_rag_api 2>&1
EOF

echo "✅ Deployment complete."
