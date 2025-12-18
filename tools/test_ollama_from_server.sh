#!/bin/bash
# Test Ollama connectivity from the RAG server
# Usage: Run this script on the RAG server (192.168.2.211)

echo "Testing Ollama connectivity from RAG server..."
echo "================================================"

if [ -z "$OLLAMA_URL" ]; then
    echo "ERROR: OLLAMA_URL not set"
    exit 1
fi

echo "1. Testing basic connectivity..."
if curl -s --max-time 5 "${OLLAMA_URL}/api/tags" > /dev/null; then
    echo "   [OK] Basic connectivity OK"
else
    echo "   [FAIL] Cannot reach Ollama"
    exit 1
fi

echo "2. Checking Ollama version..."
VERSION=$(curl -s --max-time 5 "${OLLAMA_URL}/api/version" | jq -r '.version' 2>/dev/null || echo "unknown")
echo "   [OK] Version: $VERSION"

echo "3. Testing model generation (this may take time if model needs loading)..."
echo "   [INFO] Sending test request (timeout: 60s)..."
RESPONSE=$(curl -s --max-time 60 -X POST "${OLLAMA_URL}/api/generate" \
    -H "Content-Type: application/json" \
    -d '{
        "model": "qwen2.5:latest",
        "prompt": "Say OK",
        "stream": false,
        "options": {"num_predict": 5}
    }')

if [ $? -eq 0 ]; then
    echo "   [OK] Generation successful"
    echo "$RESPONSE" | jq -r '.response' 2>/dev/null || echo "$RESPONSE"
else
    echo "   [FAIL] Generation failed or timed out"
    echo "   This confirms the timeout issue!"
    exit 1
fi

echo ""
echo "[OK] All tests passed from server side"

