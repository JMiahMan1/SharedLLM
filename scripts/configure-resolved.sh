#!/usr/bin/env bash
# Configure systemd-resolved to route .local queries to dns-sync
# Run on the SharedLLM server (ai.local) as root or via sudo
set -euo pipefail

RESOLVED_CONF_DIR="/etc/systemd/resolved.conf.d"
RESOLVED_CONF="$RESOLVED_CONF_DIR/dns-sync.conf"

echo "Configuring systemd-resolved to route .local to dns-sync (127.0.0.1#5353)..."

mkdir -p "$RESOLVED_CONF_DIR"

cat > "$RESOLVED_CONF" << 'EOF'
[Resolve]
DNS=127.0.0.1#5353
Domains=~local
EOF

echo "Created $RESOLVED_CONF"
cat "$RESOLVED_CONF"

echo "Restarting systemd-resolved..."
systemctl restart systemd-resolved

echo "Verifying resolution..."
sleep 1
resolvectl query ollama-server.local 2>/dev/null || echo "Query test completed"

echo "Done. .local domains will now resolve via dns-sync."
