# Pending Tasks

The one backlog for work that is **not done yet**. Verified against the code
on 2026-10-15. When something here ships, delete it; history lives in git.

## Security

### Identity's anonymous fallback still resolves to the admin account

`POST /api/resolve` (`services/identity/main.py`, the `if not user:` block
after the device lookup) tries `user_id` → `api_key` → `rag_user` →
`voice_id` → `device_id` and, when nothing matches, returns the system
default user, who is an admin.

The gateway no longer relies on it for family data: every route that reads
or writes family data goes through `_resolve_identity_from_request`, which is
strict (a missing or unknown key is a 401), and `/execute/*` forwarding uses
it via `_resolve_user_context`. What remains is the deliberate anonymous path
(`_resolve_identity_allow_anonymous`, used by voice, registered devices and
media stream URLs), which still gets an admin identity.

Proposed fix: when a credential was **presented but did not match**, return
401; keep the fallback only when no credential was presented, and resolve
anonymous callers to a non-admin identity. `/api/resolve` is shared by kiosks,
voice and intercom, so ship it alone with a LAN repro as its test. Validate
gateway auth with identity's strict `GET /api/internal/validate-api-key`, not
`resolve_identity`.

### Other open findings from the 2026-08-22 code review

Re-checked 2026-10-15 and still present. About 80 medium/low findings from
that review were not re-checked one by one; the full list is in git history
(`docs/code_review_2026-08-22.md`, removed in the 2026-10-15 cleanup).

- **CORS `*`** on identity (`services/identity/main.py`, `allow_origins=["*"]`).
- **Tracebacks to clients**: the gateway's global exception handler returns
  `detail` and the full `traceback` in the 500 body
  (`services/gateway/main.py`, `global_exception_handler`).
- **Workspace tests run on the host**: workspace_runtime runs pytest/lint with
  `subprocess.run(cwd=workspace)` in the container that holds the Docker
  socket, executing repo code outside the sandbox (W1).
- **Bare imports in execution** (`from announce_handlers import …`,
  `from schemas import …` inside `services/execution/main.py` and some
  handlers) that only work because of `sys.path` side effects (E3).
- **Discovery probes inert**: `device_discovery.py` calls `.lower()` /
  `ET.fromstring` on a `StreamReader` and an un-awaited `resp.text`; the
  blanket `except` hides it (E4).
- **Chat history not persisted on the queued-job path**; Tier 2 runs with an
  empty short-term history (`services/gateway/orchestrator.py`) (G4).
- **All inference serialized behind Raven**: the worker claims jobs one at a
  time, so one Raven mission blocks chat jobs (G5; see ADR 010).
- **Terminal context menu** closes ~300 ms after opening
  (`TerminalPane.tsx`, inverted `menuLeaveRef` check) (U1).

## Identity config DB: seeding and runtime integration

Source: `services/identity/seed.py`, `services/identity/models.py`, `services/config.py`.

- Seed the `.env` runtime keys that still have no GlobalSetting row, among them
  `ANNOUNCEMENT_BLACKLIST` and `GATEWAY_INTERNAL_URL`.
- Complete `resolve_runtime_config()`'s settings map for the keys it still
  ignores, and fix the name mismatches between `seed.py` and the map
  (`WORKSPACE_ROOT`/`workspace_root`, `MODELS_DIR`/`models_dir`,
  `PHRASEBOOK_PATH`/`phrasebook_path`).
- Keep first-run seeding non-destructive and test resolution against a live
  container.

## Logging settings are ignored

`services/logging/main.py` imports `LOG_RETENTION_DAYS` / `LOG_MAX_ENTRIES` by
value before `resolve_runtime_config()` runs, so the `log_retention_days` /
`log_max_entries` settings never take effect (defaults 30 days / 10000 always
apply). Read them from `services.config` at use time.

## Artifact framework (`services/storage`): not started

Design: `services/storage/ARTIFACTS.md`; build plan:
`services/storage/ARTIFACT_IMPLEMENTATION_PLAN.md`. None of it exists in
`services/storage/main.py` yet: artifact models and database, local provider,
signed URLs with range streaming, usage/quota endpoints and gateway proxies,
TTS/image/video wiring, and the Storage / Artifacts UI pages.

## Raven capability gaps

- No browser automation and no screenshot feedback, so Raven cannot test UIs.
- No multi-agent delegation; one model instance does all the work.
- CI generation depends on GitHub credentials being present.
- No dependency-install awareness; missing packages are found by failing.
- Loop detection escalates but does not diagnose.
- No shared library/template reuse across workspaces.

## Media player

- Selecting a remote Music Assistant speaker (`ma:` target) still calls
  `maPlayer.connect()`, starting the in-browser Sendspin player just to send a
  JSON-RPC command. Remote speakers should be driven without the browser
  becoming a player, through `services/execution/handlers/mass_client.py`.
- The device picker highlights "Web Player" from `!selectedTarget` rather than
  `localMode`.
- Background playback stops when the app is swiped away from recents (the
  WebView is destroyed). Surviving that needs a native audio player.
- The remaining phases of `docs/MEDIA_OVERHAUL.md`.

## Voice

- `POST /execute/voice/command` (`services/execution/main.py`) returns `null`
  when a transcript matches neither the light nor the media keywords, instead
  of a FAILURE result.

## Mobile app

- `services/ui/android/app/src/main/assets/public/bundle.zip` ships a full
  copy of the web bundle inside the APK. Harmless, but it roughly doubles the
  APK; exclude it from `npx cap sync`.
- Health: no background step sync yet (`docs/HEALTH_STEPS.md`).

## Family hub

- Calls: media (audio/video in-app) needs a Talk client library or LiveKit,
  and a reachable STUN/TURN server off the LAN.
- Drawing: multi-user live drawing on one board.
- Arcade: phases 2-5 of `docs/ARCADE_INTEGRATION.md`.

## Tests

- `test/unit/test_gateway_auth.py::test_gateway_extracts_bearer_token` passes
  alone but fails in a whole-suite run: several gateway tests inject a mocked
  `intent_engine` into `sys.modules` at module scope and never restore it.
