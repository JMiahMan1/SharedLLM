#!/bin/bash
API_URL="http://localhost:11435/api/chat"
H_TYPE="Content-Type: application/json"

echo "--- 1. Create Note ---"
curl -s -X POST "$API_URL" -H "$H_TYPE" -d '{"query": "Create a note called RemoteTest with content: First Draft", "model": "qwen3:latest"}' | jq -r '.message.content'
echo ""

sleep 2

echo "--- 2. Read Note (Expect: First Draft) ---"
curl -s -X POST "$API_URL" -H "$H_TYPE" -d '{"query": "Read the note called RemoteTest", "model": "qwen3:latest"}' | jq -r '.message.content'
echo ""

sleep 2

echo "--- 3. Update Note (Expect Success) ---"
curl -s -X POST "$API_URL" -H "$H_TYPE" -d '{"query": "Update the note RemoteTest. Change content to: Second Draft", "model": "qwen3:latest"}' | jq -r '.message.content'
echo ""

sleep 2

echo "--- 4. Read Updated Note (Expect: Second Draft) ---"
curl -s -X POST "$API_URL" -H "$H_TYPE" -d '{"query": "Read note RemoteTest", "model": "qwen3:latest"}' | jq -r '.message.content'
echo ""

echo "--- 5. Delete Note ---"
curl -s -X POST "$API_URL" -H "$H_TYPE" -d '{"query": "Delete note RemoteTest", "model": "qwen3:latest"}' | jq -r '.message.content'
echo ""
