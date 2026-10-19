# Artifact Framework: Implementation Plan

Phased, actionable plan for the Artifact Framework (extends the `storage` service).
Design spec: `ARTIFACTS.md`. Branch: `microservices`. CI must pass before deploy.

> **Status (2026-10-19):** the local provider, artifact/quota database, API,
> signed-range streaming, purge loop, gateway proxies, user Artifact library,
> admin Storage page and the workspace-deletion artifact prompt are implemented.
> TTS outputs (execute TTS, Nextcloud storage TTS and podcast renders) and
> agent-loop image generation now create artifacts too. There is no dedicated
> video or document generator left to wire; workspace/git files stay in the
> workspace by design.
> All implementation tasks are tracked in [docs/PENDING_TASKS.md](../../docs/PENDING_TASKS.md).
> They were consolidated there so this file stays a lightweight index.

## Open constants (configurable via env / settings)
- `ARTIFACT_LOCAL_QUOTA_BYTES` = 5 GiB (global default; per-user override from day one)
- `ARTIFACT_WARN_PCT` = 0.90
- `ARTIFACT_UNDO_WINDOW_HOURS` = 24
- signed-URL TTL = 15 min
- purge-loop interval = 5 min
