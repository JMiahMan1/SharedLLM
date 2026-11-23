#!/usr/bin/env bash
set -euo pipefail

mkdir -p data/network
curl -fsSL https://standards-oui.ieee.org/oui/oui.csv -o data/network/oui.csv
echo "Downloaded data/network/oui.csv"