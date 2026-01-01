#!/bin/bash
# app/tests/verify_notes.sh
# Intended to be run on the remote deployment server (e.g., 192.168.2.211)

BASE_URL="http://localhost:11435/api/chat"
USER_ID="note_verifier"

echo "=== 1. ADD CHECKBOX NOTE ==="
curl -s -X POST "$BASE_URL" \
  -H "Content-Type: application/json" \
  -H "X-RAG-User: $USER_ID" \
  -d '{"query": "Create a note called Final Checkbox with content: - [ ] Item A\n- [ ] Item B", "model": "qwen3:latest"}'
echo ""

sleep 2

echo -e "\n=== 2. SPECIAL USE: ADD TO SHOPPING LIST ==="
curl -s -X POST "$BASE_URL" \
  -H "Content-Type: application/json" \
  -H "X-RAG-User: $USER_ID" \
  -d '{"query": "Add Bread to my list", "model": "qwen3:latest"}'
echo ""

sleep 2

echo -e "\n=== 3. READ SHOPPING LIST ==="
# Should contain Bread
curl -s -X POST "$BASE_URL" \
  -H "Content-Type: application/json" \
  -H "X-RAG-User: $USER_ID" \
  -d '{"query": "Read my Shopping List", "model": "qwen3:latest"}'
echo ""

echo -e "\n=== 4. UPDATE CHECKBOX NOTE (Check Item B) ==="
curl -s -X POST "$BASE_URL" \
  -H "Content-Type: application/json" \
  -H "X-RAG-User: $USER_ID" \
  -d '{"query": "Update the Final Checkbox note. Change content to: - [ ] Item A\n- [x] Item B", "model": "qwen3:latest"}'
echo ""

sleep 2

echo -e "\n=== 5. READ CHECKBOX NOTE ==="
# Should have [x] Item B
curl -s -X POST "$BASE_URL" \
  -H "Content-Type: application/json" \
  -H "X-RAG-User: $USER_ID" \
  -d '{"query": "Read the Final Checkbox note", "model": "qwen3:latest"}'
echo ""

echo -e "\n=== 6. CLEANUP ==="
curl -s -X POST "$BASE_URL" \
  -H "Content-Type: application/json" \
  -H "X-RAG-User: $USER_ID" \
  -d '{"query": "Delete the Final Checkbox note", "model": "qwen3:latest"}'
echo ""
