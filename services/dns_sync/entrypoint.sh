#!/bin/sh
# Start dnsmasq in background, then run dns_sync.py

# Generate initial config if not present
if [ ! -f "$DNS_CONF_PATH" ]; then
    cat > "$DNS_CONF_PATH" <<EOF
# Initial dnsmasq config
server=127.0.0.11
local=/local/
EOF
fi

# Start dnsmasq (not as daemon, keep it in background)
echo "[entrypoint] Starting dnsmasq..."
dnsmasq --keep-in-foreground --no-daemon &
DNsmasq_PID=$!

# Wait for dnsmasq to start
sleep 2

# Run the sync script
echo "[entrypoint] Starting DNS sync sidecar..."
exec python dns_sync.py
