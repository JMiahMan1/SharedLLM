# Jarvis AI OS - Master Testing Protocol (May 2026 Standard)

This document outlines the dual-track testing strategy required for the Jarvis AI OS microservice mesh.

## 1. Track A: Automated Unit & Integration Tests (Mocked)
These tests run in CI/CD environments (GitHub Actions) and do not require physical hardware.

- **Tools:** `pytest`, `httpx`, `respx`, `msw`.
- **Location:** `test/` (Python), `services/ui/src/test/` (React).
- **Execution:**
  ```bash
  pytest test/
  ```

## 2. Track B: Live Hardware Verification (Physical Host)
These tests interact with real physical devices (Roku, TVs, Home Assistant entities) and **MUST** be executed directly on the Jarvis host server.

- **Target Server:** `192.168.2.205`
- **SSH Access Protocol:**
  ```bash
  ssh jeremiah@192.168.2.205
  ```
- **Execution:**
  Once logged in, navigate to the project root and run:
  ```bash
  ./run_local_tests.sh test/live/
  ```

> [!IMPORTANT]
> Any test marked with `@pytest.mark.live` will be skipped in CI/CD and requires the physical environment of `192.168.2.205` to pass.

## 3. Security Hardening Verification
- **Path Traversal:** Use `test/test_workspace_security.py` to verify that 403 Forbidden is raised for sandbox escapes.
- **RBAC:** Verify UI routing constraints by logging in as a non-admin user and attempting to access `/admin`.
- **Timeout Cascades:** Use `test/test_gateway_timeouts.py` to ensure the system remains resilient when Home Assistant is slow.
