#!/bin/bash
# run_tests_locally.sh

echo "Building Test Environment (Python 3.12)..."
docker build -f Dockerfile.test -t shared_llm_tests .

echo "Running Unit Tests..."
docker run --rm shared_llm_tests python3 test/test_notes_unit.py

# Optional: Run full pytest if configured
# docker run --rm shared_llm_tests pytest
