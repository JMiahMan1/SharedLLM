# SharedLLM Documentation Index

## Quick Start
1. **AGENTS.md** - Critical runtime rules for agents
2. **docs/DOC_GAP_ANALYSIS.md** - Known contradictions
3. **docs/CONFIG.md** - Configuration model
4. **docs/DEBUGGING_DECISION_TREES.md** - Troubleshooting flowcharts

## Documentation Hierarchy
- **AGENTS.md** - Agent instructions (read first)
- **docs/INDEX.md** - This navigation guide
- **docs/DOC_GAP_ANALYSIS.md** - Contradictions
- **docs/CONFIG.md** - Configuration
- **docs/DNS_SYNC_SERVICE.md** - DNS sync service
- **docs/CONTROL_PLANE_SERVICE.md** - Docker orchestration
- **docs/AUTOMATION_SERVICE.md** - Background tasks
- **docs/DNS_RESOLVER.md** - DNS resolver patch
- **docs/MEDIA_PLAYER.md** - Media pipeline (see MA_STREAMING_FIX.md)
- **docs/MA_STREAMING_FIX.md** - Current MA streaming
- **docs/LOGGING_SERVICE.md** - Observability
- **docs/DEBUGGING_DECISION_TREES.md** - Troubleshooting
- **docs/CI_VALIDATION.md** - CI/CD integration
- **docs/SCRIPTS.md** - Test scripts
- **docs/api_reference.md** - API endpoints
- **docs/adr_*.md** - Architecture decisions

## Critical Values (Verify in Code)
- RAVEN_MAX_TOTAL_SECONDS: 1800s
- RAVEN_HUNG_THRESHOLD: 600s
- RAVEN_HEARTBEAT_INTERVAL: 30s
- LOG_MAX_ENTRIES: 10000
- FERNET_KEY: Required bootstrap

## Quick Commands
```bash
# Validate docs
python3 scripts/validate_docs.py

# Deploy
bash scripts/deploy.sh

# Test
bash scripts/run_ci_unit_tests.sh
```
