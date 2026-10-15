# Caddy — Routing and Cross-Network Ports

`Caddyfile` + the `caddy` service in `docker-compose.yml`. Caddy (bridge
network) is the single front door for the UI/API and the bridge between the
host-networked **execution** service and the bridge services.

## Why the cross-network ports exist

Execution runs with `network_mode: host`, so it cannot use Docker's service
names. Instead each bridge service is published on a host port **through
Caddy**, and execution reaches it at `127.0.0.1:<port>` (the `HOST_*_SVC_URL`
set in `.env`). Caddy forwards by Docker name. Names Caddy itself cannot
resolve (e.g. `.local` hosts) go through the DNS service — see
`docs/DNS_SERVICE.md`.

| Host port | Caddy site | Upstream |
|---|---|---|
| 8001 | `:8001` | `identity:8001` |
| 8004 | `:8004` | `rag:8004` |
| 8005 | `:8005` | `storage:8005` |
| 8006 | `:8006` | `logging:8006` |
| 8007 | `:8007` | `workspace_runtime:8007` |
| 8008 | `:8008` | `control_plane:8008` |
| 8009 | `:8009` | `geo:8009` |
| 8010 | `:8010` | `bible:8010` |
| 8095 | `:8095` | `ma:8095` (Music Assistant) |
| 11435 | `:11435` | `gateway:11435` (`/v1/*`, `/api/*`, `/execute*`, `/health*`) |

## The front door (`:80`, published as 8080; 443 as 8443)

Path routing (longer paths are declared before their prefixes):

| Path | Upstream |
|---|---|
| `/v1/*`, `/execute*`, `/health*`, and most `/api/*` (chat, media, communication, raven, telemetry, storage, integrations, …) | gateway |
| `/api/users/*/location`, `/api/users/location*` | gateway (adds the internal secret identity requires) |
| `/api/users*`, `/api/auth*`, `/api/settings*`, `/api/devices*` (except `favorites`/`pair`), `/api/resolve*`, `/api/widgets*` | identity |
| `/api/logs/stream*` | logging (WebSocket; needs `?token=`) |
| `/api/admin/logs*` | gateway (admin only) |
| `/api/admin/raven*`, `/api/admin/services*`, `/api/admin/users/*/stars`, `/api/admin/volumes*`, `/api/admin/dns*` | gateway |
| other `/api/admin*`, `/api/workspace*`, `/api/webhook*` | workspace_runtime |
| `/control_plane*` | control_plane |
| `/rag*` | rag |
| `/*` | ui |

The access log redacts the `token` and `mt` query parameters (the git-pull
webhook token and the WebSocket media token) and drops the `Authorization`
header.

Tests: `services/tests/test_config_network_mode_casing.py` covers the
`BRIDGE_*` / `HOST_*` URL sets this relies on.
