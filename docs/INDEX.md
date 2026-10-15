# SharedLLM Documentation Index

## Start here

1. **AGENTS.md** — runtime and deploy rules for agents (read first)
2. **README.md** — architecture and the service/port table
3. **docs/CONFIG.md** — configuration model (`.env` bootstrap, GlobalSettings)
4. **docs/PENDING_TASKS.md** — the one backlog of open work
5. **TESTING.md** — what CI runs and how to run tests

## Services

- **docs/AUTOMATION_SERVICE.md** — timers and scheduled triggers
- **docs/CONTROL_PLANE_SERVICE.md** — container control, sandbox reaper, OSRM rebuilds
- **docs/GEO_SERVICE.md** — family location, trips, steps, workouts, stars; OSRM/Nominatim
- **docs/LOGGING_SERVICE.md** — central logs
- **docs/SQLITE_VEC_MIGRATION.md** — RAG vector store reference (cited by `services/rag/main.py`)
- **docs/CALIBRE_LIBRARY.md** — Calibre library from Nextcloud into RAG, with chapter citations
- **docs/PER_USER_CREDENTIALS.md** — per-user and shared service credentials

## Features

- **docs/FAMILY_HUB.md** — family chat (Nextcloud Talk), games, drawing, music, recipes, voice signatures, calls, phone notifications
- **docs/BIBLE_STUDY.md** — Bible reader, study help, plans, games, memorization, interlinear
- **docs/ACHIEVEMENTS.md** — goals, achievements, opt-in sharing, stars
- **docs/HEALTH_STEPS.md** — pedometer capture, device ledger, widgets
- **docs/NOTES.md** — Keep-style notes over Nextcloud markdown
- **docs/ARCADE_INTEGRATION.md** — Alpaca arcade games in the Family hub
- **docs/ENTITY_PROTECTION.md** — locking Home Assistant entities to admins and chosen users
- **docs/THEMES.md** — Jarvis-wide theme packs, import/export, widget tinting

## Media

- **docs/MEDIA_PLAYER.md** — media pipeline, device targeting, transport (canonical)
- **docs/MEDIA_UPSTREAM_API_NOTES.md** — Music Assistant / Audiobookshelf / HA contracts
- **docs/MEDIA_OVERHAUL.md** — remaining media redesign backlog

## Mobile

- **docs/MOBILE_OTA_UPDATES.md** — Android OTA bundle and APK update flow

## Networking

- **docs/DNS_SERVICE.md** — the DNS service (health-checked mappings, upstream cache)
- **docs/CADDY_CROSS_NETWORK_IMPLEMENTATION.md** — Caddy routing and the execution host-port bridge

## Raven

- **docs/RAVEN_LEARNING_MEMORY.md** — how Raven works, its tools and its learning memory
- **docs/adr_*.md** — architecture decision records (004-014, branch-push guardrails, hardening slice 01)

## Reference

- **docs/api_reference.md** — selected endpoints (the full list is the live OpenAPI at `/docs`)
- **docs/SCRIPTS.md** — deploy, test and diagnostic scripts

## Critical values (verify in code)

- `RAVEN_MAX_TOTAL_SECONDS`: 1800 s; `RAVEN_ITERATION_TIMEOUT`: 600 s (`services/config.py`)
- `raven_max_iterations`: 60 (`MAX_TOOL_ITERATIONS` in `services/gateway/agent_loop.py`)
- `INTERNAL_SECRET`, `FERNET_KEY`: required bootstrap secrets, `.env` only

## Quick commands

```bash
# Validate docs (writes docs/DOC_VALIDATION_REPORT.md, which is gitignored)
bash doc_validator.sh

# Deploy (images are built by CI; never on the server)
bash scripts/deploy_remote.sh jeremiah@192.168.2.205 /home/jeremiah/SharedLLM <service> [...]

# Test
python3 -m pytest -q                         # full Python suite
cd services/ui && npx vitest run             # UI unit tests
```
