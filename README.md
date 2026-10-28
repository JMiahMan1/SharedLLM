# Jarvis (SharedLLM) - Modular SOA AI Middleware

Jarvis is a Service-Oriented Architecture that unifies smart home control,
personal cloud services, family features and semantic memory behind one
conversational AI and one app (web + Android).

## Architecture Overview

Requests are handled in three tiers, balancing speed and intelligence:

1. **Tier 1: FastPath (semantic matcher)**: keyword and embedding match
   (`nomic-embed-text-v1.5`) for common commands, bypassing the LLM entirely.
   Use case: "turn off the lights". Under ~100 ms.
2. **Tier 2: Librarian**: single-turn LLM with tool access (up to 3 tool
   turns) for ambiguous home commands, document retrieval and general
   questions. Seconds.
3. **Tier 3: Raven**: the autonomous agent for coding and system repair. Runs
   a multi-step agent loop (up to 60 iterations, `raven_max_iterations`) with
   tool feedback, in workspaces managed by the Workspace Runtime. See
   `docs/RAVEN_LEARNING_MEMORY.md`.

### Services

Every service is a container in `docker-compose.yml`. All run on the
`sharedllm_default` bridge network except **execution**, which uses host
networking for device discovery (Google Cast, DLNA, SSDP) and reaches the
bridge services through Caddy (`docs/CADDY_CROSS_NETWORK_IMPLEMENTATION.md`).

| Service | Port | Description |
|---|---|---|
| **caddy** | 8080 / 8443 (+ cross-network ports) | Front door for the UI and API; host-port bridge for execution |
| **gateway** | 11435 | Orchestrator, intent classification, API for the UI and OpenAI-compatible clients |
| **identity** | 8001 | Users, API keys, encrypted credentials, devices, settings (GlobalSetting) |
| **execution** | 8003 (host network) | Home Assistant, media, Talk, TTS/STT, voice signatures, tools |
| **rag** | 8004 | Semantic memory on SQLite + sqlite-vec (`docs/SQLITE_VEC_MIGRATION.md`) |
| **storage** | 8005 | Nextcloud file access and indexing |
| **logging** | 8006 | Centralized logs (admin-only reads) |
| **workspace_runtime** | 8007 | Raven workspaces, sandboxes, git webhooks |
| **control_plane** | 8008 | Container status/restarts, sandbox reaper, OSRM map rebuilds |
| **geo** | 8009 | Family location, trips, steps, workouts, stars (`docs/GEO_SERVICE.md`) |
| **bible** | 8010 | Bible reader, study, plans, games (`docs/BIBLE_STUDY.md`) |
| **automation** | 11437 | Timers and scheduled triggers (`docs/AUTOMATION_SERVICE.md`) |
| **telemetry** | 11438 | Device telemetry, reports, notification outbox |
| **ui** | via caddy | React app (also the Android app via Capacitor) |
| **dns** | 15353 (host), 172.26.0.254 (bridge) | Name server for all containers (`docs/DNS_SERVICE.md`) |
| **osrm**, **osrm-foot**, **nominatim** | internal | Road/foot routing and self-hosted geocoding for geo |
| **capabilities-sync** | none | One-shot: indexes system capabilities into RAG |
| **redis** | 6379 | Job queue, sessions, history, caches |

Configuration: `docs/CONFIG.md`. Secrets (`INTERNAL_SECRET`, `FERNET_KEY`)
live only in the server's `.env`; everything else is a GlobalSetting in
Identity, editable in Admin > LLM & Settings.

## Deployment

Images are built by GitHub Actions on push to `microservices`; nothing is
built on the server.

```bash
bash scripts/deploy_remote.sh <user>@<server> /home/<user>/SharedLLM <service> [<service>...]
```

See `CONTRIBUTING.md` for the project rules (including deploys) and `docs/SCRIPTS.md` for the scripts.

## Key Components

- **Raven**: the coding and long-running task agent, with a RAG-backed
  learning memory.
- **Fast path routing**: semantic intent classification skips the LLM for
  strong matches.
- **Family hub**: Nextcloud Talk chat, games, drawing, music, recipes, voice
  signatures (`docs/FAMILY_HUB.md`).
- **Capability enforcement**: per-workspace policies (read, write, git, pytest)
  that confine Raven to what it is allowed to do in each workspace.

Docs index: `docs/INDEX.md`. Backlog: `docs/PENDING_TASKS.md`.
