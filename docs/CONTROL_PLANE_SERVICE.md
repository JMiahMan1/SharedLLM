# Control Plane Service

Docker orchestration and management service for the SharedLLM stack.

## Overview

The Control Plane (`sharedllm_control_plane`) is a FastAPI service on port **8008** that provides Docker API access for managing the SharedLLM container stack. It serves as the operational interface for container lifecycle management, health monitoring, image update detection, and remote command execution.

## Docker Integration

The service mounts the Docker socket (`/var/run/docker.sock`) and uses the `docker` Python SDK to interact with the Docker daemon.

- **Docker network:** `sharedllm` (standard compose network)
- **Container mode:** `privileged: true` (required for full Docker socket access)
- **Filtering:** Only containers with names prefixed `sharedllm_` or labeled `com.docker.compose.project=sharedllm` are exposed to API consumers

## Authentication

All management endpoints require the `X-Internal-Secret` header. The secret is resolved from shared config via `services.config.INTERNAL_SECRET`.

## API Endpoints

### Health & Info

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/health` | None | Service health — returns `status`, `service`, `git_sha`, `start_time` |
| `GET` | `/control_plane/health` | None | Alias for `/health` |
| `GET` | `/info` | None | Build info (service name, version, git SHA/branch, build date) |

### Container Management

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/api/containers` | Internal Secret | List all `sharedllm_` containers with status, uptime, health, image, exit code, restart count |
| `GET` | `/api/containers/{service_name}` | Internal Secret | Get detailed info for a specific service. Supports base name (`gateway`), full name (`sharedllm_gateway_1`), or partial match |
| `DELETE` | `/api/containers/{service_name}` | Internal Secret | Remove a **stopped** container. Returns 409 if running |

### Service Restart

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `POST` | `/api/restart/{service_name}?recreate=false` | Internal Secret | Restart a container. With `recreate=true` (or if a newer local image is detected), the container is recreated with preserved network config, volumes, and environment |

Recreate flow:
1. Stop old container
2. Rename to `{name}_backup_{timestamp}`
3. Extract full config (ports, volumes, networks, aliases, IPs, labels)
4. Create new container with same config but new image
5. Reconnect to all Docker networks with original aliases and IPs
6. Start new container
7. Remove backup

On failure: new container is torn down, backup is restored and started.

### Health Aggregation

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/api/health` | Internal Secret | Aggregated health of all services — returns totals (running/stopped/unhealthy), per-service details, and control plane uptime |
| `GET` | `/api/status/{service_name}` | Internal Secret | Status of a single service (same detail as `/api/containers/{service_name}`) |

### Update Detection

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/api/admin/services/updates` | Internal Secret | Check all services for available image updates without pulling. Compares local `RepoDigest` (sha256) against remote registry digest via OCI Distribution API |

Token resolution order:
1. Fetches `github_token` for user ID 1 from Identity service (`POST /api/resolve`)
2. Falls back to `GHCR_TOKEN` environment variable

Uses `Docker-Content-Digest` header from registry HEAD response.

### Image Pull

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `POST` | `/api/containers/{service_name}/pull` | Internal Secret | Pull latest image for a service. Returns current vs. new image ID and whether an update was applied |

### Logs

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/api/containers/{service_name}/logs?tail=100` | Internal Secret | Retrieve container logs. Detects and counts Python tracebacks in output |

### Remote Execution

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `POST` | `/api/containers/{service_name}/exec` | Internal Secret | Execute a shell command inside a running container |

Request body: `{ "command": "ping -c 1 sharedllm_gateway" }`

Returns: exit code and combined stdout/stderr output.

## Container Name Resolution

The `_resolve_container()` helper tries three strategies in order:

1. **Exact match** — direct `client.containers.get(service_name)`
2. **Compose label** — find container with `com.docker.compose.service` matching the name
3. **Name patterns** — try `sharedllm_{name}_1`, `sharedllm_{name}`, and `{name}` as fallbacks

## Configuration

Environment variables (from `docker-compose.yml`):

| Variable | Purpose |
|----------|---------|
| `INTERNAL_SECRET` | Auth token for all management endpoints |
| `FERNET_KEY` | Shared encryption key |
| `DOCKER_HOST` | Docker socket path (default: `unix:///var/run/docker.sock`) |
| `GHCR_TOKEN` | GitHub PAT with `packages:read` scope (for update checks) |
| `IDENTITY_SVC_URL` | Identity service URL (for GHCR token resolution) |
| `GIT_SHA`, `BUILD_DATE`, `SERVICE_NAME` | Build metadata (set via Docker build args) |

## Dependencies

- `docker` — Docker Python SDK
- `fastapi` + `uvicorn` — Web framework
- `services.config.INTERNAL_SECRET` — Shared secret resolution
- `services.shared.info_endpoint` — Standard `/info` router

## Deployment

- **Image:** `ghcr.io/jmiahman1/sharedllm-control_plane:latest`
- **Ports:** 8008 (exposed)
- **Restart policy:** `always`
- **User:** `PUID:PGID` (defaults 1000:1000)
- **Group:** `DOCKER_GID` (default 999) for Docker socket access
- **Volumes:** Docker socket, `/etc/localtime` (ro)
- **Dns:** `172.26.0.10` (dns-sync), `192.168.1.1` (upstream)
- **Depends on:** None explicitly (Docker socket is always available)

The Control Plane is depended on by the Gateway service via `CONTROL_PLANE_URL` environment variable.
