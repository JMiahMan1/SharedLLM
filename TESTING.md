# Raven AI OS - Master Testing & Synchronization Protocol

This document outlines the multi-tier verification strategy for the Raven Autonomous System. Following the May 2026 hardening initiative, all core microservices are now verifiable via unified CI scripts and live dashboard telemetry.

## 1. Multi-Tier Testing Architecture

### Tier 1: CI-Safe Unit & Logic Tests (Logic Track)
These tests validate service logic, API contracts, and autonomous routing using fully mocked infrastructure. They are safe to run in GitHub Actions (GHA) or any headless environment.

- **Primary Command**: `./scripts/run_ci_unit_tests.sh`
- **Scope**: Identity, Execution, RAG, Storage, Gateway, and Workspace Runtime.
- **Mocking Strategy**: Uses `respx` and `MagicMock` to simulate Ollama LLMs, Redis, and Vector DBs.
- **Key Coverage**:
  - RBAC and Credential Resolution (Identity)
  - Tool Execution & JSON Parsing (Gateway)
  - Storage Proxy & Indexing Flow (Storage)
  - Sandbox Isolation & Security Policies (Workspace Runtime)

### Tier 2: System-on-a-Chip (SOA) Smoke Tests (Integration Track)
These tests verify the live connectivity and handshake protocols between active microservices. They require the service mesh to be running (either via `docker-compose` or local development servers).

- **Primary Script**: `python3 soa_smoke_test.py`
- **Execution (UI)**: Trigger via **JarvisLab Dashboard** -> **Verification Engine** -> **Run Smoke Test**.
- **Scope**: Validates end-to-end connectivity from Gateway to Storage/Execution.

### Tier 3: Hardware Verification (Live Track)
These tests interact with real physical devices (Roku, TVs, Home Assistant entities). They must be executed on the physical host where the hardware is reachable.

- **Target Server**: `192.168.2.205` (Local Jarvis Host)
- **Execution**: `./run_local_tests.sh test/live/`
- **Marker**: Tests are decorated with `@pytest.mark.live`.

---

## 2. Dashboard Observability (JarvisLab)

The **JarvisLab Verification Engine** (/lab) provides real-time visibility into system health:

- **Verification Engine**: Exposes buttons to run both "Unit Tests" and "Smoke Tests" directly from the browser.
- **Live Logs**: Streams real-time telemetry from all microservices, enabling rapid debugging of tool execution loops.
- **RAG Insights**: Displays vector database stats and synchronization health.

---

## 3. Security & Hardening Standards

- **Internal Secret Protection**: All inter-service calls must include the `X-Internal-Secret` header.
- **Lifespan Management**: All services have been migrated to the FastAPI `lifespan` pattern for clean startup/shutdown and to avoid Python 3.14+ deprecations.
- **Non-Streaming Parity**: The Gateway chat pipeline implements tool-parsing logic for both streaming and non-streaming requests to ensure functional parity in all client implementations.
- **Anti-Refusal Nudges**: Critical directives are injected into the system prompt for storage and diagnostic tasks to prevent model hesitation.

---

## 4. Maintenance & CI/CD

To maintain 100% coverage, all new features must:
1. Include a mocked unit test in the service's `tests/` directory.
2. Be added to the `scripts/run_ci_unit_tests.sh` execution list.
3. Pass the full suite before being merged into the master branch.
