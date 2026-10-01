# Pending Tasks

Single source of truth for documentation tasks that are **not yet complete**.

Verified against the codebase on 2026-07-11. Tasks that were already done have
been removed from their source docs; tasks whose direction was superseded by a
working alternative (e.g. the DNS relay, implemented via `dns-sync` +
`dns-forwarder`) are also removed.

## ⚠️ CRITICAL (open) — `/execute/*` is unauthenticated and runs as the default admin
Found and measured 2026-10-01. Deliberately **not** fixed yet — see "Why it is deferred".

Any client on the LAN can drive the whole house — lights, locks, covers, climate,
media — with **no credentials at all**, resolving as the `default` user
(`is_admin: true`). This also means the per-entity protection shipped in
`docs/ENTITY_PROTECTION.md` is **bypassed entirely on this path**, because
protection is evaluated against a `user_context` that is always "default admin".

Measured from the LAN against production (`http://192.168.2.205:8080`, no
`Authorization` header at all):

| request | result |
|---|---|
| `POST /execute/entity/search` | **200** — returned **696 entities** (whole house inventory) |
| `POST /execute/media/status` | **200** |
| `POST /execute/ha_service` | reached **Home Assistant** and attempted a real call (`HA returned 400 … climate/set_temperature`); failed only because the entity id was fake |

Externally it is contained: `https://ai.sumemail.com/execute/entity/search`
returns **405 from Cloudflare** (the origin answers; the edge blocks it). So this
is LAN-internal exposure, not internet-facing.

### Root cause
`POST /api/resolve` (`services/identity/main.py`) tries `user_id` → `api_key` →
`rag_user` → `voice_id` → `device_id`, and when **none** match, **falls back to the
system default user, which is an admin**. The gateway's `_resolve_user_context`
(`services/gateway/main.py`, used by every `_forward_execution_request` route)
guarded with `creds_data.get("user")`, which is truthy (`'default'`) *after* that
fallback — so its `BUG-02` docstring claim that "unauthenticated calls must fail
with 401" is not actually achieved. Proven directly:

```
POST identity:8001/api/resolve {"api_key":"totally-bogus-key"}
→ {"user":"default","is_admin":true,"api_key":"bf7ca7c0…"}
```

### Proposed fix
In `/api/resolve`, when credentials were **presented but none matched**, return
**401** instead of falling back; keep the fallback only when **no** credential was
presented at all. That preserves kiosk / media-token / voice / intercom behaviour,
which rely on anonymous resolution, while rejecting a bad credential outright.

### Why it is deferred
`/api/resolve` is shared resolution used by media kiosks, voice, and intercom, and
this repo has a history of auth churn (the `BUG-01` / `BUG-02` comments are
scar tissue from the last pass). None of those callers can be exercised from the
dev box, so shipping an app-wide auth change untested — immediately after a
different security fix (`88ceec44`) — is how the house gets broken. It wants its
own commit with the LAN repro above as the test.

Note for whoever picks this up: **do not** validate gateway auth by calling
`resolve_identity`. `88ceec44` hit exactly that trap. Use the strict, no-fallback
`GET /api/internal/validate-api-key` on identity instead.

## Identity Config DB — seeding & runtime integration
Source: `services/identity/seed.py`, `services/identity/models.py`, `services/config.py`

- Complete `seed.py` with all `.env` runtime keys that should persist to GlobalSettings table:
  - Missing seeds: `ALPACA_SD_URL`, `WORKSPACE_ROOT`, `WORKSPACE_REGISTRY_PATH`, `WORKSPACE_DATABASE_URL`, `VOLUME_MANIFEST_PATH`, `VOLUME_BACKUP_ROOT`, `GIT_WEBHOOK_SECRET`, `ANNOUNCEMENT_BLACKLIST`, `LOCAL_NOTES_ROOT`, `FAST_PATH_THRESHOLD`, `MODELS_DIR`, `TEMP_MEDIA_DIR`, `SCRIPTS_DIR`, `LEGACY_ENV_PATH`, `COMPOSE_PROJECT_DIR`, `GATEWAY_INTERNAL_URL`, `LLAMA_SERVER_PROXY_URL`
- Fix key name mismatches between seed.py and settings_map: `WORKSPACE_ROOT` vs `workspace_root`, `MODELS_DIR` vs `models_dir`, `PHRASEBOOK_PATH` vs `phrasebook_path`
- Complete `resolve_runtime_config()` settings_map in `services/config.py` with all missing entries:
  - Missing map keys: `MA_URL`, `MA_TOKEN`, `MODELS_DIR`/`models_dir`, `PHRASEBOOK_PATH`/`phrasebook_path`, `EXTRA_INDEX_URL`, `ALPACA_SD_URL`, `WORKSPACE_*`, `VOLUME_*`, `GIT_WEBHOOK_SECRET`, `ANNOUNCEMENT_BLACKLIST`, `LOCAL_NOTES_ROOT`, `FAST_PATH_THRESHOLD`, `TEMP_MEDIA_DIR`, `SCRIPTS_DIR`, `LEGACY_ENV_PATH`, `COMPOSE_PROJECT_DIR`, `GATEWAY_INTERNAL_URL`, `LLAMA_SERVER_PROXY_URL`
- Verify first-run seeding (non-destructive — existing settings must not be overwritten)
- Test runtime config resolution against live container

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
Source: superseded plan doc removed 2026-09-21; items still open

- Enhance E2E Playwright test coverage.
- Monitor GHA pipelines after push.

## Raven Capability Gaps
Source: `docs/RAVEN_CAPABILITY_GAP_ANALYSIS.md`, `docs/RAVEN_AUDIT_BLUEPRINT.md`, `docs/raven_learning.md`

- No native browser automation tool — Raven can't test web UIs interactively
- No screenshot/visual feedback loop — can't verify UI state or visual rendering
- No multi-agent delegation — all work done by single LLM instance
- CI workflow generation conditional on GitHub credentials — no automated validation without CI
- No explicit dependency-install awareness — must discover and install deps manually
- Loop detection escalates but doesn't auto-diagnose — Raven still needs to figure out fixes
- No cross-workspace shared library/template reuse — each mission starts from scratch

## Open items found during the 2026-09-21 bug sweep

### Mobile OTA / app updates
- Live server metadata and bundle are still out of sync until the next deploy
  (`/api/app-updates/version` advertised `c2946120` while `bundle.zip` contained
  `2c8dc477`). Redeploy so the two converge; the gateway now derives the
  advertised SHA from the bundle, so this should not recur.
- `services/ui/android/app/src/main/assets/public/bundle.zip` ships a full copy
  of the web bundle *inside* the APK's own assets. Harmless but roughly doubles
  APK size — exclude it from `npx cap sync`.

### Media player
- Selecting a remote Music Assistant speaker (`ma:` target) still calls
  `maPlayer.connect()`, which starts the in-browser Sendspin audio player purely
  to send a JSON-RPC command. Remote speakers should be driven without the
  browser becoming a player; the MA logic belongs behind
  `services/execution/handlers/mass_client.py`, not a new gateway endpoint.
- The device picker highlights "Web Player" based on `!selectedTarget` rather
  than `localMode`, so it looks active when no device has been chosen yet.

### Voice
- `POST /execute/voice/command` (`services/execution/main.py`) falls off the end
  of the function when a transcript matches neither the light nor the media
  keyword lists, returning `null` to the caller instead of a FAILURE result.

### Frontend typing
- ~~`services/ui/src/ma-stream-test.ts` still has ~24 TypeScript strictness
  errors~~ **resolved**: the dev-only harness and its `e2e/ma-stream.spec.ts`
  were deleted (with the config entry) and **all pre-existing `tsc` errors are
  now fixed** — `npx tsc -b` is clean as of `2b6a3552`.
- ~~Playwright could not even collect the suite~~ **resolved**: the suite is
  split into hermetic and live projects and module-load throws were fixed
  (`d1a693b4`, `0d7126c2`).
- ~~`QuickNotesWidget` computes an `error` state that is never set —~~ fixed:
  the widget sets and renders the error message (`QuickNotesWidget.tsx`).

### Test suite
Of the 13 pre-existing failures found in this sweep, 12 are fixed (see the
`fix(tests)` commit). One remains:

- `test/unit/test_gateway_auth.py::test_gateway_extracts_bearer_token` passes on
  its own and when `test/` runs alone, but fails in a whole-suite run: the
  gateway never calls `/api/resolve`, so the captured body is None. Some earlier
  module changes the chat handler's branch (several gateway tests inject a
  mocked `intent_engine` into `sys.modules` at module scope and never restore
  it, which is the most likely candidate). Not run by any workflow that
  triggers on this branch — `python-tests.yml` is limited to `main` /
  `annoucements` and to `app/**` + `test/**` paths.

## Added 2026-09-24 (v1.4.9) — historical

- **Notes:** the editor now saves with the `write` action (full replace);
  `append` is quick-capture only. See `docs/NOTES.md`.
- **Themes:** themes are Jarvis-wide; per-widget theme pickers were removed.
  See `docs/THEMES.md`.
- **Health/steps:** per-user step goal endpoints added; the Android widget and
  dashboard now show only real derived metrics. Open gap: no background step
  sync service (see `docs/HEALTH_STEPS.md`).
- **Wander:** live map with Places, proximity clustering, member focus,
  accuracy/last-seen on cards. See the Geo update section.

## Family hub roadmap — status

- **Phase 1 — goals + achievements: SHIPPED** (engine, endpoints, Wander panel).
- **Phase 1a — Talk depth: SHIPPED** — reactions (`183d2757`) and polls
  (`3a021061`), surfaced in `ChatPanel` via Talk's own APIs.
- **Phase 2 — opt-in activity sharing: SHIPPED** — Identity rows + geo/achievement
  enforcement, `ActivitySharingPanel` in Settings, tests. See
  `docs/ACHIEVEMENTS.md`.
- **Remaining:** chat envelope + activity cards and encouragement references;
  Skylight star grants; games over a Talk bot; Excalidraw canvas with
  Raven/Alpaca "make it way better"; calls (Talk signalling + TURN, LiveKit
  fallback); music + recipes; arcade feature slice (alpaca prerequisite is
  already satisfied — `GET /api/games` + `/api/games/<slug>` exist and are
  regression-tested — see `docs/ARCADE_INTEGRATION.md`).
- **Device ledger** shipped (`StepLedger.java` + backfill); remaining gap is
  true background sync (foreground service/WorkManager) — see
  `docs/HEALTH_STEPS.md`.

