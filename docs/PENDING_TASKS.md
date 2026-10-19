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

Fixed 2026-10-18. Runtime settings now come from Identity under these keys:

- `workspace_root` and `workspace_runtime_root`: roots for the execution
  service and the workspace runtime service. Each service has its own mount,
  so the keys stay separate; they normally name the same host directory.
  They are seeded from `WORKSPACE_ROOT` and `WORKSPACE_RUNTIME_ROOT`.
- `models_dir`: seeded from `MODELS_DIR`.
- `phrasebook_path`: seeded from `PHRASEBOOK_PATH`.
- `announcement_blacklist`: seeded from `ANNOUNCEMENT_BLACKLIST`.
- `gateway_internal_url`: seeded from `GATEWAY_INTERNAL_URL`.

`resolve_runtime_config()` maps all of them, and first-run seeding fills an
empty value or replaces the shipped default without overwriting a value an
operator has changed.

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

- `test/unit/test_gateway_auth.py::test_gateway_extracts_bearer_token` is
  isolated: the test no longer replaces `sys.modules['redis']` globally; it
  patches `services.gateway.cache.get_redis` and `history.get_redis` with a
  local fake. `voice_speaker.decode` also ignores non-string Redis records.
  The combined gateway/auth suite passes.
