# Artifact Framework — Implementation Plan

Phased, actionable plan for the Artifact Framework (extends the `storage` service).
Design spec: `ARTIFACTS.md`. Branch: `microservices`. CI must pass before deploy.

> **All implementation tasks are tracked in [docs/PENDING_TASKS.md](../../docs/PENDING_TASKS.md).**
> They were consolidated there so this file stays a lightweight index.
> Verified 2026-07-11: the framework is **not yet implemented**
> (`services/storage/artifacts.py` does not exist; 0 artifact references in `storage/main.py`).

## Open constants (configurable via env / settings)
- `ARTIFACT_LOCAL_QUOTA_BYTES` = 5 GiB (global default; per-user override from day one)
- `ARTIFACT_WARN_PCT` = 0.90
- `ARTIFACT_UNDO_WINDOW_HOURS` = 24
- signed-URL TTL = 15 min
- purge-loop interval = 5 min
