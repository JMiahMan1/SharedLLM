# Raven Autonomous System - Testing Framework

This document outlines the testing strategy and execution protocols for the Raven microservice mesh.

## 1. Test Architecture

The system uses a tiered testing approach to ensure both CI reliability and local system fidelity:

### Tier 1: CI-Safe Unit Tests (Services)
- **Location**: `services/<service>/tests/test_main.py`
- **Scope**: Internal logic, schema validation, and mocked inter-service communication.
- **Execution**: `./scripts/run_ci_unit_tests.sh`
- **CI Protocol**: Runs on every push to `microservices` branch. Uses `respx` and `mocker` to bypass heavy dependencies.

### Tier 2: Frontend Verification (UI)
- **Location**: `services/ui/src/**/*.test.tsx`
- **Scope**: Component rendering, state management, and API client mocking.
- **Execution**: `cd services/ui && npm test`

### Tier 3: Local Integration Tests
- **Location**: `test/local/` and `test/integration/`
- **Scope**: Live service-to-service communication, real Home Assistant entities, and actual storage indexing.
- **Markers**: Marked with `@pytest.mark.local_only`.
- **Execution**: `pytest -m local_only`

### Tier 4: System Smoke Tests
- **Location**: `soa_smoke_test.py`
- **Scope**: End-to-end health checks and basic intent routing on a live deployment.
- **Execution**: `python soa_smoke_test.py` or via the **JarvisLab** dashboard.

## 2. JarvisLab Verification Dashboard

The **JarvisLab** dashboard (`/lab`) provides a real-time observability layer:
- **Mesh Health**: Visualizes readiness across all microservices.
- **Verification Engine**: Allows triggering smoke and unit test suites directly from the UI.
- **Live Logs**: Streams real-time websocket telemetry for debugging autonomous missions.

## 3. Maintenance Protocols

- **New Services**: Must include a `tests/test_main.py` file to be picked up by the CI runner.
- **Lifespan Migration**: All services must use the FastAPI `lifespan` pattern for Python 3.14+ compatibility.
- **Security**: Any new test or admin endpoints must be protected by the `X-Internal-Secret` header.
