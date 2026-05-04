# SharedLLM Testing Guide

This document outlines the testing strategies for the SharedLLM SOA architecture.

## 1. CI / Smoke Tests
These tests are designed for speed and run in CI environments. They use mocks for external services.
- **Location**: `services/tests/`
- **Command**: `pytest services/tests/`

## 2. Deep Local Functionality Tests
These tests are designed to be run **locally** against live hardware and services. They verify that commands actually result in state changes.
- **Location**: `test/local/`
- **Dependencies**: Requires a running SOA stack and valid `.env` credentials.

### How to Run:
```bash
./run_local_tests.sh
```

### Coverage:
1. **Hardware Verification**: Toggles lights and checks HA states to ensure physical control.
2. **Brightness Depth**: Verifies that percentage-based brightness commands result in the correct HA attribute values.
3. **Nextcloud Persistence**: Creates and reads notes directly from Nextcloud to ensure data is saved.
4. **RAG Sync Loop**: Verifies that the Gateway's entity fetching triggers the vector indexing pipeline.

> [!IMPORTANT]
> These tests interact with REAL hardware. Ensure the devices specified (e.g. `light.piano_lamp`) are safe to toggle before running.

## 3. Live Coding Evaluation

This scenario checks whether the coding path can return a correct fix for a
small broken repository fixture.

- **Fixture**: `test/fixtures/code_helper_bug_example/`
- **Runner**: `test/live/code_helper_fix_eval.py`
- **What it does**:
  1. sends the broken source file and tests to `/api/chat`
  2. asks for only the corrected file contents
  3. extracts the returned Python code block
  4. writes it into a temporary workspace
  5. runs `pytest` against the fixture tests

### How to Run
```bash
API_URL=http://localhost:11435 python3 test/live/code_helper_fix_eval.py
```

### Expected Outcome

- The fixture should fail before the model is asked to fix it.
- The script should report `[PASS]` only if the returned candidate actually
  passes the bundled tests.
