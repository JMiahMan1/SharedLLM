# Pending Tasks

The one backlog for work that is **not done yet**. Verified against the code
on 2026-10-18. When something here ships, delete it; history lives in git.

## Security

### Other open findings from the 2026-08-22 code review

Re-checked 2026-10-15 and still present. About 80 medium/low findings from
that review were not re-checked one by one; the full list is in git history
(`docs/code_review_2026-08-22.md`, removed in the 2026-10-15 cleanup).

- **Tracebacks to clients**: the gateway's global exception handler returns
  `detail` and the full `traceback` in the 500 body
  (`services/gateway/main.py`, `global_exception_handler`).
- **Workspace tests**: the Docker reachability test is marked `local_only`
  and skips when no daemon is available; CI does not require the host socket
  (W1).
- **Bare imports in execution**: execution imports are package-safe; the
  remaining bare imports are intentional fallback imports inside
  try/except blocks (E3).
- **Discovery probes**: the port 80 and 8080 branches await `resp.text()`
  before reading it; other probes use `resp.content` bytes. The blanket
  exception remains as a safety net (E4).
- **Terminal context menu**: fixed; the menu no longer closes after opening
  (U1).
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

## Logging settings

`services/logging/main.py` resolves runtime configuration at startup and
assigns `LOG_RETENTION_DAYS` / `LOG_MAX_ENTRIES` from the resolved settings.
The retention and max entry settings now take effect.

## Artifact framework (`services/storage`): generator wiring remains

The local provider, artifact/quota database, API, signed range streaming,
24-hour soft delete, purge loop, gateway proxies, Artifact library, admin
Storage page, and the workspace-deletion 409 prompt (delete or orphan) are
implemented. Still open:

- TTS outputs now create artifacts: `/execute/tts`, Nextcloud
  `storage_text_to_audio`, and podcast renders. Image, video and document
  generators still need wiring.
- The local quota comes from `ARTIFACT_LOCAL_QUOTA_BYTES` and per-user rows in
  `UserStorageQuota`; the proposed Identity `artifact_local_quota_bytes`
  setting is not wired (storage does not read Identity settings).

## Raven capability gaps

- No browser automation and no screenshot feedback, so Raven cannot test UIs.
- No multi-agent delegation; one model instance does all the work.
- CI generation depends on GitHub credentials being present.
- No dependency-install awareness; missing packages are found by failing.
- Loop detection escalates but does not diagnose.
- No shared library/template reuse across workspaces.

## Media player

- The web player picker uses the empty target for this device; no separate
  `localMode` state is needed.
- Background playback stops when the app is swiped away from recents (the
  WebView is destroyed). Surviving that needs a native audio player.
- The remaining phases of `docs/MEDIA_OVERHAUL.md`.

## Voice

- `POST /execute/voice/command` returns a FAILURE result for transcripts that
  match no supported command. Covered by `test_voice_command.py`.

## Mobile app

- The Android build removes `android/app/src/main/assets/public/bundle.zip`
  after `npx cap sync`, so the OTA bundle is no longer packaged into the APK.
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
