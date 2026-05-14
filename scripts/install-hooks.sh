#!/usr/bin/env bash
# =============================================================================
# Install SharedLLM Git Hooks
# Run this once on any machine where you want auto-deploy on git pull.
# Usage: bash scripts/install-hooks.sh
# =============================================================================
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HOOKS_DIR="$REPO_DIR/.git/hooks"
SCRIPTS_DIR="$REPO_DIR/scripts"

echo "Installing SharedLLM git hooks..."
echo "Repo:  $REPO_DIR"
echo "Hooks: $HOOKS_DIR"

# post-merge: fires after a successful git pull that downloads new commits
install_hook() {
    local hook_name="$1"
    local source_file="$SCRIPTS_DIR/${hook_name}.hook"
    local dest_file="$HOOKS_DIR/$hook_name"

    if [ ! -f "$source_file" ]; then
        echo "WARN: Hook source not found: $source_file — skipping."
        return
    fi

    cp "$source_file" "$dest_file"
    chmod +x "$dest_file"
    echo "  ✅ Installed: .git/hooks/$hook_name"
}

install_hook "post-merge"

echo ""
echo "Done. git pull on this machine will now automatically:"
echo "  1. Detect if Dockerfile/requirements changed (rebuild) or not (restart)"
echo "  2. Restart the rag-api container"
echo "  3. Wait for the API to become healthy"
echo "  4. Re-ingest Home Assistant devices"
echo ""
echo "Deploy logs are written to: data/deploy.log"
echo "To trigger a manual deploy at any time: bash scripts/deploy.sh"
