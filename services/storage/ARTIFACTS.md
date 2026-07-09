# Artifact Framework — Design & Architecture

> Status: Designed (approved). Implementation tracked in `ARTIFACT_IMPLEMENTATION_PLAN.md`.
> Scope: Managed, lifecycle-independent artifacts produced by workspaces (TTS audio,
> generated images, video, exported docs) that must survive workspace deletion and be
> browsable/manageable by their owner. Git-tracked outputs stay in the workspace repo
> and are NOT represented as artifacts.

This feature **extends the existing `storage` service** (port 8005). It adds a local
storage backend, an artifact metadata store, a size fit-check with quota enforcement,
a 24-hour soft-delete undo window, and per-user / system storage-usage reporting.

---

## 1. Goals & non-goals

**Goals**
- Workspaces can produce artifacts without requiring a git repo.
- Artifacts persist independently of the workspace lifecycle.
- Before an artifact is created, its size is checked against free local space and the
  user's quota; the user is told if it will not fit.
- A per-user local quota (default 5 GiB) is enforceable from day one.
- Deletions are recoverable for 24h; a hard-delete bypass exists with phrase confirmation.
- Owners (and admins) can see storage usage per user and system-wide.

**Non-goals**
- Replacing the RAG indexing role of the storage service.
- Snapshotting workspace git repos as artifacts.
- Cross-device replication (Nextcloud/S3 backends are *optional* and out of v1 scope
  beyond the existing provider abstraction).

---

## 2. Storage backends

The service already defines `StorageProvider` (`services/storage/providers.py`) and a
`build_provider` factory supporting `nextcloud` only, with a commented `local` stub.

- **`LocalStorageProvider`** (new, `services/storage/providers_impl/local.py`): writes
  bytes to `ARTIFACTS_ROOT` at `/artifacts/{owner}/{kind}/{artifact_id}`. Paths are
  generated from the artifact `id` — never user-supplied — so there is no traversal risk.
  Implements `list_entries`, `get_content`, `write_content` (and `delete_content`).
- **`NextcloudStorageProvider`** (existing): remains available as an alternate backend;
  selected per artifact via `storage_backend`. Default backend is `local`.

`build_provider` is extended with `if config.kind == "local": ...`.

---

## 3. Data model (new `artifacts.db` in the storage service)

A new SQLModel engine is added to `storage` (SQLite at `ARTIFACTS_ROOT/artifacts.db`,
i.e. `/artifacts/artifacts.db`). Two tables:

### `Artifact`
| field | type | notes |
|-------|------|-------|
| `id` | str (PK, uuid) | also used as the on-disk filename |
| `owner_user` | str | Identity username (ownership) |
| `workspace_id` | str \| None | originating workspace; **no FK**, survives deletion |
| `kind` | str (enum) | `tts_audio`\|`image`\|`video`\|`document`\|`other` |
| `name` | str | display name / filename |
| `mime_type` | str \| None | |
| `size` | int | bytes (exact, measured on write) |
| `storage_backend` | str | `local`\|`nextcloud`\|... |
| `storage_ref` | str | backend-specific path/uri |
| `sha256` | str \| None | for dedup / idempotency |
| `generator` | str \| None | e.g. `kokoro_tts` |
| `metadata` | JSON | duration, voice, prompt, source, etc. |
| `created_at` | datetime | |
| `updated_at` | datetime | |
| `deleted_at` | datetime \| None | soft-delete timestamp (24h undo window) |
| `is_attached` | bool | false once the workspace is gone |

### `UserStorageQuota`
| field | type | notes |
|-------|------|-------|
| `username` | str (PK) | |
| `quota_bytes` | int | per-user override |
| `updated_at` | datetime | |

Global default lives in the Identity `DEFAULT_GLOBAL_SETTINGS` key
`artifact_local_quota_bytes` (default `5 GiB` = `5368709120`).

---

## 4. Size fit-check & quota (headline behavior)

`check_fit(owner_user, needed_bytes) -> FitResult`:
```
free  = shutil.disk_usage(ARTIFACTS_ROOT).free
quota = user_quota(owner_user) or ARTIFACT_LOCAL_QUOTA_BYTES   # per-user else global
used  = Σ Artifact.size WHERE owner_user = owner AND deleted_at IS NULL
fits  = (used + needed) <= min(quota, free)
warn  = (used / quota) >= ARTIFACT_WARN_PCT                    # default 0.90
```
`FitResult` = `{fits, needed, used, quota, free, headroom, warn, message}`.

- `warn` never blocks; it surfaces a message: *"You're at ≥90% of your storage. This may
  fail due to space — clean up or request more from your admin."*
- `fits=False` blocks creation with a clear message: *"Cannot store artifact: needs
  ~X MB, only Y MB free of Z MB quota/local."*
- **Preflight** `POST /artifacts/check-fit {size, owner}` lets generators (and the UI)
  warn the user *before* generation begins. `POST /artifacts` re-checks with the real
  byte length.

---

## 5. Endpoints (added to `storage`, proxied by gateway under `/api/artifacts/*`)

| method | path | purpose |
|--------|------|---------|
| POST | `/artifacts` | create (multipart/b64 + metadata) → fit-check → write → row → signed URL; sha256 dedup |
| GET | `/artifacts` | paginated list (`limit`/`offset`); filters `kind`,`workspace_id`,`attached`,`search` |
| GET | `/artifacts/{id}` | metadata |
| GET | `/artifacts/{id}/stream` | **range-capable** (`Accept-Ranges`, `206 Partial Content`) for media |
| GET | `/artifacts/{id}/download` | short-lived **signed URL** (HMAC token, ~15 min) |
| PATCH | `/artifacts/{id}` | rename / metadata |
| DELETE | `/artifacts/{id}` | **soft delete** (sets `deleted_at`; recoverable 24h) |
| DELETE | `/artifacts/{id}?hard=true` | **hard delete**; body `{confirm_phrase:"delete <filename>"}`; bypasses window, purges immediately |
| POST | `/artifacts/{id}/restore` | undo a soft delete (within 24h) |
| POST | `/artifacts/check-fit` | preflight size/quota gate |

Storage-usage endpoints:
| GET | `/storage/usage` | current user: `{used, quota, free, count, by_kind, warn}` |
| GET | `/storage/usage/{user}` | admin, per user |
| GET | `/storage/usage/system` | admin: per-user table + volume `total/free/used` |
| PUT | `/storage/quota/{user}` | admin sets per-user quota |

**Authz:** internal calls (generators) use `X-Internal-Secret`; user calls resolve
`owner_user` from `UserContext` (forwarded by gateway). List/get/delete are gated on
`owner_user == caller` with admin bypass (mirror `verify_entity_access` in
`workspace_runtime/main.py`).

---

## 6. Scheduled purge job (24h undo window)

A background asyncio task started in the storage `lifespan` runs every ~5 minutes:
purges `Artifact` rows where `deleted_at IS NOT NULL` and
`now - deleted_at > ARTIFACT_UNDO_WINDOW_HOURS` (24h) — deletes the bytes via the
backend and removes the row. Hard deletes skip the window.

---

## 7. Workspace deletion & orphaning

Gateway `DELETE /api/workspaces/{id}` (`services/gateway/main.py:~3794` →
`workspace_runtime/main.py:1510`): if the workspace has artifacts and the request does
not include `delete_artifacts`, return `409 {artifact_count, needs_confirmation:true}`
so the UI prompts. On confirmation `delete_artifacts=true` → delete artifact rows+bytes;
otherwise keep them with `is_attached=false` (orphaned, still owned). Survival is the
default because artifacts live in a separate DB with no cascade.

---

## 8. Generator integration (opt-in, general framework)

A helper `create_artifact(user_context, workspace_id, kind, name, data, mime, generator,
**meta)` in the execution service POSTs to storage `/artifacts`. Wire, in order:
- **TTS**: `services/execution/main.py:1075` (`/execute/tts`),
  `services/execution/handlers/storage.py:61` (`storage_text_to_audio`),
  `services/execution/handlers/talk.py:245` → `kind=tts_audio` (call `check-fit` first).
- **Image**: alpaca/Stable Diffusion (`services/gateway/tool_registry.py`) → `image`.
- **Video**: `services/execution/handlers/video.py` → `video`.
- **Doc exports** → `document`.

`workspace_id` is taken from `UserContext` when present (null = global artifact).
Git commits remain in the repo (no artifact created).

---

## 9. UI

- **`Storage.tsx` (user):** usage progress bar (used/quota), by-kind breakdown, recent
  artifacts, **≥90% warning banner** + "request more space" CTA.
- **Admin `SystemStorage.tsx`:** per-user usage table + per-user quota editor + volume
  disk stats.
- **`Artifacts.tsx` (global library):** virtualized list (`@tanstack/react-virtual`),
  inline `<audio controls>` (range streaming via signed URL), image thumbnails,
  download/rename/delete (hard-delete phrase prompt), "unattached" badge, restore-within-
  24h state.
- **Per-workspace:** Artifacts tab in the workspace view.
- `services/ui/src/services/api.ts`: `listArtifacts`, `getArtifact`, `streamArtifact`,
  `downloadArtifact`, `deleteArtifact`, `restoreArtifact`, `createArtifact`,
  `checkArtifactFit`, `getStorageUsage`, `getSystemStorageUsage`, `setUserQuota`.

---

## 10. Deployment

- `docker-compose.yml` storage service (~line 328): add a named volume
  `storage_artifacts:/artifacts`, env `ARTIFACTS_ROOT=/artifacts` and
  `ARTIFACT_LOCAL_QUOTA_BYTES=5368709120`. The CI storage build job already exists.
- Seed `artifact_local_quota_bytes` in `services/identity/models.py`
  `DEFAULT_GLOBAL_SETTINGS`.
- No data migration (greenfield tables).
- `services/storage/requirements.txt`: add `sqlmodel`, `python-multipart`.

---

## 11. Testing

- pytest (`@pytest.mark.local_only`): local provider read/write, `check-fit` with
  simulated low free space, quota enforcement, 90% warn flag, soft-delete + restore +
  24h purge, hard-delete phrase verification, ownership authz, workspace-orphan flow.
- Vitest: storage-usage page render, artifact library, fit/warning banner.
