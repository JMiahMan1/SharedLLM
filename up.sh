#!/usr/bin/env bash
# up.sh - Legacy wrapper for scripts/deploy.sh
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
bash "$REPO_DIR/scripts/deploy.sh" "$@"
