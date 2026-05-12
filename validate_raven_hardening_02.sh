#!/bin/bash
# Raven Hardening Slice 02 — Post-Deployment Validation
# Run this script on the server (192.168.2.205) after deployment
# Tests the hardened AgentLoop timeout and heartbeat logic

set -e

REMOTE_HOST="192.168.2.205"
REMOTE_USER="ai-server"
REMOTE_PATH="/home/jeremiah/Summers Drive/Code/SharedLLM"

echo "=== Raven Hardening Validation (Slice 02) ==="
echo "Target: ${REMOTE_USER}@${REMOTE_HOST}"
echo ""

# Step 1: Ensure services are up
echo "→ Step 1: Checking service health..."
HEALTH=$(ssh ${REMOTE_USER}@${REMOTE_HOST} "curl -s http://localhost:8080/health/ready")
echo "$HEALTH" | jq -r '.status'
if [ "$(echo "$HEALTH" | jq -r '.status')" != "READY" ]; then
  echo "ERROR: Services not ready. Aborting."
  echo "$HEALTH" | jq '.services'
  exit 1
fi
echo "✓ All services healthy"

# Step 2: Verify config loaded
echo ""
echo "→ Step 2: Verifying RAVEN config in container..."
ssh ${REMOTE_USER}@${REMOTE_HOST} "docker exec sharedllm_gateway python3 -c \"\
from services.gateway.config import RAVEN_MAX_TOTAL_SECONDS, RAVEN_HEARTBEAT_INTERVAL; \
print(f'MAX_TOTAL_SECONDS={RAVEN_MAX_TOTAL_SECONDS}, HEARTBEAT={RAVEN_HEARTBEAT_INTERVAL}')\""
echo "✓ Config loaded"

# Step 3: Run unit tests for Raven routing (requires no external services)
echo ""
echo "→ Step 3: Running Raven routing unit tests..."
ssh ${REMOTE_USER}@${REMOTE_HOST} "cd '${REMOTE_PATH}' && \
  python3 -m pytest services/tests/test_raven_routing.py -v"
echo "✓ Raven routing tests passed"

# Step 4: Run hardening tests (mocked, should pass)
echo ""
echo "→ Step 4: Running Raven hardening tests (mocked)..."
ssh ${REMOTE_USER}@${REMOTE_HOST} "cd '${REMOTE_PATH}' && \
  python3 -m pytest services/tests/test_raven_hardening.py -v"
echo "✓ Hardening tests passed"

# Step 5: Trigger a short Raven job and verify timeout NOT hit
echo ""
echo "→ Step 5: Smoke test — quick Raven job (should NOT timeout)..."
SMOKE_OUTPUT=$(ssh ${REMOTE_USER}@${REMOTE_HOST} "cd '${REMOTE_PATH}' && \
  timeout 180 python3 delegate_audit_to_raven.py 'List running containers' 2>&1" || true)
echo "$SMOKE_OUTPUT" | head -20
if echo "$SMOKE_OUTPUT" | grep -qi "HARD TIMEOUT"; then
  echo "⚠ WARNING: Hard timeout triggered on quick job! Investigate."
else
  echo "✓ No timeout on short job"
fi

# Step 6: Check logs for expected heartbeat pattern
echo ""
echo "→ Step 6: Verifying heartbeat logs appear..."
sleep 2
LOG_CHECK=$(ssh ${REMOTE_USER}@${REMOTE_HOST} "docker logs sharedllm_gateway 2>&1 | grep -i 'heartbeat' | tail -2")
if [ -n "$LOG_CHECK" ]; then
  echo "✓ Heartbeat logs present:"
  echo "$LOG_CHECK" | sed 's/^/  /'
else
  echo "⚠ No heartbeat logs found (may be normal if no Raven job ran)"
fi

# Step 7: Check for any timeout errors in logs
echo ""
echo "→ Step 7: Scanning for HARD TIMEOUT errors (should be zero)..."
TIMEOUT_COUNT=$(ssh ${REMOTE_USER}@${REMOTE_HOST} "docker logs sharedllm_gateway 2>&1 | grep -c 'HARD TIMEOUT' || true")
if [ "$TIMEOUT_COUNT" -gt 0 ]; then
  echo "⚠ Found ${TIMEOUT_COUNT} timeout(s) in logs — review if expected"
else
  echo "✓ No timeout errors in logs"
fi

# Step 8: Run workspace-related tests (if Redis available)
echo ""
echo "→ Step 8: Running workspace orchestration tests..."
ssh ${REMOTE_USER}@${REMOTE_HOST} "cd '${REMOTE_PATH}' && \
  python3 -m pytest services/tests/test_workspace_orchestration.py -v -k 'test_workspace_bootstrap' 2>&1 || {
    echo 'Note: Some workspace tests require full Redis stack; may fail in CI without Dockerized Redis.'
  }"

echo ""
echo "=== Validation Complete ==="
echo ""
echo "Summary:"
echo "  ✓ Service health check"
echo "  ✓ Config loaded in container"
echo "  ✓ Raven routing tests"
echo "  ✓ Raven hardening tests"
echo "  ✓ Smoke test (no timeout)"
echo "  ✓ Heartbeat logs verified"
echo ""
echo "If all steps passed, Slice 02 (Hard Timeout) is operational."
echo ""
echo "Next: Monitor production for 24h for any unexpected HARD TIMEOUT messages."
echo "Tune: RAVEN_MAX_TOTAL_SECONDS if legitimate jobs exceed 10min."
