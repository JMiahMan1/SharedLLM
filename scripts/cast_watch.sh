#!/usr/bin/env bash
# cast_watch.sh
# Usage (on the server, from the checkout): scripts/cast_watch.sh start <device address> | stop | status
#
# Starts scripts/cast_watch.py (a read-only Cast watcher, see its docstring) in
# the hub's image, on the host network (Cast and mDNS need it), restarting
# with the server. Events go to data/diagnostics/cast_watch.jsonl. The
# telemetry address and INTERNAL_SECRET are taken from the running hub's
# environment, so nothing secret is written here or put on a command line.

set -euo pipefail
cd "$(dirname "$0")/.."
NAME=sharedllm_cast_watch

case "${1:-}" in
  start)
    HOST="${2:?give the Cast device address}"
    mkdir -p data/diagnostics
    IMAGE=$(docker inspect -f '{{.Config.Image}}' sharedllm_homehub)
    docker rm -f "$NAME" >/dev/null 2>&1 || true
    docker exec sharedllm_homehub sh -c 'printf "TELEMETRY_SVC_URL=%s\nINTERNAL_SECRET=%s\n" "$HOST_TELEMETRY_SVC_URL" "$INTERNAL_SECRET"' \
      > data/diagnostics/.cast_watch.env
    chmod 600 data/diagnostics/.cast_watch.env
    docker run -d --name "$NAME" --restart unless-stopped --network host \
      --env-file data/diagnostics/.cast_watch.env \
      -e CAST_WATCH_HOST="$HOST" \
      -e CAST_WATCH_NETWORK_DB=/net/router.db \
      -e CAST_WATCH_OUT=/out/cast_watch.jsonl \
      -v "$PWD/scripts/cast_watch.py:/cast_watch.py:ro,z" \
      -v "$PWD/data/network:/net:ro,z" \
      -v "$PWD/data/diagnostics:/out:z" \
      --entrypoint python "$IMAGE" /cast_watch.py
    ;;
  stop)
    docker rm -f "$NAME" && rm -f data/diagnostics/.cast_watch.env
    ;;
  status)
    docker ps --filter "name=$NAME" --format '{{.Names}} {{.Status}}'
    docker logs --tail 20 "$NAME"
    ;;
  *)
    echo "Usage: $0 start <device address> | stop | status" >&2
    exit 2
    ;;
esac
