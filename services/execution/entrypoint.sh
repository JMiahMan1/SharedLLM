#!/bin/sh
# Dynamically resolve the docker group GID from the mounted socket
# This avoids hardcoding DOCKER_GID in .env and works across different systems
if [ -S /var/run/docker.sock ]; then
    DOCKER_GID=$(stat -c '%g' /var/run/docker.sock 2>/dev/null)
    if [ -n "$DOCKER_GID" ]; then
        # Create a 'docker' group with the host's GID if it doesn't exist
        addgroup -g "$DOCKER_GID" docker 2>/dev/null || true
        # Add the sharedllm user to the docker group
        addgroup sharedllm docker 2>/dev/null || true
        echo "[entrypoint] Added sharedllm to docker group (GID=$DOCKER_GID)"
    fi
fi
exec "$@"
