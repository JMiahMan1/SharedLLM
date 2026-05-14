#!/usr/bin/env bash
# scripts/test_raven_pipeline.sh
# Integration test for Raven hardening: UID check and Kill Switch verification.

set -e

echo "=== Raven Hardening Integration Test ==="

# 1. Verify UID of running containers
echo "[1/3] Checking container UIDs..."
SERVICES=("gateway" "execution" "workspace_runtime")
for SVC in "${SERVICES[@]}"; do
    CONTAINER_NAME="sharedllm_${SVC}"
    UID_RUNNING=$(docker exec "$CONTAINER_NAME" id -u)
    if [ "$UID_RUNNING" == "1000" ]; then
        echo "  [OK] $SVC is running as UID 1000"
    else
        echo "  [WARNING] $SVC is running as UID $UID_RUNNING (Expected 1000)"
    fi
done

# 2. Verify Redis Connectivity & Kill Key
echo -e "\n[2/3] Checking Redis Kill Switch path..."
# Check if we can SET a test kill key
docker exec sharedllm_redis redis-cli SET raven:mission:kill:test-123 1 > /dev/null
VAL=$(docker exec sharedllm_redis redis-cli GET raven:mission:kill:test-123)
if [ "$VAL" == "1" ]; then
    echo "  [OK] Redis kill switch keys are writable and readable."
else
    echo "  [ERROR] Redis kill switch test failed."
fi
docker exec sharedllm_redis redis-cli DEL raven:mission:kill:test-123 > /dev/null

# 3. Verify Identity Configuration
echo -e "\n[3/3] Checking Identity Service for Raven defaults..."
# Check if the fast_path_threshold is seeded
docker exec sharedllm_identity sqlite3 /data/identity.db "SELECT value FROM globalsetting WHERE key='fast_path_threshold';" | grep "0.85" > /dev/null
if [ $? -eq 0 ]; then
    echo "  [OK] Identity service has correct Raven defaults (fast_path_threshold=0.85)"
else
    echo "  [WARNING] Identity service missing fast_path_threshold default."
fi

echo -e "\n=== Test Complete ==="
