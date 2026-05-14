#!/bin/bash
set -e

echo "=== RUNNING CI-SAFE UNIT TESTS ==="

# Define services with tests
SERVICES=("identity" "storage" "workspace_runtime" "execution" "rag" "gateway")

ROOT_DIR=$(pwd)

for SVC in "${SERVICES[@]}"; do
    echo "--- Testing Service: $SVC ---"
    cd "services/$SVC"
    PYTHONPATH="$PYTHONPATH:$ROOT_DIR:." pytest tests/
    cd "$ROOT_DIR"
done

echo "=== ALL UNIT TESTS PASSED ==="
