#!/bin/sh
# Entrypoint: resolve DNS mappings from Identity and write to /etc/hosts
# Then exec the original CMD.

IDENTITY_URL="${IDENTITY_SVC_URL:-http://identity:8001}"
INTERNAL_SECRET="${INTERNAL_SECRET}"

echo "[dns-entrypoint] Fetching DNS mappings from Identity..."

# Fetch mappings from Identity
MAPPINGS=$(curl -sf -H "X-Internal-Secret: $INTERNAL_SECRET" "$IDENTITY_URL/api/settings/dns_mappings" 2>/dev/null)

if [ -n "$MAPPINGS" ]; then
    # Parse JSON and write to /etc/hosts
    # Format: {"hostname": ["ip1", "ip2"], ...}
    echo "$MAPPINGS" | python3 -c "
import sys, json
try:
    data = json.loads(sys.stdin.read())
    val = data.get('value', data) if isinstance(data, dict) else data
    mappings = json.loads(val) if isinstance(val, str) else val
    for hostname, ips in mappings.items():
        if isinstance(ips, str):
            ips = [ips]
        elif not isinstance(ips, list):
            continue
        for ip in ips:
            if ip:
                print(f'{ip}\t{hostname}')
except Exception as e:
    print(f'[dns-entrypoint] Parse error: {e}', file=sys.stderr)
" >> /etc/hosts
    echo "[dns-entrypoint] DNS mappings written to /etc/hosts:"
    grep -E 'ollama|llama|ai\.' /etc/hosts 2>/dev/null || true
else
    echo "[dns-entrypoint] WARNING: Could not fetch DNS mappings"
fi

exec "$@"
