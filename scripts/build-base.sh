#!/bin/bash
set -e
cd "$(dirname "$0")/.."

echo "=== Building sharedllm-base image (pre-installs all common Python packages + ML deps) ==="
echo "This image is shared across all Python services — first build takes ~15min, subsequent builds are instant."

docker build -f docker/Dockerfile.base -t sharedllm-base:latest .

echo ""
echo "=== Base image built: sharedllm-base:latest ==="
echo "To build all services: docker compose -f docker-compose.yml up -d --build"
echo "The base layers will be reused — only per-service diffs are built."
