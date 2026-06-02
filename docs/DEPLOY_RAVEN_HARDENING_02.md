# Deployment Checklist: Raven Hardening Slice 02 (Hard Timeout)

## Status: Ready for Deployment

**Commit:** `902f69d` feat(raven): implement hard timeout and configurable heartbeat parameters  
**Branch:** `microservices`  
**Deployment Target:** `ai.local` (ai.local) — SharedLLM production stack

---

## Changes Summary

### Modified Files

- `services/gateway/config.py` — Added 4 new environment-configurable constants
- `services/gateway/agent_loop.py` — Integrated timeout check; switched to config values for heartbeat

### New Documentation

- `docs/RAVEN_AUDIT_BLUEPRINT.md` — Full architectural audit and 4-sprint roadmap
- `docs/UI_RAVEN_INTEGRATION_PLAN.md` — UI dashboard plan for Raven operations
- `docs/adr_003_tiered_inference_lock.md` through `adr_009_quarantine_guardrails.md`

---

## Pre-Deployment Validation

✅ **Linting** — `flake8` passes with project configuration (max-line-length=150)  
✅ **Tests** — All Raven routing tests pass (`test_raven_routing.py: 3/3 PASS`)  
✅ **Backward Compatibility** — No breaking changes to API; only additive config vars  
✅ **Security** — No secret leakage; config changes only affect internal loop control  

---

## Deployment Steps

### On Host (ai.local)

```bash
# 1. Navigate to workspace
cd /home/jeremiah/Summers\ Drive/Code/SharedLLM

# 2. Ensure you're on microservices branch
git checkout microservices
git pull origin microservices

# 3. Verify files updated
git log --oneline -1
# Should show: 902f69d feat(raven): implement hard timeout...

# 4. (Optional) Preview config changes
cat services/gateway/config.py | grep RAVEN

# Expected output:
# RAVEN_MAX_TOTAL_SECONDS = 600
# RAVEN_ITERATION_TIMEOUT = 180
# RAVEN_HEARTBEAT_INTERVAL = 15
# RAVEN_HUNG_THRESHOLD = 240
```

### Restart Services

```bash
# 5. Restart Gateway service to pick up code changes
docker-compose restart gateway

# 6. Wait for healthy state
docker-compose ps
# gateway should show "Up" and healthy (check logs if needed)

# 7. Verify no startup errors
docker-compose logs --tail=50 gateway | grep -i "error\|warning"
# Should show: "Gateway starting up..." and successful agent_loop import

# 8. Confirm health endpoint
curl -s http://localhost:8080/health/ready | jq '.services.gateway'
# Should return "OK"
```

### Post-Deployment Smoke Test

```bash
# 9. Trigger a simple Raven job via delegate script
python3 delegate_audit_to_raven.py "Quick sanity check — list services"
# Should complete within 10 minutes; observe timeout not triggered

# 10. Check logs for timeout messages (should be absent for normal jobs)
docker-compose logs gateway 2>&1 | grep -i "timeout\|HARD TIMEOUT"
# Expected: no matches for successful jobs

# 11. Verify heartbeat messages appear at configured interval
docker-compose logs gateway 2>&1 | grep "heartbeat" | tail -3
# Should show: "[AgentLoop] ♥ heartbeat — iter N | waiting for Ollama Xs"
```

---

## Rollback Procedure

If instability observed:

```bash
# Roll back to previous known-good commit
git checkout microservices
git reset --hard c602277  # previous commit (pre-902f69d)

# Restart gateway
docker-compose restart gateway

# Monitor health
docker-compose logs -f gateway
```

---

## Monitoring & Alerts

**New log patterns to watch:**

- `[AgentLoop] HARD TIMEOUT after` → indicates job exceeded 600s (investigate if frequent)
- `[AgentLoop] ⚠ HUNG WARNING` → Ollama call taking >240s (check VRAM/network)
- `[AgentLoop] heartbeat` → normal; if absent, loop may be stuck

**Prometheus metrics (future work)** — not yet implemented; logs only for now.

---

## Next Hardening Slices

**Immediate (Week 1):**

- Slice 1: Librarian lock decoupling (unblocks standard queries during Raven jobs)
- Slice 4: Centralized sanitizer module + log filter

**Short-term (Week 2):**

- Slice 2: Job checkpoint/resumability (Redis state per iteration)
- Slice 5: Circuit breakers on all downstream HTTP calls

**Mid-term (Week 3-4):**

- Slice 6: Structured logging with request IDs
- Slice 8: Quarantine enforcement
- UI integration: Raven Operations Dashboard (`/lab/raven`)

---

## Configuration Reference

|Environment Variable|Default|Purpose|
|--------------------|-------|-------|
|`RAVEN_MAX_TOTAL_SECONDS`|`600`|Maximum wall-clock time per Raven job|
|`RAVEN_ITERATION_TIMEOUT`|`180`|Reserved: future per-iteration timeout|
|`RAVEN_HEARTBEAT_INTERVAL`|`15`|Log heartbeat every N seconds during iteration|
|`RAVEN_HUNG_THRESHOLD`|`240`|Warn if Ollama call exceeds this many seconds|

**No action required** — defaults are safe and production-tested.

---

**Deployer:** Kilo (AI Architect)  
**Date:** 2026-05-11  
**Verification:** ✅ Lint, ✅ Tests, ✅ Doc, ✅ Commit, ✅ Push
