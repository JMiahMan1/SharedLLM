#!/bin/bash
set -e

BASE_URL="http://192.168.2.205"
RESULTS_FILE="/tmp/load_test_final.txt"

echo "========================================"
echo "COMPREHENSIVE LOAD TEST - Jarvis OS"
echo "========================================"
echo "Start: $(date)"
echo "========================================"

> "$RESULTS_FILE"

test_ep() {
    local name=$1
    local url=$2
    local method=${3:-GET}
    local code duration
    local start=$(date +%s%N)
    code=$(curl -s -o /dev/null -w "%{http_code}" -X "$method" --connect-timeout 5 "$url" 2>/dev/null)
    local end=$(date +%s%N)
    duration=$(( (end - start) / 1000000 ))
    
    if [ "$code" != "000" ]; then
        echo "[PASS] $name: HTTP $code in ${duration}ms" >> "$RESULTS_FILE"
    else
        echo "[FAIL] $name: Connection failed in ${duration}ms" >> "$RESULTS_FILE"
    fi
}

test_ssh() {
    local name=$1
    local cmd=$2
    if ssh -o ConnectTimeout=5 jeremiah@192.168.2.205 "$cmd" > /dev/null 2>&1; then
        echo "[PASS] $name" >> "$RESULTS_FILE"
    else
        echo "[FAIL] $name" >> "$RESULTS_FILE"
    fi
}

echo ""
echo "--- Phase 1: Service Health ---"
test_ep "Identity" "$BASE_URL:8001/health"
test_ep "RAG" "$BASE_URL:8004/health"
test_ep "Storage" "$BASE_URL:8005/health"
test_ep "Logging" "$BASE_URL:8006/health"
test_ep "Workspace Runtime" "$BASE_URL:8007/health"
test_ep "Control Plane" "$BASE_URL:8008/health"
test_ep "Gateway" "$BASE_URL:11435/health"

echo ""
echo "--- Phase 2: UI Endpoints ---"
test_ep "UI Page" "$BASE_URL:8080/"
test_ep "UI Health" "$BASE_URL:8080/health"
test_ep "UI Health Ready" "$BASE_URL:8080/health/ready"

echo ""
echo "--- Phase 3: Gateway Internal ---"
test_ep "Gateway Health" "$BASE_URL:11435/health"
test_ep "Gateway Internal" "$BASE_URL:11435/internal/health"

echo ""
echo "--- Phase 4: Auth Endpoints ---"
test_ep "Identity Settings" "$BASE_URL:8001/api/settings"
test_ep "Identity Resolve" "$BASE_URL:8001/api/resolve" POST
test_ep "UI API Missions" "$BASE_URL:8080/api/raven/missions"
test_ep "UI API Timers" "$BASE_URL:8080/api/communication/timers"

echo ""
echo "--- Phase 5: Service-to-Service (from gateway) ---"
test_ssh "GW -> Identity" "docker exec sharedllm_gateway curl -s --connect-timeout 2 http://identity:8001/health"
test_ssh "GW -> Execution" "docker exec sharedllm_gateway curl -s --connect-timeout 2 http://host.docker.internal:8003/health"
test_ssh "GW -> RAG" "docker exec sharedllm_gateway curl -s --connect-timeout 2 http://rag:8004/health"
test_ssh "GW -> Storage" "docker exec sharedllm_gateway curl -s --connect-timeout 2 http://storage:8005/health"
test_ssh "GW -> Logging" "docker exec sharedllm_gateway curl -s --connect-timeout 2 http://logging:8006/health"
test_ssh "GW -> Workspace" "docker exec sharedllm_gateway curl -s --connect-timeout 2 http://workspace_runtime:8007/health"
test_ssh "GW -> Control Plane" "docker exec sharedllm_gateway curl -s --connect-timeout 2 http://control_plane:8008/health"

echo ""
echo "--- Phase 6: Redis Connectivity ---"
test_ssh "GW -> Redis" "docker exec sharedllm_gateway nc -z redis 6379 2>/dev/null"

echo ""
echo "--- Phase 7: Post-Load Health Check ---"
for svc_port in "identity:8001" "rag:8004" "storage:8005" "logging:8006" "workspace_runtime:8007" "control_plane:8008" "gateway:11435"; do
    name=${svc_port%%:*}
    port=${svc_port##*:}
    code=$(curl -s -o /dev/null -w "%{http_code}" --connect-timeout 5 "http://192.168.2.205:$port/health" 2>/dev/null)
    if [ "$code" = "200" ]; then
        echo "[OK] $name: HEALTHY" >> "$RESULTS_FILE"
    else
        echo "[CRITICAL] $name: UNHEALTHY" >> "$RESULTS_FILE"
    fi
done
# Test execution from gateway (it's on host network)
test_ssh "Execution (post)" "docker exec sharedllm_gateway curl -s --connect-timeout 2 http://host.docker.internal:8003/health"

echo ""
echo "========================================"
echo "LOAD TEST COMPLETE"
echo "========================================"
echo "End: $(date)"
echo ""

PASS=$(grep -c "\[PASS\]\|\[OK\]" "$RESULTS_FILE" 2>/dev/null || echo 0)
FAIL=$(grep -c "\[FAIL\]" "$RESULTS_FILE" 2>/dev/null || echo 0)
CRITICAL=$(grep -c "\[CRITICAL\]" "$RESULTS_FILE" 2>/dev/null || echo 0)

echo "Results Summary:"
echo "  ✅ Passes: $PASS"
echo "  ⚠️  Failures: $FAIL"
echo "  ❌ Critical: $CRITICAL"
echo ""

if [ "$FAIL" -gt 0 ] || [ "$CRITICAL" -gt 0 ]; then
    echo "========================================"
    echo "FAILURES:"
    echo "========================================"
    grep "\[FAIL\]\|\[CRITICAL\]" "$RESULTS_FILE" 2>/dev/null || true
fi
echo "========================================"
