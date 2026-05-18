#!/bin/sh
# Entrypoint wrapper: resolve dns-sync container IP and prepend to resolv.conf
# This avoids hardcoding the DNS server IP in docker-compose.yml

DNS_SERVICE="${DNS_SERVICE_NAME:-dns-sync}"
MAX_RETRIES=30
RETRY_DELAY=1

echo "[entrypoint] Resolving DNS server: ${DNS_SERVICE}..."

for i in $(seq 1 $MAX_RETRIES); do
    DNS_IP=$(getent hosts "$DNS_SERVICE" | awk '{print $1}' | head -1)
    if [ -n "$DNS_IP" ]; then
        echo "[entrypoint] Resolved ${DNS_SERVICE} -> ${DNS_IP}"
        # Prepend to resolv.conf (preserve existing nameservers as fallback)
        EXISTING=$(cat /etc/resolv.conf 2>/dev/null | grep -v "^nameserver" || true)
        echo "nameserver ${DNS_IP}" > /etc/resolv.conf
        echo "$EXISTING" >> /etc/resolv.conf
        echo "[entrypoint] Updated /etc/resolv.conf:"
        cat /etc/resolv.conf
        break
    fi
    echo "[entrypoint] Attempt $i/$MAX_RETRIES: ${DNS_SERVICE} not yet resolvable, retrying in ${RETRY_DELAY}s..."
    sleep $RETRY_DELAY
done

if [ -z "$DNS_IP" ]; then
    echo "[entrypoint] WARNING: Could not resolve ${DNS_SERVICE}, using default DNS"
fi

# Execute the original CMD
exec "$@"
