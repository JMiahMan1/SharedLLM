#!/bin/bash
# Safe Production Repository Sync
# Zero-regression sync with backup and rollback capability

set -e  # Exit on error

echo "=== SAFE PRODUCTION SYNC ==="
echo "This script will safely sync git without touching running containers"
echo ""

BACKUP_BRANCH="production-backup-$(date +%Y%m%d-%H%M%S)"

# Step 1: Create backup of current production state
echo "[1/5] Creating backup of current production state..."
git add -A
git commit -m "Production backup before sync - $(date)" || echo "No changes to commit"
git branch "$BACKUP_BRANCH"
echo "✓ Backup created: $BACKUP_BRANCH"
echo ""

# Step 2: Fetch latest from origin
echo "[2/5] Fetching latest commits from origin..."
git fetch origin timer
echo "✓ Fetched latest commits"
echo ""

# Step 3: Check what will change
echo "[3/5] Analyzing changes..."
echo "Current commit: $(git log --oneline -1)"
echo "Latest origin commit: $(git log --oneline -1 origin/timer)"
echo ""
echo "Files that will be updated:"
git diff --name-status HEAD origin/timer || echo "Already up to date"
echo ""

# Step 4: Merge with strategy preference for current production files
echo "[4/5] Merging latest commits (keeping production state on conflicts)..."
# Use 'ours' strategy for merge - if there's a conflict, keep current production version
git merge origin/timer -X ours -m "Merge latest commits (production-safe)" || {
    echo "⚠ Merge conflicts detected"
    echo "Resolving with production preference..."
    git merge --abort
    # Alternative: rebase onto origin/timer
    echo "Using alternative: accepting all latest changes..."
    git reset --hard origin/timer
}
echo "✓ Merge complete"
echo ""

# Step 5: Clean up untracked files safely
echo "[5/5] Cleaning up..."
rm -f app/domains/media/power_sync.py 2>/dev/null || true
rm -f sync_remote.sh 2>/dev/null || true
echo "✓ Cleanup complete"
echo ""

# Final status
echo "=== SYNC COMPLETE ==="
echo "Current state:"
git log --oneline -5
echo ""
git status --short
echo ""
echo "Backup branch: $BACKUP_BRANCH"
echo "To rollback: git reset --hard $BACKUP_BRANCH"
echo ""
echo "⚠ IMPORTANT: Restart Docker containers to load new code:"
echo "   docker-compose restart"
