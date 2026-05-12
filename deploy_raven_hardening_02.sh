#!/bin/bash
# Raven Hardening Slice 02 — Deployment Script
# This script deploys the changes to 192.168.2.205 (ai.local)
# Prerequisites: SSH access to ai-server user, docker-compose installed

set -e  # exit on error

REMOTE_HOST="192.168.2.205"
REMOTE_USER="ai-server"
REMOTE_PATH="/home/jeremiah/Summers Drive/Code/SharedLLM"
BRANCH="microservices"

echo "=== Raven Hardening Slice 02 Deploy ==="
echo "Target: ${REMOTE_USER}@${REMOTE_HOST}"
echo "Branch: ${BRANCH}"
echo ""

# Step 1: Ensure remote repo is clean
echo "→ Checking remote git status..."
ssh ${REMOTE_USER}@${REMOTE_HOST} "cd '${REMOTE_PATH}' && git status --porcelain" || {
  echo "ERROR: Cannot access remote. Check SSH/network."
  exit 1
}

# Step 2: Pull latest
echo "→ Pulling latest from origin/${BRANCH}..."
ssh ${REMOTE_USER}@${REMOTE_HOST} "cd '${REMOTE_PATH}' && \
  git checkout ${BRANCH} && \
  git pull origin ${BRANCH}" || {
  echo "ERROR: Git pull failed."
  exit 1
}

# Step 3: Verify commit
echo "→ Verifying commit..."
ssh ${REMOTE_USER}@${REMOTE_HOST} "cd '${REMOTE_PATH}' && \
  git log --oneline -1 | grep -q 'feat(raven): implement hard timeout' && \
  echo '✓ Correct commit checked out' || {
    echo 'ERROR: Expected commit not found.'
    exit 1
  }"

# Step 4: Restart gateway service
echo "→ Restarting gateway service..."
ssh ${REMOTE_USER}@${REMOTE_HOST} "cd '${REMOTE_PATH}' && \
  docker-compose restart gateway" || {
  echo "ERROR: docker-compose restart failed."
  exit 1
}

# Step 5: Wait for health
echo "→ Waiting for gateway to become healthy..."
for i in {1..30}; do
  status=$(ssh ${REMOTE_USER}@${REMOTE_HOST} "curl -s http://localhost:8080/health | jq -r '.status' 2>/dev/null" || echo "down")
  if [ "$status" = "ok" ] || [ "$status" = "READY" ]; then
    echo "✓ Gateway healthy after ${i}s"
    break
  fi
  printf "."
  sleep 2
done

# Step 6: Quick smoke test
echo ""
echo "→ Running smoke test..."
ssh ${REMOTE_USER}@${REMOTE_HOST} "cd '${REMOTE_PATH}' && \
  python3 delegate_audit_to_raven.py 'Smoke test — are you operational?' --timeout 120" | tee /tmp/raven_smoke.log || {
  echo "WARNING: Smoke test timed out or failed — check logs."
}

# Step 7: Check for timeout errors
if grep -q "HARD TIMEOUT" /tmp/raven_smoke.log; then
  echo "⚠ WARNING: Hard timeout triggered on smoke test. Investigate."
else
  echo "✓ No timeout errors detected"
fi

echo ""
echo "=== Deployment Complete ==="
echo ""
echo "Post-deployment checks:"
echo "1. Monitor gateway logs: ssh ${REMOTE_USER}@${REMOTE_HOST} 'docker-compose logs -f gateway'"
echo "2. Verify Raven jobs in UI: http://192.168.2.205:8080/lab"
echo "3. Check for 'HARD TIMEOUT' warnings in logs (should be rare)"
echo ""
echo "Rollback (if needed):"
echo "  ssh ${REMOTE_USER}@${REMOTE_HOST} 'cd \"${REMOTE_PATH}\" && git reset --hard c602277 && docker-compose restart gateway'"
echo ""
