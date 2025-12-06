#!/bin/bash
# deploy_remote.sh
# Usage: ./deploy_remote.sh [user@machine_ip] [path_to_app]

HOST="${1:-jeremiah@192.168.2.211}"
DIR="${2:-/home/jeremiah/SharedLLM}"

echo "Deploying to $HOST:$DIR..."

ssh "$HOST" "cd \"$DIR\" && git fetch origin && git checkout timer && git pull origin timer && docker compose restart"

echo "Deployment complete."
