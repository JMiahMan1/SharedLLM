#!/usr/bin/env bash
# Fetch the IEEE MAC vendor list Admin > Network uses to name a device's maker.
# Run from the stack directory; the control plane reads it from data/network
# (mounted at NETWORK_DATA_DIR) and reads a new or refreshed file on its next pass.
set -euo pipefail

DIR="${1:-data/network}"
mkdir -p "$DIR"
curl -fsSL https://standards-oui.ieee.org/oui/oui.csv -o "$DIR/oui.csv.tmp"
mv "$DIR/oui.csv.tmp" "$DIR/oui.csv"
echo "Downloaded $DIR/oui.csv"
