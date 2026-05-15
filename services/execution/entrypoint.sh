#!/bin/sh
# Dynamically resolve the docker group GID from the mounted socket
# This avoids hardcoding DOCKER_GID in .env and works across different systems
if [ -S /var/run/docker.sock ]; then
    DOCKER_GID=$(stat -c '%g' /var/run/docker.sock 2>/dev/null)
    if [ -n "$DOCKER_GID" ]; then
        # Change the container's docker group GID to match the host's socket GID
        groupmod -g "$DOCKER_GID" docker 2>/dev/null || true
        # Add the sharedllm user to the docker group
        adduser sharedllm docker 2>/dev/null || true
        echo "[entrypoint] Aligned docker group to host GID=$DOCKER_GID"
    fi
fi
exec "$@"
