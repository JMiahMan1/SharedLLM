#!/bin/bash
set -e

BASE_URL="http://192.168.2.205"
RESULTS_FILE="/tmp/load_test_results_$(date +%Y%m%d_%H%M%S).txt"
ERRORS_FILE="/tmp/load_test_errors_$(date +%Y%m%d_%H%M%S).txt"

echo "========================================"
echo "COMPREHENSIVE LOAD TEST - Jarvis OS"
echo "========================================"
echo "Start: $(date)"
echo "========================================"

> "$RESULTS_FILE"
> "$ERRORS_FILE"

test_ep() {
    local name=$1
    local url=$2
    local method=${3:-GET}
    local code duration
    local start=$(date +%s%N)
    code=$(curl -s -o /dev/null -w "%{http_code}" -X "$method" "$url" 2>/dev/null)
    local end=$(date +%s%N)
    duration=$(( (end - start) / 1000000 ))
    
    if [ "$code" != "000" ]; then
        echo "[PASS] $name: HTTP $code in ${duration}ms" >> "$RESULTS_FILE"
    else
        echo "[FAIL] $name: Connection failed in ${duration}ms" >> "$RESULTS_FILE"
        echo "$(date) [FAIL] $name: $url" >> "$ERRORS_FILE"
    fi
}

echo ""
echo "--- Phase 1: Individual Service Health (from host) ---"
for svc_port in "identity:8001" "rag:8004" "storage:8005" "logging:8006" "workspace_runtime:8007" "control_plane:8008" "gateway:11435"; do
    name=${svc_port%%:*}
    port=${svc_port##*:}
    test_ep "$name" "http://192.168.2.205:$port/health"
done

echo ""
echo "--- Phase 2: UI Endpoints ---"
test_ep "UI Page" "http://192.168.2.205:8080/"
test_ep "UI Health" "http://192.168.2.205:8080/health"
test_ep "UI Health Ready" "http://192.168.2.205:8080/health/ready"

echo ""
echo "--- Phase 3: Gateway Internal ---"
test_ep "Gateway Health" "http://192.168.2.205:11435/health"
test_ep "Gateway Internal Health" "http://192.168.2.205:11435/internal/health"

echo ""
echo "--- Phase 3.5: Authenticated API Endpoints ---"
test_ep "Identity Settings (no auth)" "http://192.168.2.205:8001/api/settings"
test_ep "Identity Resolve (no auth)" "http://192.168.2.205:8001/api/resolve" POST
test_ep "UI API Info" "http://192.168.2.205:8080/api/info"
test_ep "UI API Missions" "http://192.168.2.205:8080/api/raven/missions"
test_ep "UI API Timers" "http://192.168.2.205:8080/api/communication/timers"

echo ""
echo "--- Phase 4: Concurrent Load (20 requests) ---"
for i in $(seq 1 20); do
    test_ep "Concurrent $i" "http://192.168.2.205:8080/health" &
done
wait

echo ""
echo "--- Phase 5: Gateway Health Rapid (50 requests) ---"
for i in $(seq 1 50); do
    test_ep "Rapid $i" "http://192.168.2.205:11435/health" &
done
wait

echo ""
echo "--- Phase 6: Mixed Service Load ---"
for i in $(seq 1 30); do
    test_ep "Mixed $i - UI" "http://192.168.2.205:8080/health" &
    test_ep "Mixed $i - Gateway" "http://192.168.2.205:11435/health" &
done
wait

echo ""
echo "--- Phase 7: Service-to-Service Communication (from gateway) ---"
if ssh jeremiah@192.168.2.205 "docker exec sharedllm_gateway curl -s --connect-timeout 2 http://identity:8001/health" > /dev/null 2>&1; then
    echo "[PASS] Gateway -> Identity" >> "$RESULTS_FILE"
else
    echo "[FAIL] Gateway -> Identity" >> "$RESULTS_FILE"
    echo "$(date) [FAIL] Gateway -> Identity" >> "$ERRORS_FILE"
fi

if ssh jeremiah@192.168.2.205 "docker exec sharedllm_gateway curl -s --connect-timeout 2 http://execution:8003/health" > /dev/null 2>&1; then
    echo "[PASS] Gateway -> Execution (Docker DNS)" >> "$RESULTS_FILE"
else
    echo "[FAIL] Gateway -> Execution (Docker DNS)" >> "$RESULTS_FILE"
    echo "$(date) [FAIL] Gateway -> Execution (Docker DNS)" >> "$ERRORS_FILE"
fi

if ssh jeremiah@192.168.2.205 "docker exec sharedllm_gateway curl -s --connect-timeout 2 http://host.docker.internal:8003/health" > /dev/null 2>&1; then
    echo "[PASS] Gateway -> Execution (host.docker.internal)" >> "$RESULTS_FILE"
else
    echo "[FAIL] Gateway -> Execution (host.docker.internal)" >> "$RESULTS_FILE"
    echo "$(date) [FAIL] Gateway -> Execution (host.docker.internal)" >> "$ERRORS_FILE"
fi

if ssh jeremiah@192.168.2.205 "docker exec sharedllm_gateway curl -s --connect-timeout 2 http://rag:8004/health" > /dev/null 2>&1; then
    echo "[PASS] Gateway -> RAG" >> "$RESULTS_FILE"
else
    echo "[FAIL] Gateway -> RAG" >> "$RESULTS_FILE"
    echo "$(date) [FAIL] Gateway -> RAG" >> "$ERRORS_FILE"
fi

if ssh jeremiah@192.168.2.205 "docker exec sharedllm_gateway curl -s --connect-timeout 2 http://storage:8005/health" > /dev/null 2>&1; then
    echo "[PASS] Gateway -> Storage" >> "$RESULTS_FILE"
else
    echo "[FAIL] Gateway -> Storage" >> "$RESULTS_FILE"
    echo "$(date) [FAIL] Gateway -> Storage" >> "$ERRORS_FILE"
fi

if ssh jeremiah@192.168.2.205 "docker exec sharedllm_gateway curl -s --connect-timeout 2 http://logging:8006/health" > /dev/null 2>&1; then
    echo "[PASS] Gateway -> Logging" >> "$RESULTS_FILE"
else
    echo "[FAIL] Gateway -> Logging" >> "$RESULTS_FILE"
    echo "$(date) [FAIL] Gateway -> Logging" >> "$ERRORS_FILE"
fi

if ssh jeremiah@192.168.2.205 "docker exec sharedllm_gateway curl -s --connect-timeout 2 http://workspace_runtime:8007/health" > /dev/null 2>&1; then
    echo "[PASS] Gateway -> Workspace Runtime" >> "$RESULTS_FILE"
else
    echo "[FAIL] Gateway -> Workspace Runtime" >> "$RESULTS_FILE"
    echo "$(date) [FAIL] Gateway -> Workspace Runtime" >> "$ERRORS_FILE"
fi

if ssh jeremiah@192.168.2.205 "docker exec sharedllm_gateway curl -s --connect-timeout 2 http://control_plane:8008/health" > /dev/null 2>&1; then
    echo "[PASS] Gateway -> Control Plane" >> "$RESULTS_FILE"
else
    echo "[FAIL] Gateway -> Control Plane" >> "$RESULTS_FILE"
    echo "$(date) [FAIL] Gateway -> Control Plane" >> "$ERRORS_FILE"
fi

echo ""
echo "--- Phase 8: Redis Connectivity (from gateway) ---"
if ssh jeremiah@192.168.2.205 "docker exec sharedllm_gateway nc -z redis 6379 2>/dev/null"; then
    echo "[PASS] Gateway -> Redis TCP" >> "$RESULTS_FILE"
else
    echo "[FAIL] Gateway -> Redis TCP" >> "$RESULTS_FILE"
    echo "$(date) [FAIL] Gateway -> Redis TCP" >> "$ERRORS_FILE"
fi

echo ""
echo "========================================"
echo "POST-LOAD TEST HEALTH CHECK"
echo "========================================"

for svc_port in "identity:8001" "execution:8003" "rag:8004" "storage:8005" "logging:8006" "workspace_runtime:8007" "control_plane:8008" "gateway:11435"; do
    name=${svc_port%%:*}
    port=${svc_port##*:}
    code=$(curl -s -o /dev/null -w "%{http_code}" "http://192.168.2.205:$port/health" 2>/dev/null)
    if [ "$code" = "200" ]; then
        echo "[OK] $name: HEALTHY" >> "$RESULTS_FILE"
    else
        echo "[CRITICAL] $name: UNHEALTHY (HTTP $code)" >> "$RESULTS_FILE"
        echo "$(date) [CRITICAL] $name post-load: UNHEALTHY" >> "$ERRORS_FILE"
    fi
done

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
    echo "ALL FAILURES:"
    echo "========================================"
    grep "\[FAIL\]\|\[CRITICAL\]" "$RESULTS_FILE" 2>/dev/null || true
    echo ""
    if [ -s "$ERRORS_FILE" ]; then
        echo "ERROR LOG:"
        cat "$ERRORS_FILE"
    fi
fi
echo "========================================"
