# Control Plane Service

Docker orchestration and management service for the SharedLLM stack.

## Overview

The Control Plane (`sharedllm_control_plane`) is a FastAPI service on port **8008** (reached through Caddy's `:8008` site and `/control_plane*`) that provides Docker API access for managing the SharedLLM container stack: container lifecycle, health, image update detection, remote command execution, and two background jobs (below).

## Background jobs

Two daemon threads start with the service:

- **Sandbox reaper**: every `SANDBOX_REAP_INTERVAL` seconds (default 3600)
  removes orphaned Raven sandbox containers whose workspace no longer exists
  (workspace ids come from workspace_runtime; if that lookup fails the sweep
  is skipped).
- **OSRM map updater**: every `OSRM_MAP_CHECK_INTERVAL` seconds (default
  3600) asks geo which regions are needed (`GET /map/regions`), extends the
  map build (`services/control_plane/osrm_map.py`, GitHub Actions
  `osrm-map.yml`) and redeploys `osrm` / `osrm-foot` when a newer build has
  finished.

## Docker Integration

The service mounts the Docker socket (`/var/run/docker.sock`) and uses the `docker` Python SDK to interact with the Docker daemon.

- **Docker network:** `sharedllm` (compose key; Docker network name `sharedllm_default`)
- **Container mode:** `privileged: true` (required for full Docker socket access)
- **Filtering:** Only containers with names prefixed `sharedllm_` or labeled `com.docker.compose.project=sharedllm` are exposed to API consumers

## Authentication

All management endpoints require the `X-Internal-Secret` header. The secret is resolved from shared config via `services.config.INTERNAL_SECRET`.

## API Endpoints

### Health & Info

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/health` | None | Service health: returns `status`, `service`, `git_sha`, `start_time` |
| `GET` | `/control_plane/health` | None | Alias for `/health` |
| `GET` | `/info` | None | Build info (service name, version, git SHA/branch, build date) |

### Container Management

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/api/containers` | Internal Secret | List all `sharedllm_` containers with status, uptime, health, image, exit code, restart count |
| `DELETE` | `/api/containers/{service_name}` | Internal Secret | Remove a **stopped** container. Returns 409 if running |

### Service Restart

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `POST` | `/api/restart/{service_name}?recreate=false` | Internal Secret | Restart a container. With `recreate=true`, or when a pull has left a newer local image for the container's tag, the container is recreated with its network config, volumes and environment. So *Pull* then *Restart* applies an update, as *Pull & Restart* does in one step |

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
| `GET` | `/api/health` | Internal Secret | Aggregated health of all services: returns totals (running/stopped/unhealthy), per-service details, and control plane uptime |
| `GET` | `/api/status/{service_name}` | Internal Secret | Detailed status of one service. Accepts a base name (`gateway`), full name (`sharedllm_gateway`), or partial match |

### Update Detection

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/api/admin/services/updates` | Internal Secret | For every `sharedllm_` container, whether its registry has a newer image. Never pulls |

How (`services/control_plane/registry.py`):
- The image checked is the one the container was created from
  (`Config.Image`, e.g. `ghcr.io/jmiahman1/sharedllm-ui:latest`), not
  `image.tags[0]`, which can be a commit tag that never moves. Pull, restart
  and pull-and-restart use the same reference.
- The registry's digest comes from a manifest `HEAD` using the standard
  Bearer-token exchange. The images are public, so no token is needed; for a
  private image the GitHub token is used (user ID 1's `github_token` from
  Identity, else `GHCR_TOKEN`, else `GITHUB_TOKEN`), falling back to an
  anonymous pull token if it is rejected.
- `has_update` is true when that digest is not among the image's local
  `RepoDigests`. Per-service `check_error` is `no_image_tag`,
  `digest_unavailable` or `registry_unreachable` (after the first network
  failure the rest are skipped so the call stays fast).

### Image Pull

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `POST` | `/api/containers/{service_name}/pull` | Internal Secret | Start a **non-blocking** pull of the latest image for a service. Returns immediately with `status: "pulling"`. Poll `GET /api/containers/{service_name}/pull/status` for progress. |
| `GET` | `/api/containers/{service_name}/pull/status` | Internal Secret | Get the status of a background image pull (`idle` / `pulling` / `completed` / `failed`). |
| `POST` | `/api/containers/{service_name}/pull-and-restart` | Internal Secret | Pull the latest image and immediately restart the container with it (phone-update flow). Fixes volume permissions before recreating. |

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

1. **Exact match**: direct `client.containers.get(service_name)`
2. **Compose label**: find container with `com.docker.compose.service` matching the name
3. **Name patterns**: try `sharedllm_{name}_1`, `sharedllm_{name}`, and `{name}` as fallbacks

## Configuration

Environment variables (from `docker-compose.yml`):

| Variable | Purpose |
|----------|---------|
| `INTERNAL_SECRET` | Auth token for all management endpoints |
| `FERNET_KEY` | Shared encryption key |
| `DOCKER_HOST` | Docker socket path (default: `unix:///var/run/docker.sock`) |
| `GHCR_TOKEN` | Optional GitHub PAT with `packages:read`, only needed if the images are private |
| `GITHUB_TOKEN` | Fallback for `GHCR_TOKEN` |
| `GHCR_NAMESPACE` | Owner used to infer an image for a container with no tag (default `jmiahman1`) |
| `IDENTITY_SVC_URL` | Identity service URL (`${BRIDGE_IDENTITY_SVC_URL}`; GHCR token resolution) |
| `WORKSPACE_RUNTIME_SVC_URL` | Workspace list for the sandbox reaper |
| `GEO_SVC_URL` | Geo service, asked which OSRM regions are needed (not set in compose; `services/config.py` defaults to `http://geo:8009`) |
| `SANDBOX_REAP_INTERVAL`, `OSRM_MAP_CHECK_INTERVAL` | Background job intervals (seconds) |
| `GIT_SHA`, `BUILD_DATE`, `SERVICE_NAME` | Build metadata (set via Docker build args) |

## Dependencies

- `docker`: Docker Python SDK
- `fastapi` + `uvicorn`: web framework
- `services.config.INTERNAL_SECRET`: shared secret resolution
- `services.shared.info_endpoint`: standard `/info` router

## Deployment

- **Image:** `ghcr.io/jmiahman1/sharedllm-control_plane:latest`
- **Ports:** 8008 inside the bridge; Caddy publishes host port 8008 to it
- **Restart policy:** `always`
- **User:** `PUID:PGID` (defaults 1000:1000)
- **Group:** `DOCKER_GID` (default 999) for Docker socket access
- **Volumes:** Docker socket, `/etc/localtime` (ro)
- **DNS:** `172.26.0.254` (the DNS service, see `docs/DNS_SERVICE.md`)
- **Depends on:** None explicitly (Docker socket is always available)

The Control Plane is depended on by the Gateway service via `CONTROL_PLANE_URL` environment variable.
