# SharedLLM Documentation Index

## Quick Start
1. **AGENTS.md** — critical runtime rules for agents (read first)
2. **docs/CONFIG.md** — configuration model
3. **docs/api_reference.md** — API endpoints
4. **docs/DOC_GAP_ANALYSIS.md** — known contradictions between docs and code

## Services
- **docs/AUTOMATION_SERVICE.md** — background tasks
- **docs/CONTROL_PLANE_SERVICE.md** — Docker orchestration
- **docs/GEO_SERVICE.md** — family location (wraps Home Assistant)
- **docs/LOGGING_SERVICE.md** — observability
- **docs/SQLITE_VEC_MIGRATION.md** — RAG vector store (referenced by `services/rag/main.py`)

## Mobile
- **docs/MOBILE_OTA_UPDATES.md** — Android OTA bundle + APK update flow

## Media
- **docs/MEDIA_PLAYER.md** — media pipeline, device targeting and transport commands
- **docs/MA_STREAMING_FIX.md** — Music Assistant browser streaming path

## Networking
- **docs/DNS_SYNC_SERVICE.md** — DNS sync service
- **docs/DNS_RESOLVER.md** — DNS resolver patch
- **docs/DNS_RELAY_ARCHITECTURE.md** — DNS relay design
- **docs/CADDY_CROSS_NETWORK_IMPLEMENTATION.md** — cross-network routing

## Raven
- **docs/RAVEN_AUDIT_BLUEPRINT.md** — audit design
- **docs/RAVEN_CAPABILITY_GAP_ANALYSIS.md** — capability gaps
- **docs/RAVEN_LEARNING_MEMORY.md**, **docs/raven_learning.md** — learning/memory

## Reference
- **docs/SCRIPTS.md** — test and deploy scripts
- **docs/PENDING_TASKS.md** — open documentation tasks
- **docs/adr_*.md** — architecture decision records

## Critical Values (verify in code)
- RAVEN_MAX_TOTAL_SECONDS: 1800s
- RAVEN_HUNG_THRESHOLD: 600s
- RAVEN_HEARTBEAT_INTERVAL: 30s
- LOG_MAX_ENTRIES: 10000
- FERNET_KEY: required bootstrap

## Quick Commands
```bash
# Validate docs (writes docs/DOC_VALIDATION_REPORT.md, which is gitignored)
bash doc_validator.sh

# Deploy
bash scripts/deploy.sh              # standard deploy
bash scripts/deploy_local_build.sh  # build images on the server, no GHCR pull

# Test
bash scripts/run_ci_unit_tests.sh   # CI unit tests
python3 -m pytest -q                # full Python suite
cd services/ui && npm test          # UI unit tests
```
