#!/bin/bash
# run_local_tests.sh

echo "=== SharedLLM Deep Local Functionality Tests ==="

wait_for_readiness() {
    local url="http://localhost:11435/health/ready"
    local max_attempts=60
    local attempt=1

    echo "Waiting for SOA Stack Readiness ($url)..."
    while [ $attempt -le $max_attempts ]; do
        resp=$(curl -s "$url")
        if echo "$resp" | grep -q '"status":"READY"' ; then
            echo "STACK IS READY!"
            return 0
        fi
        
        # Show what's NOT ready
        not_ready=$(echo "$resp" | jq -r '.services | to_entries[] | select(.value != "OK") | .key' 2>/dev/null)
        if [ -n "$not_ready" ]; then
            echo -n " [Still waiting for: $not_ready] "
        else
            echo -n "."
        fi
        
        sleep 5
        attempt=$((attempt + 1))
    done
    echo " FAILED after $max_attempts attempts."
    echo "Last response: $resp"
    return 1
}

# 1. Wait for global readiness
wait_for_readiness || exit 1

# 2. Run Pytest
echo -e "\nRunning Identity and Auth Database Verification..."
pytest test/local/test_auth_identity.py -s

echo -e "\nRunning Hardware and Service State Verification..."
pytest test/local/test_hardware_state.py -s

echo -e "\nRunning RAG Sync Loop Verification..."
pytest test/local/test_rag_sync.py -s

echo -e "\n=== Tests Complete ==="
