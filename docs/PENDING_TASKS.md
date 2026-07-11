# Pending Tasks

Single source of truth for documentation tasks that are **not yet complete**.

Verified against the codebase on 2026-07-11. Tasks that were already done have
been removed from their source docs; tasks whose direction was superseded by a
working alternative (e.g. the DNS relay, implemented via `dns-sync` +
`dns-forwarder`) are also removed.

## Artifact Framework (`services/storage`) — not implemented
Source: `services/storage/ARTIFACT_IMPLEMENTATION_PLAN.md` (design spec: `ARTIFACTS.md`)

- `services/storage/requirements.txt`: add `sqlmodel`, `python-multipart`.
- `services/storage/config.py` (currently an empty stub): expose `ARTIFACTS_ROOT`,
  `ARTIFACT_LOCAL_QUOTA_BYTES`, etc.
- `services/storage/database.py` (new): `create_engine("sqlite:///{ARTIFACTS_ROOT}/artifacts.db")`.
- `services/storage/models.py`: add Pydantic + SQLModel artifact models.
- `docker-compose.yml` storage service (~line 328): add volume `storage_artifacts:/artifacts`.
- `services/identity/models.py` `DEFAULT_GLOBAL_SETTINGS`: add artifact-related settings.
- `services/storage/main.py`: import db init; call in `lifespan`; start purge loop.
- `services/storage/providers_impl/local.py`: `LocalStorageProvider` implementation.
- `services/storage/providers.py` `build_provider`: add `if config.kind == "local": ...`.
- `services/storage/artifacts.py` (new): business logic.
- `services/storage/main.py`: artifact endpoints.
- Signed-URL helper: `make_artifact_token` / `verify_artifact_token` (HMAC w/ `INTERNAL_SECRET` or `FERNET_KEY`, ~15 min TTL).
- Range helper for streaming (parse `Range`, seek, `Content-Range`, `206`).
- `GET /storage/usage` (current user), `GET /storage/usage/{user}` (admin), plus gateway proxies under `/api/storage/usage*`, `/api/storage/quota/{user}`.
- `services/gateway/main.py` `DELETE /api/workspaces/{id}`: clean up artifacts before deleting.
- Execution helper `create_artifact(...)` (HTTP to storage `/artifacts`).
- Wire TTS first (`services/execution/main.py:1075`), then image (`services/gateway/tool_registry.py` alpaca) and video.
- Return `artifact_id` + serve URL alongside existing responses.
- `services/ui/src/services/api.ts`: add artifact + usage methods (see `ARTIFACTS.md §9`).
- `services/ui/src/pages/Storage.tsx` (user): progress bar, by-kind, recent, ≥90% banner.
- `services/ui/src/pages/admin/SystemStorage.tsx` (admin): per-user table + quota editor + disk stats.
- `services/ui/src/pages/Artifacts.tsx` (global library): virtualized list, range audio.
- Per-workspace Artifacts tab in the workspace view.
- `pytest` (`@pytest.mark.local_only`): local provider rw, `check-fit` low-free simulation.
- `Vitest`: storage-usage render, artifact library, fit/warning banner.
- `git push origin microservices` → confirm CI green (`gh run list --branch=microservices`).
- `./scripts/deploy_remote.sh jeremiah@192.168.2.205`.
- Live verification: upload TTS artifact → stream → fill to >90% → confirm warn → exceed.

## UI Stabilization (`services/ui`) — open items
Source: `docs/ui_stabilization_plan.md`

- Enhance E2E Playwright test coverage.
- Monitor GHA pipelines after push.
