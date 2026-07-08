# SharedLLM Context & Repository Rules

## 1. CRITICAL INFRASTRUCTURE RULES
Violating these rules will crash the development environment.

* **NO LOCAL DOCKER:** **NEVER** run Docker commands locally. All Docker operations (`build`, `run`, `compose up/stop`) must be executed on the remote server: `192.168.2.205`. 
    * *Usage:* `ssh jeremiah@192.168.2.205 "docker compose ..."`
* **NO HARDCODED IPs:** Do not hardcode IPs (e.g., `192.168.1.1`, `192.168.2.205`). Use network discovery (DNS sync service via Docker API) so services connect via hostnames or discovered gateways.
* **BRANCH DISCIPLINE:** All work goes to the `microservices` branch. Never push to `main` unless explicitly requested.

## 2. CI/CD & Deployment Workflow
1.  **Push:** `git push origin microservices` triggers the GitHub Actions CI (`.github/workflows/build-images.yml`).
2.  **Verify Build:** Execute `gh run list --branch=microservices --limit=5`. **DO NOT** proceed until the status is exactly `"completed"`.
3.  **Deploy:** Execute `./scripts/deploy_remote.sh jeremiah@192.168.2.205`. This pulls the latest GHCR `latest` tags and runs `docker compose up -d`.

## 3. Architecture & Key Services
* **Identity Service:** Port 8001 (Resolves user credentials, manages `mass_token_enc` fallback to admin/ID 1).
* **Gateway:** Port 11435 (Proxies requests to MA/ABS, handles sendspin protocol).
* **UI:** Port 8080 (Web player interface).
* **MA Server:** `https://ha.sumemail.com:8095`
* **Caddy:** Reverse proxy in front of services.

## 4. Testing Protocols
* **Pytest Markers:** Strictly adhere to test markers. 
    * Use `@pytest.mark.local_only` for tests requiring hardware/heavy setup.
    * Use `@pytest.mark.integration` for inter-service communication tests.
    * Integration tests require services to be running to avoid "Connection refused" errors.
* **Playwright E2E Tests:** Located in `services/ui/e2e/`. 
    * Use `loginAsDefault(page)` for testing.
    * *Critical:* Always use `page.on('websocket')` listeners instead of `waitForEvent('websocket')`, as events often fire before listeners attach.

## 5. Server Diagnostics & Access
* **Sudo Usage:** You have passwordless sudo access strictly for predefined, read-only diagnostic commands.
* **Approved Commands:** * Network: `sudo lsof -i -n -P`, `sudo netstat -ulnp`, `sudo netstat -tlnp`, `sudo tcpdump -i any`.
    * Docker Diagnostics: `sudo docker ps`, `logs`, `inspect`, `top`, `network ls`, `network inspect`.
* **Prohibited Contexts:** Do not use sudo for interactive shells, `docker run`, or `docker compose`.
