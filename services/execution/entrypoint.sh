#!/bin/bash
set -e

DNS_SYNC_IP="172.26.0.10"
HOSTNAME="ollama-server.local"

echo "[execution] Resolving $HOSTNAME via DNS-sync ($DNS_SYNC_IP)..."

RESOLVED_IP=$(dig +short +time=2 +tries=1 @$DNS_SYNC_IP $HOSTNAME A 2>/dev/null | head -1)

if [ -n "$RESOLVED_IP" ]; then
    echo "[execution] Resolved $HOSTNAME -> $RESOLVED_IP"
    if ! grep -q "$HOSTNAME" /etc/hosts 2>/dev/null; then
        echo "$RESOLVED_IP $HOSTNAME" >> /etc/hosts
        echo "[execution] Added $HOSTNAME to /etc/hosts"
    else
        sed -i "s/.*$HOSTNAME/$RESOLVED_IP $HOSTNAME/" /etc/hosts
        echo "[execution] Updated $HOSTNAME in /etc/hosts"
    fi
else
    echo "[execution] WARNING: Could not resolve $HOSTNAME via DNS-sync"
fi

exec "$@"
