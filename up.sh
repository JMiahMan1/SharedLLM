#!/bin/bash
# up.sh - SharedLLM Portable Launcher
# Automatically detects host UID/GID and Docker GID to ensure correct permissions.

# Detect IDs
export PUID=$(id -u)
export PGID=$(id -g)
export DOCKER_GID=$(getent group docker | cut -d: -f3)

echo "=== SharedLLM Launcher ==="
echo "User IDs: $PUID:$PGID"
echo "Docker GID: $DOCKER_GID"

# Run Docker Compose
docker compose "$@"
