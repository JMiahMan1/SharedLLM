#!/bin/sh
# Dynamically resolve the docker group GID from the mounted socket
# This avoids hardcoding DOCKER_GID in .env and works across different systems
if [ -S /var/run/docker.sock ]; then
    DOCKER_GID=$(stat -c '%g' /var/run/docker.sock 2>/dev/null)
    if [ -n "$DOCKER_GID" ]; then
        echo "[entrypoint] Docker socket GID=$DOCKER_GID, adding to supplementary groups"
        # Use setpriv to run the process with the docker group as a supplementary group
        exec setpriv --reuid=1000 --regid=1000 --groups=1000,"$DOCKER_GID" -- "$@"
    fi
fi
# Fallback: just drop to the sharedllm user without docker access
exec setpriv --reuid=1000 --regid=1000 --groups=1000 -- "$@"
