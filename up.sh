#!/bin/bash
# up.sh - SharedLLM Portable Launcher
# Automatically detects host UID/GID and Docker GID to ensure correct permissions.

# Detect IDs
export PUID=$(id -u)
export PGID=$(id -g)
export DOCKER_GID=$(getent group docker | cut -d: -f3)

# Fallback if DOCKER_GID is empty (e.g. group not found)
if [ -z "$DOCKER_GID" ]; then
  # Try to find it from the socket itself if getent fails
  DOCKER_GID=$(stat -c '%g' /var/run/docker.sock 2>/dev/null || echo 980)
fi

echo "=== SharedLLM Launcher ==="
echo "User IDs: $PUID:$PGID"
echo "Docker GID: $DOCKER_GID"

# Run Docker Compose
docker compose "$@"
