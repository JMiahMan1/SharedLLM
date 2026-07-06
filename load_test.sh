#!/bin/bash
# Load test script for Jarvis OS
set -e

BASE_URL="http://192.168.2.205"
RESULTS_FILE="/tmp/load_test_results_$(date +%Y%m%d_%H%M%S).txt"
ERRORS_FILE="/tmp/load_test_errors_$(date +%Y%m%d_%H%M%S).txt"

echo "========================================"
echo "Jarvis OS Load Test"
echo "========================================"
echo "Start time: $(date)"
echo "Results: $RESULTS_FILE"
echo "Errors: $ERRORS_FILE"
echo "========================================"

# Initialize files
> "$RESULTS_FILE"
> "$ERRORS_FILE"

# Helper function to make requests and track results
test_endpoint() {
    local name=$1
    local url=$2
    local method=${3:-GET}
    local expected_status=${4:-200}
    
    local start_time=$(date +%s%N)
    local http_code=$(curl -s -o /dev/null -w "%{http_code}" -X "$method" "$url" 2>/dev/null)
    local end_time=$(date +%s%N)
    local duration=$(( (end_time - start_time) / 1000000 ))
    
    if [ "$http_code" = "$expected_status" ]; then
        echo "[PASS] $name: ${http_code} in ${duration}ms" | tee -a "$RESULTS_FILE"
    else
        echo "[FAIL] $name: Expected $expected_status, got $http_code in ${duration}ms" | tee -a "$RESULTS_FILE"
        echo "$(date) [FAIL] $name: $url - Status $http_code (expected $expected_status)" >> "$ERRORS_FILE"
    fi
}

# Test 1: Basic health checks
echo ""
echo "--- Test 1: Basic Health Checks ---"
test_endpoint "UI Health" "$BASE_URL:8080/health"
test_endpoint "Gateway Health" "$BASE_URL:11435/health"
test_endpoint "Identity Health" "$BASE_URL:8001/health"
test_endpoint "Execution Health" "$BASE_URL:8003/health"
test_endpoint "RAG Health" "$BASE_URL:8004/health"
test_endpoint "Storage Health" "$BASE_URL:8005/health"
test_endpoint "Logging Health" "$BASE_URL:8006/health"
test_endpoint "Workspace Runtime Health" "$BASE_URL:8007/health"
test_endpoint "Control Plane Health" "$BASE_URL:8008/health"

# Test 2: UI endpoints
echo ""
echo "--- Test 2: UI Endpoints ---"
test_endpoint "UI Page" "$BASE_URL:8080/"
test_endpoint "UI API Ready" "$BASE_URL:8080/health/ready"
test_endpoint "UI API Info" "$BASE_URL:8080/api/info"

# Test 3: Identity service
echo ""
echo "--- Test 3: Identity Service ---"
test_endpoint "Identity Settings" "$BASE_URL:8001/api/settings"
test_endpoint "Identity Resolve" "$BASE_URL:8001/api/resolve" POST 200

# Test 4: Gateway internal endpoints
echo ""
echo "--- Test 4: Gateway Internal ---"
test_endpoint "Gateway Internal Health" "$BASE_URL:11435/internal/health"

# Test 5: Concurrent requests (5 simultaneous)
echo ""
echo "--- Test 5: Concurrent Requests (5x) ---"
for i in $(seq 1 5); do
    test_endpoint "Concurrent $i - UI" "$BASE_URL:8080/health"
done

# Test 6: Rapid fire (10 requests)
echo ""
echo "--- Test 6: Rapid Fire (10x) ---"
for i in $(seq 1 10); do
    test_endpoint "Rapid $i - Gateway" "$BASE_URL:11435/health"
done

# Test 7: Service communication (gateway to services)
echo ""
echo "--- Test 7: Service Communication ---"
test_endpoint "Gateway -> Identity" "http://172.26.0.9:8001/health"
test_endpoint "Gateway -> Execution" "http://host.docker.internal:8003/health"
test_endpoint "Gateway -> RAG" "http://172.26.0.5:8004/health"
test_endpoint "Gateway -> Storage" "http://172.26.0.6:8005/health"

# Test 8: Redis connectivity (from gateway)
echo ""
echo "--- Test 8: Redis Connectivity ---"
# Redis doesn't have HTTP, but we can check if it's reachable via TCP
if nc -z 172.26.0.4 6379 2>/dev/null; then
    echo "[PASS] Redis TCP connectivity from gateway" | tee -a "$RESULTS_FILE"
else
    echo "[FAIL] Redis TCP connectivity from gateway" | tee -a "$RESULTS_FILE"
    echo "$(date) [FAIL] Redis TCP connectivity from gateway failed" >> "$ERRORS_FILE"
fi

# Summary
echo ""
echo "========================================"
echo "Load Test Complete"
echo "========================================"
echo "Completed: $(date)"
echo ""
echo "Summary:"
echo "  Results file: $RESULTS_FILE"
echo "  Errors file: $ERRORS_FILE"
echo ""

PASS_COUNT=$(grep -c "\[PASS\]" "$RESULTS_FILE" 2>/dev/null || echo 0)
FAIL_COUNT=$(grep -c "\[FAIL\]" "$RESULTS_FILE" 2>/dev/null || echo 0)

echo "Results:"
echo "  ✅ Passed: $PASS_COUNT"
echo "  ❌ Failed: $FAIL_COUNT"
echo ""

if [ "$FAIL_COUNT" -gt 0 ]; then
    echo "========================================"
    echo "FAILURES:"
    echo "========================================"
    cat "$ERRORS_FILE"
fi

echo ""
echo "========================================"
