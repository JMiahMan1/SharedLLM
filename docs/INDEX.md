# SharedLLM Documentation Index

## Start here

1. `CONTRIBUTING.md`: project rules (temp files, mobile parity, fail fast, build and deploy)
2. `README.md`: architecture and the service/port table
3. `docs/CONFIG.md`: configuration model (`.env` bootstrap, GlobalSettings)
4. `docs/PENDING_TASKS.md`: the one backlog of open work
5. `TESTING.md`: what CI runs and how to run tests

## Services

- `docs/AUTOMATION_SERVICE.md`: timers and scheduled triggers
- `docs/CONTROL_PLANE_SERVICE.md`: container control, sandbox reaper, OSRM rebuilds
- `docs/GEO_SERVICE.md`: family location, trips, steps, workouts, stars; OSRM/Nominatim
- `docs/LOGGING_SERVICE.md`: central logs
- `docs/SQLITE_VEC_MIGRATION.md`: RAG vector store reference (cited by `services/rag/main.py`)
- `docs/CALIBRE_LIBRARY.md`: Calibre library from Nextcloud into RAG, with chapter citations
- `docs/PER_USER_CREDENTIALS.md`: per-user and shared service credentials

## Features

- `docs/DASHBOARD.md`: app shell (navigation, bell, readiness), dashboard widgets, settings, sign-in, docs page
- `docs/INTEGRATIONS.md`: Accounts & Integrations page: each service's fields, API keys
- `docs/FAMILY_HUB.md`: family chat (Nextcloud Talk), games, drawing, music, recipes, voice signatures, calls, phone notifications
- `docs/PUSH.md`: phone notifications over self-hosted ntfy: publishing notices, the phone stream, categories and preferences, action buttons, UnifiedPush for other apps
- `docs/BIBLE_STUDY.md`: Bible reader, study help, plans, games, memorization, interlinear
- `docs/ACHIEVEMENTS.md`: goals, achievements, opt-in sharing, stars
- `docs/HEALTH_STEPS.md`: pedometer capture, device ledger, widgets
- `docs/NOTES.md`: Keep-style notes over Nextcloud markdown (and the Recipes cookbook)
- `docs/CALENDAR.md`: merged Nextcloud/Skylight/iCal agenda, windows, repeats, edit by UID
- `docs/ARCADE_INTEGRATION.md`: Alpaca arcade games in the Family hub
- `docs/ENTITY_PROTECTION.md`: locking Home Assistant entities to admins and chosen users
- `docs/THEMES.md`: Jarvis-wide theme packs, import/export, widget tinting

## Media

- `docs/MEDIA_PLAYER.md`: media pipeline, device targeting, transport (canonical)
- `docs/MEDIA_UPSTREAM_API_NOTES.md`: Music Assistant / Audiobookshelf / HA contracts
- `docs/MEDIA_OVERHAUL.md`: remaining media redesign backlog

## Mobile

- `docs/MOBILE_OTA_UPDATES.md`: Android OTA bundle and APK update flow

## Networking

- `docs/DNS_SERVICE.md`: the DNS service (health-checked mappings, upstream cache)
- `docs/CADDY_CROSS_NETWORK_IMPLEMENTATION.md`: Caddy routing and the execution host-port bridge

## Raven

- `docs/RAVEN_LEARNING_MEMORY.md`: how Raven works, its tools and its learning memory

Architecture decision records:

- `docs/adr_004_structured_tracing_sanitizer.md`: structured request tracing and central secret sanitization in logs (proposed, not implemented)
- `docs/adr_005_job_persistence.md`: Raven job checkpoints in Redis so jobs can resume (implemented differently)
- `docs/adr_006_circuit_breaker.md`: circuit breakers around inter-service HTTP calls (proposed, not implemented)
- `docs/adr_007_hard_timeout.md`: hard wall-clock limit per Raven job (partly implemented)
- `docs/adr_008_streaming_first.md`: streaming inference by default in the agent loop
- `docs/adr_009_quarantine_guardrails.md`: quarantining workspaces whose files keep failing verification (implemented differently)
- `docs/adr_010_tiered_inference_locks.md`: tiered inference locks so Raven work does not block quick queries
- `docs/adr_011_hardened_json_extraction.md`: hardened JSON extraction from LLM responses
- `docs/adr_012_raven_routing_guards.md`: keeping Raven requests off the fast path
- `docs/adr_013_agentloop_termination_safeguards.md`: success criteria and iteration guards for the agent loop
- `docs/adr_014_multidialect_tool_execution_and_resilience.md`: parsing several tool-call dialects and surviving handler failures
- `docs/adr_raven_branch_push_guardrails.md`: Raven pushes only to unprotected branches, enforced in `workspace_runtime`
- `docs/adr_raven_hardening_slice_01.md`: first set of in-place Raven hardening controls

## Reference

- `docs/BACKGROUND_JOBS.md`: every periodic/background job: owner service, interval, what it does
- `docs/api_reference.md`: selected endpoints (the full list is the live OpenAPI at `/docs`)
- `docs/SCRIPTS.md`: deploy, test and diagnostic scripts
- `tools/README.md`: command-line tools the execution service runs

## Critical values (verify in code)

- `RAVEN_MAX_TOTAL_SECONDS`: 1800 s; `RAVEN_ITERATION_TIMEOUT`: 600 s, defined but not yet enforced (`services/config.py`)
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
