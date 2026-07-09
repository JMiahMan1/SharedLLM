# Artifact Framework — Implementation Plan

Phased, actionable plan for the Artifact Framework (extends the `storage` service).
Design spec: `ARTIFACTS.md`. Branch: `microservices`. CI must pass before deploy.

---

## Phase 0 — Scaffolding & config

**Goal:** storage service can own artifact data + local bytes.

- [ ] `services/storage/requirements.txt`: add `sqlmodel`, `python-multipart`.
- [ ] `services/storage/config.py` (currently empty stub): expose
      `ARTIFACTS_ROOT = os.getenv("ARTIFACTS_ROOT", "/artifacts")`,
      `ARTIFACT_LOCAL_QUOTA_BYTES = int(os.getenv("ARTIFACT_LOCAL_QUOTA_BYTES", "5368709120"))`,
      `ARTIFACT_WARN_PCT = float(os.getenv("ARTIFACT_WARN_PCT", "0.90"))`,
      `ARTIFACT_UNDO_WINDOW_HOURS = float(os.getenv("ARTIFACT_UNDO_WINDOW_HOURS", "24"))`.
- [ ] `services/storage/database.py` (new): `create_engine("sqlite:///{ARTIFACTS_ROOT}/artifacts.db")`,
      `init_artifacts_db()` creating `Artifact` + `UserStorageQuota` tables (idempotent).
- [ ] `services/storage/models.py`: add Pydantic + SQLModel models:
      - `ArtifactKind` (str enum), `ArtifactBase`/`ArtifactCreate`/`ArtifactRead`,
        `FitResult`, `CheckFitRequest`, `UserStorageQuotaRead`/`Write`,
        `StorageUsage`, `SystemStorageUsage`.
- [ ] `docker-compose.yml` storage service (~line 328): add volume `storage_artifacts:/artifacts`,
      env `ARTIFACTS_ROOT=/artifacts`, `ARTIFACT_LOCAL_QUOTA_BYTES=5368709120`.
- [ ] `services/identity/models.py` `DEFAULT_GLOBAL_SETTINGS`: add
      `{"key": "artifact_local_quota_bytes", "value": "5368709120", "description": "..."}`.
- [ ] `services/storage/main.py`: import db init; call in `lifespan`; start purge loop
      (`asyncio.create_task(_purge_deleted_artifacts())`).

**Acceptance:** storage service boots with `artifacts.db` created; `/health` ok.

---

## Phase 1 — Local backend + fit-check (MVP)

**Goal:** store artifact bytes locally and block when space/quota is exceeded.

- [ ] `services/storage/providers_impl/local.py`: `LocalStorageProvider` implementing
      `StorageProvider` (`list_entries`, `get_content`, `write_content`, `delete_content`)
      rooted at `ARTIFACTS_ROOT/{owner}/{kind}/{artifact_id}`.
- [ ] `services/storage/providers.py` `build_provider`: add `if config.kind == "local": ...`.
- [ ] `services/storage/artifacts.py` (new): business logic
      - `user_quota(owner) -> int` (per-user `UserStorageQuota` else global default).
      - `used_bytes(owner) -> int` (Σ `Artifact.size` where not deleted).
      - `check_fit(owner, needed) -> FitResult` (free space + quota + 90% warn).
      - `create_artifact(...)`: fit-check → `provider.write_content` → insert row (sha256 dedup).
      - `get_artifact`, `list_artifacts` (paginated/filtered), `stream_artifact` (range),
        `soft_delete`, `hard_delete(phrase)`, `restore`, `purge_expired`.
- [ ] `services/storage/main.py`: endpoints
      `POST /artifacts`, `GET /artifacts`, `GET /artifacts/{id}`,
      `GET /artifacts/{id}/stream` (range `206`), `GET /artifacts/{id}/download` (HMAC signed URL),
      `PATCH /artifacts/{id}`, `DELETE /artifacts/{id}` (+ `?hard=true` + phrase body),
      `POST /artifacts/{id}/restore`, `POST /artifacts/check-fit`.
- [ ] Signed-URL helper: `make_artifact_token(artifact_id)` / `verify_artifact_token` (HMAC w/ `INTERNAL_SECRET` or `FERNET_KEY`, ~15 min TTL).
- [ ] Range helper for streaming (parse `Range`, seek, `Content-Range`, `206`).

**Acceptance:** upload a small file → list → stream (seekable) → soft-delete → restore;
`check-fit` with a `needed` larger than free/quota returns `fits=False` + message;
90%-full owner gets `warn=True`.

---

## Phase 2 — Quota, usage & admin

- [ ] `GET /storage/usage` (current user), `GET /storage/usage/{user}` (admin),
      `GET /storage/usage/system` (admin: per-user + `shutil.disk_usage(ARTIFACTS_ROOT)`),
      `PUT /storage/quota/{user}` (admin sets `UserStorageQuota`).
- [ ] Gateway proxies under `/api/storage/usage*`, `/api/storage/quota/{user}`,
      `/api/artifacts/*` (mirror existing `/api/storage/*` proxy style in
      `services/gateway/main.py:~3830`).

**Acceptance:** a user sees their used/quota/by_kind; admin sees system totals + can set
a per-user quota that `check_fit` then honors.

---

## Phase 3 — Workspace-delete confirmation & orphaning

- [ ] `services/gateway/main.py` `DELETE /api/workspaces/{id}`: before calling
      `workspace_runtime`, query storage for `workspace_id == id`; if artifacts exist and
      `delete_artifacts` not in body → `409 {artifact_count, needs_confirmation:true}`.
      On confirm, delete artifact rows+bytes; else keep with `is_attached=false`.

**Acceptance:** deleting a workspace with artifacts returns 409; confirming with
`delete_artifacts=true` removes them; without it, artifacts remain reachable + flagged
"unattached".

---

## Phase 4 — Generator integration

- [ ] Execution helper `create_artifact(...)` (HTTP to storage `/artifacts` with
      `X-Internal-Secret`).
- [ ] Wire TTS first: `services/execution/main.py:1075`,
      `services/execution/handlers/storage.py:61`, `services/execution/handlers/talk.py:245`
      → `kind=tts_audio`; call `check-fit` before generating.
- [ ] Wire image (`services/gateway/tool_registry.py` alpaca), video
      (`services/execution/handlers/video.py`), doc exports → respective kinds.
- [ ] Return `artifact_id` + serve URL alongside existing responses.

**Acceptance:** a TTS request produces a stored, streamable artifact owned by the caller;
over-quota requests surface the "won't fit" message instead of failing mid-generation.

---

## Phase 5 — UI

- [ ] `services/ui/src/services/api.ts`: add artifact + usage methods (see `ARTIFACTS.md §9`).
- [ ] `services/ui/src/pages/Storage.tsx` (user): progress bar, by-kind, recent, ≥90% banner.
- [ ] `services/ui/src/pages/admin/SystemStorage.tsx` (admin): per-user table + quota editor + disk stats.
- [ ] `services/ui/src/pages/Artifacts.tsx` (global library): virtualized list, range audio
      player (signed URL), image thumbnails, rename/delete (hard-delete phrase prompt),
      "unattached" badge, restore-within-24h state.
- [ ] Per-workspace Artifacts tab in the workspace view.

**Acceptance:** user can browse/play/download/delete artifacts; storage page warns near quota;
admin can see system usage and set quotas.

---

## Phase 6 — Tests, CI & deploy

- [ ] pytest (`@pytest.mark.local_only`): local provider rw, `check-fit` low-free simulation,
      quota enforcement, 90% warn, soft-delete + restore + 24h purge, hard-delete phrase,
      ownership authz, workspace-orphan flow.
- [ ] Vitest: storage-usage render, artifact library, fit/warning banner.
- [ ] `git push origin microservices` → confirm CI green (`gh run list --branch=microservices`).
- [ ] `./scripts/deploy_remote.sh jeremiah@192.168.2.205`.
- [ ] Live verification: upload TTS artifact → stream → fill to >90% → confirm warn → exceed
      quota → confirm block → soft-delete → restore → hard-delete with phrase.

---

## Open constants (configurable via env / settings)
- `ARTIFACT_LOCAL_QUOTA_BYTES` = 5 GiB (global default; per-user override from day one)
- `ARTIFACT_WARN_PCT` = 0.90
- `ARTIFACT_UNDO_WINDOW_HOURS` = 24
- signed-URL TTL = 15 min
- purge-loop interval = 5 min
