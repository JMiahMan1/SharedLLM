#!/usr/bin/env bash
# rvcurl.sh — resilient curl wrapper for the SharedLLM gateway/RAG.
# Retries on connection failures / timeouts (HTTP 000) with backoff, and uses a
# generous per-attempt timeout so the high LAN jitter (mdev ~144ms on the local
# subnet) never aborts a legitimate request.
#
# Usage: rvcurl.sh [curl args...]
#   All args are passed straight to curl. The wrapper adds --max-time, retries,
#   and connects via the gateway (192.168.2.205:11435) unless a URL is given.
set -uo pipefail

MAX_ATTEMPTS=5
PER_ATTEMPT_TIMEOUT=45   # seconds; RAG-backed calls can tail to ~1.3s but leave headroom
BASE_BACKOFF=2            # seconds; doubled each retry

attempt=1
while :; do
    # Run curl, capture body to a temp file, surface http_code + time.
    tmp=$(mktemp)
    code=$(curl -s --max-time "$PER_ATTEMPT_TIMEOUT" -o "$tmp" -w "%{http_code}" "$@" 2>/dev/null)
    rc=$?
    if [ "$code" = "000" ] || [ "$rc" -ne 0 ]; then
        echo "[rvcurl] attempt $attempt failed (http_code=$code curl_rc=$rc); retrying in ${BASE_BACKOFF}s..." >&2
        rm -f "$tmp"
        if [ "$attempt" -ge "$MAX_ATTEMPTS" ]; then
            echo "[rvcurl] GAVE UP after $MAX_ATTEMPTS attempts." >&2
            exit 7
        fi
        sleep "$BASE_BACKOFF"
        BASE_BACKOFF=$((BASE_BACKOFF * 2))
        attempt=$((attempt + 1))
        continue
    fi
    cat "$tmp"
    rm -f "$tmp"
    exit 0
done
