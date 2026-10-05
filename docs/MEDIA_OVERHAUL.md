# MEDIA OVERHAUL — Implementation Reference

---

## HOW TO USE THIS DOCUMENT (read fully before touching code)

You are implementing a large redesign of the **Media** feature of Jarvis OS (repo root `SharedLLM/`). This document is the single source of truth. Follow it **in order**. Do not skip phases. Do not invent scope.

### Hard rules
1. **Temp files:** ALWAYS use `<repo>/.tmp/` for scratch files, screenshots, downloads, and logs. NEVER use `/tmp` or `/var/folders` (from `AGENTS.md`, no exceptions).
2. **One task = one commit.** Each task below has an ID (for example `P1-T3`). Commit message format: `fix(media): P1-T3 <short summary>` or `feat(media): …`. Do not batch tasks.
3. **Tests before fixes (TDD for bugs).** For every bug ID (`BUG-xx`), first write a test that FAILS on current code, run it, and confirm it fails. Then fix the bug and confirm the test passes. Paste both run outputs into the commit body (just the summary lines).
4. **Gate after every task.** All of these must pass before you commit (see §9.1 for the exact commands):
   - UI: `npm run lint`, `npx tsc -b`, `npm test`
   - Backend: `pytest` for any service you touched
   If anything fails, fix it before moving on. Never mark a task done while it is red.
5. **Never delete an existing test to make things pass.** If an existing test encodes a behavior that this doc marks as a bug (for example `test_proxy_media_status_falls_back_to_first_user`), rewrite the test to assert the new correct behavior, and say so in the commit.
6. **Verify upstream APIs before coding against them.** Items marked **(VERIFY)** involve Music Assistant (MA), Audiobookshelf (ABS), or Home Assistant (HA) API shapes that were not confirmed against a live server. Before implementing, confirm the command or endpoint with the procedure in §10 and record the real request and response in `docs/MEDIA_UPSTREAM_API_NOTES.md`. If reality differs from this doc, reality wins. Write the difference down.
7. **Do not touch unrelated features** (Family/Talk, Geo, Chat, and so on). The git tree has unrelated uncommitted work (`ChatPanel.tsx`, `Family.test.tsx`, `test_talk_reactions.py`). Leave those files alone and never `git add -A`. Stage files explicitly.
8. **Small files.** New components go in `services/ui/src/features/media/…` (layout in §6.2). No file over ~400 lines. `Media.tsx` (currently 2,311 lines) must end up as a thin route shell.
9. **If you are stuck for more than 3 attempts on the same failure, stop.** Write what you tried in `docs/MEDIA_OVERHAUL_BLOCKERS.md` and move to the next independent task.

### Glossary

| Term | Meaning |
|---|---|
| MA | Music Assistant server (v2.10+). Its JSON-RPC WebSocket is reached through the gateway at `WS /api/ma-jsonrpc?token=` |
| ABS | Audiobookshelf server |
| HA | Home Assistant |
| Sendspin | The `@sendspin/sendspin-js` library, which streams MA audio into a browser `<audio>` element. Gateway proxy: `WS /api/sendspin?token=` |
| Web Player | This browser or Android app acting as an MA player (via Sendspin) |
| Remote player | Any speaker or TV: an HA `media_player.*` entity or an MA player (`ma:<player_id>`) |
| Gateway | `services/gateway/main.py` (FastAPI, port 8080 behind Caddy) |
| Execution | `services/execution/main.py` (runs HA/MA/ABS calls) |

---

## 1. CONTEXT: why this work exists

Media is one of the most-used surfaces in Jarvis OS on both desktop and the Android (Capacitor) app. Today it is **functional but fragile and visually plain**:
- **Playback stops when you leave the Media page.** The web player is created inside the page and torn down on unmount.
- There is **no mini-player, no queue, no shuffle/repeat, no artist/album pages, no artwork in lists, no lock-screen scrubber, and no reliable Android background audio.**
- `Media.tsx` has one 2,311-line component. The play logic is copy-pasted 3× (web / MA / HA) × 4 (track / playlist / audiobook / local).
- The backend has **critical security holes**: a client-supplied identity is trusted, there is SSRF in the image proxy, and tokens show up in logs. Several ABS features are silently broken because of wrong API paths and wrong credential keys.
- Tests mostly run against a live LAN server with sleeps and conditional skips, so they don't really prove anything.

**Goal:** an award-caliber listening experience that works identically on phone, tablet, desktop, and the Android APK. Every change must be proven by automated tests plus the manual device checklist in §9.4.

---

## 2. CURRENT ARCHITECTURE (as-is map)

```
Browser / Capacitor WebView
 └─ pages/Media.tsx (2311 lines; all state in useState)
     ├─ useMAWebPlayer()  lib/maWebPlayer.ts (1127 lines)
     │    ├─ WS /api/sendspin?token=…      → gateway → MA sendspin (audio)
     │    └─ WS /api/ma-jsonrpc?token=…    → gateway → MA JSON-RPC (dumb pipe, any command)
     ├─ poll every 3s: POST /execute/media/status → gateway → execution → HA /api/states (full read, no cache)
     ├─ every 5s while playing: POST /execute/media/state/sync (web player state → SQLite)
     ├─ react-query: /api/media/music-assistant/{playlists,recent,search}, /api/media/audiobookshelf/{libraries,library/{id},search,last-played}
     ├─ GET /api/media/detail?uri=, POST /api/media/favorite
     └─ <img src="/api/media/imageproxy?path=…&token=…">
 └─ components/widgets/ActiveMediaWidget.tsx (dashboard; separate 5s poll, duplicate logic)
 └─ components/LocalAudioPlayer.tsx (DEAD CODE: only imported by a test)
 └─ lib/webPlayer.ts (multi-tab coordination: effectively inert; only player-id persistence is live)
Android: MainActivity.java (WebView autoplay allowed), widgets/MediaWidget.java (native widget; parses wrong fields)
         Manifest declares FOREGROUND_SERVICE_MEDIA_PLAYBACK but NO service exists.
```

Routing: `App.tsx:122` renders `<ProtectedRoute><Media/></ProtectedRoute>`. **Each route builds its own shell** (`App.tsx:35-69`), so the whole layout remounts on every navigation.

Design tokens: `src/index.css:17-55` (`--site-*`, `--color-surface-*`, `--radius-*`), theme engine `src/themes/siteTheme.ts`, and utility classes `.glass-panel`, `.glass-card`, `.glass-input`, `.glass-button`, `.skeleton`. Media currently hard-codes `cyan/purple/pink/amber` Tailwind colors, which **ignore the active theme**. Note: `tailwind.config.js` is a v3 file that Tailwind v4 ignores (no `@config` in `index.css`).

---

## 3. AUDIT FINDINGS: the bug ledger

Every row must be fixed **and** get a regression test (Rule 3). Severity: **P0** = security / data integrity, **P1** = user-visible broken behavior, **P2** = quality / perf / a11y.

### 3.1 Backend: security (Phase 1, do first)

| ID | Sev | Where | Problem | Required fix | Required test |
|---|---|---|---|---|---|
| BUG-01 | P0 | `gateway/main.py:8537` `_resolve_user_context` | Trusts `body["user_context"]` sent by the client, so anyone can impersonate any user or admin, or inject `ha_url`/`ha_token` (SSRF) | **Always** delete `user_context` from the incoming body. Resolve only from the authenticated request (`_resolve_identity_from_request`) | pytest: POST `/execute/media/status` with a forged `user_context` → the forwarded body contains the *authenticated* user, not the forged one |
| BUG-02 | P0 | `gateway/main.py:8551`, `execution/main.py:697,741,2237,2512` | Unauthenticated calls fall back to "first user" | **DONE 2026-09-28**: no service call borrows another user's credentials any more. Execution raises `ServiceNotConfiguredError` (announce, entity search, the four MA endpoints, all six `/discovery/*` routes, which now take explicit `ha_url`/`ha_token` params from the gateway) and answers `status: FAILURE` naming the user; the gateway `/api/entities` forwards the caller's HA creds and fails the same way. The remaining `resolve_first_user()` calls are the intentionally shared system account (Skylight) and the admin `as_user` act-as path | pytest: `execution/tests/test_credentials_per_user.py` (9) + `gateway/tests/test_entities_per_user.py` (3) — no first-user call, and the error names the user and the service. Policy and grants: `docs/PER_USER_CREDENTIALS.md` |
| BUG-03 | P0 | `execution/main.py:737` transport | No `verify_entity_access` (play has it at :692) | Call the same access check used by play | pytest: user without access to entity X → transport on X is denied |
| BUG-04 | P0 | `gateway/main.py:9102` `/api/ma-jsonrpc`, `:9072-9099` debug routes | Any API key can send **any** MA command (including admin/config) | Add a command **allowlist** (the exact list is in §7.3). Reject anything else with an error frame. Debug routes are admin-only (`is_admin`) | pytest: send `config/…` → rejected; send `player_queues/play_media` → forwarded; non-admin GET debug → 403 |
| BUG-05 | P0 | `gateway/main.py:9655` imageproxy | For a full URL with `/imageproxy` in the path it fetches **any host** (SSRF) | Only fetch full URLs whose host equals the user's configured `mass_url`/`audiobookshelf_url`/`ha_url` host; any other host is **rebased** onto the configured base for the service implied by the path (original host discarded → SSRF stays closed; keeps mismatched-host MA/HA covers working — owner decision 2026-09-27). 400 only when that service isn't configured | pytest: `path=http://169.254.169.254/imageproxy?x` → the metadata host is never contacted (rebased to configured MA → 200); a matching MA host → proxied; target service unconfigured → 400 |
| BUG-06 | P0 | `gateway/main.py:2255` logging middleware, `ma_ws_client.py:108,388,743`, `main.py:8833,8900,9162,9411` | Tokens logged via `?token=` query strings and WS URLs | Add a `redact_url()` helper (it masks `token`, `api_key`, `access_token` query values as `***`) and use it everywhere URLs are logged. Remove token prefix/suffix logging | pytest: call the helper; plus a caplog test that a request to `/api/media/imageproxy?token=SECRET` never logs `SECRET` |
| BUG-07 | P0 | `abs_client.py:325`, `handlers/audiobookshelf.py:182` | ABS JWT embedded in stream URLs sent to HA/MA devices (it persists in HA history) | Route remote-device ABS streams through a gateway URL using a **short-lived signed media token** (§7.4), never the raw ABS key | pytest: the URL handed to HA contains no ABS key and has a `mt=` signed token that expires |
| BUG-08 | P0 | `execution/main.py:330-361` port 8888 file server | No auth; guessable ids (`vid-`+8 hex) | Require a signed media token (§7.4); use `secrets.token_urlsafe(16)` ids. `.part` serving stays (progressive playback starts mid-download — owner decision 2026-09-26) but is gated behind the same token auth | pytest: no token → 403; valid → 200 (incl. `.part`) |
| BUG-09 | P1 | `ha_client.py:20`, Roku handlers, `handlers/media.py:549` | TLS verify off by default | Default `verify_ssl=True`. Honor a per-user `allow_insecure_tls` identity flag only if it already exists; otherwise add env `MEDIA_ALLOW_INSECURE_TLS=false` | Unit test on the session factory |

### 3.2 Backend: functional bugs (Phase 2)

| ID | Sev | Where | Problem | Fix | Test |
|---|---|---|---|---|---|
| BUG-10 | P1 | `gateway/main.py:9632,9669-9670` | imageproxy reads `creds["abs_url"]`/`["abs_api_key"]`, but identity returns `audiobookshelf_url`/`audiobookshelf_api_key` (`identity/schemas.py:40-43`), so **every ABS cover is a 400** | Use the correct keys | pytest: ABS cover path → upstream called with the Bearer ABS key |
| BUG-11 | P1 | imageproxy | No `Cache-Control`/ETag; buffers the whole image; no resize | Stream the response. Add `Cache-Control: private, max-age=86400, immutable` and forward ETag / `If-None-Match` → 304. Add an optional `w=` param that forwards MA's `size=` when the upstream is MA **(VERIFY MA imageproxy `size` param)** | pytest: headers present; conditional request → 304 |
| BUG-12 | P1 | `abs_client.py:285-412` | Wrong ABS paths (`/api/v1/…` or missing `/api`); `update_progress` uses POST instead of PATCH. So `get_book` is broken and progress is always 0% | Correct to the real ABS API **(VERIFY each)**: `GET /api/items/{id}?expanded=1`, `GET /api/me/progress/{id}`, `PATCH /api/me/progress/{id}`, `GET /api/me/items-in-progress`, `POST /api/items/{id}/play`, `POST /api/session/{id}/sync`, `POST /api/session/{id}/close`, `GET /api/libraries/{id}/search?q=`, `GET /api/libraries/{id}/personalized` | pytest with `aioresponses` (or the mocking lib already used in `services/execution/tests`) asserting method + path for each |
| BUG-13 | P1 | `handlers/audiobookshelf.py:308,316,437` | Progress hardcoded to `"0%"`; `last_played` does 20 sequential calls to a broken path | Use `GET /api/me/items-in-progress` for metadata plus ONE companion `GET /api/me/progress` joined by `libraryItemId` (items-in-progress returns only `progressLastUpdate`, no percent — verified ABS source 2026-09-27; a literal single call cannot supply percent). No per-item calls remain | pytest: fixed upstream call count regardless of item count; progress value passed through |
| BUG-14 | P1 | `handlers/audiobookshelf.py:80-107`, `abs_client.py:137` | Search pulls every item of every library (up to 51×500) | Use server-side `GET /api/libraries/{id}/search?q=&limit=` per library, in parallel with `asyncio.gather`; author-chip re-searches get their books via `GET /api/authors/:id?include=items` (never an `/items` listing — verified ABS source 2026-09-27: book search matches title/subtitle only, so author-name queries need the authors endpoint) | pytest: no `/items` listing calls during search; author query returns their books |
| BUG-15 | P1 | `gateway/main.py` ABS endpoints (`/last-played`, `/library/{id}`, `/libraries`) | Empty list is treated as "ABS unavailable" (`if detail.get("books")`/`detail.get("libraries")`) | Check `"books" in detail` / `"libraries" in detail` instead (all three sites — the libraries endpoint had the same class of bug) | pytest: empty library → `{status: SUCCESS, books: []}`; genuine execution failure still reports the notice |
| BUG-16 | P1 | `gateway/main.py` `/api/media/audiobookshelf/status` | No auth; `abs_url` unbound on non-200; pings `/api/books` (not a real route); uses global settings | Require auth. Use the user's creds. Ping `GET /ping` **(VERIFIED 2026-09-27: ABS `server.js:385` root router, pre-auth)**. Initialize variables | pytest for all three branches (auth 401, AVAILABLE via user creds `/ping`, unconfigured, ping error code, connection error) |
| BUG-17 | P1 | `handlers/mass_client.py:199` | `search` swallows all exceptions → `[]`, so the HA fallback at `execution/main.py:2449` never runs; `artist`/`album`/`library_only` ignored | Raise a typed `MASearchError` (connection errors, timeouts, WS close, MA `error_code`); the caller falls back; pass `artist`/`album`/`library_only` through (`library_only` → `config.providers=["library"]` else MA defaults; `artist`/`album` casefold substring post-filter — documented in upstream notes) | pytest: direct search raises → fallback invoked; empty direct result stays authoritative |
| BUG-18 | P1 | `handlers/mass_ha_client.py:185-218` | MA browse via HA reads top-level keys (HA wraps them in `service_response`), omits `config_entry_id`, uses `"TRACKS"` (enum verified 2026-09-27: HA core `music_assistant/services.yaml` accepts lowercase singular `artist\|album\|audiobook\|playlist\|podcast\|track\|radio` and requires `config_entry_id`; response is `service_response.items`) | Unwrap `service_response`; pass `config_entry_id`; lowercase singular media types | pytest with a recorded HA response fixture |
| BUG-19 | P1 | `gateway/main.py:10120` MA stream GET (+ new POST `:10017`) | Every GET (including Range/seek requests) re-ran `play_media(option=replace)`, restarting the queue. UI "responseURL trick" helpers in `api.ts` turned out to be dead code (zero callers) | **DONE 2026-09-27**: `POST /api/media/stream/music-assistant` starts playback (play_media + 15s stream-URL discovery, 2h cache); GET serves bytes only and 409s on a cold start (never `play_media`); deleted the dead `responseURL` helpers from `api.ts` | pytest: two Range GETs → `play_media` called 0 times (`test_ma_stream_start_split.py`); gateway suite 467 passed |
| BUG-20 | P1 | `gateway/main.py:9001-9046` `_forward_execution_request` (retry at `:9030`→`retry_http_request` `:673`) | Non-idempotent commands (`play`, `next`, `volume_up`) are retried on `ClientError`, so they can run twice | **DONE 2026-09-27**: only known-idempotent ops retry — transport `pause`/`volume_set`/`seek` + read endpoints (`status`, `state/sync`, `entity/search`); everything else (incl. `ha_service`, ABS actions) gets exactly one attempt (`_execution_request_is_idempotent` `main.py:9014`) | pytest: `next` with a failing first attempt → called once (`test_execution_proxy_retry.py`, 5 tests); gateway suite 472 passed |
| BUG-21 | P1 | `gateway/main.py:8722` `get_ma_playlists`, `:8748` `get_ma_recent`, `:8773` `get_ma_browse` | MA playlists/recent/browse don't catch `ClientError` → 500 | **DONE 2026-09-27**: wrap the upstream GET in `try/except (TimeoutError, aiohttp.ClientError)` → `JSONResponse(502, {"status":"ERROR","error":str(e)})` (empty-success path unchanged for non-200) | pytest: each endpoint with upstream `ClientError` → 502 + `status:ERROR` (`test_ma_upstream_client_error.py`, 3 tests); gateway suite 475 passed |
| BUG-22 | P1 | `gateway/main.py:9985-10058` `_ma_proxy_bytes` (`async def` `:9959`; `except HTTPException: raise` skipped the close) | `proxy_client` session leaked on HTTPException | **DONE 2026-09-27**: `handed_to_generator` flag + `finally` closes the session on every exit except the StreamingResponse hand-off (generator's `finally` owns it); covers HTTPException, generic errors and cancellation | pytest: HTTPException path → `close()` called exactly once (`test_ma_proxy_client_close.py`); gateway suite 476 passed |
| BUG-23 | P1 | `ma_ws_client.py` `_message_loop` `:453`, ERROR branch `:531`, `_dispatch_event` `:595`, `_schedule_reconnect` `:712`, jitter `:740` (plan refs `444-455,501,525,552,670,680` stale) | Reconnect never started; ERROR frames don't resolve futures (full-timeout waits); a player event overwrites queue state; jitter always 0; no `queue_id` filter | **DONE 2026-09-27**: message-loop exit arms `_schedule_reconnect()` (shutdown/dup guarded, any close code); ERROR + no-type error frames `_fail_pending_response` → immediate `RuntimeError` (also record `_ma_error_code`); `player_updated` no longer replaces `_queue_state`; `jitter = random.uniform(0, delay * RECONNECT_JITTER)`; `_queue_id` adopted from ingest / first event / command args, foreign `queue_id` events ignored (`_event_matches_tracked_queue`) | pytest: 6 tests in `test_ma_ws_client.py::TestBug23Hardening` (one per sub-bug + command tracking); gateway suite 482 passed |
| BUG-24 | P1 | `media_playback_registry.py:102` `save_playback_state` read-then-write; `media_playback_service.py:30,91,163-165` (same refs now) | Read-then-write race; saves "playing" before success; local aliases differ between status and play; `None.lower()` crash | **DONE 2026-09-27**: single `INSERT … ON CONFLICT(username) DO UPDATE` (`registry.py:140`, only caller-provided columns overwrite, `updated_at` always); hardware `play` persists state only after handler `SUCCESS` (`service.py:98`); one `LOCAL_PLAYER_ALIASES = {local,web_player,browser,android}` (`service.py:19`) used at play `:35`, transport `:121`, status `:171` + `main.py:719,763`; `None` entity_id guarded in status/transport | pytest: 5 new tests in `test_media_playback_service.py` (`bug24_*`: stale-read upsert, no-save-on-failure + save-on-success, browser/android alias status, NULL entity_id status+transport) — all red-first, then green |
| BUG-25 | P1 | `handlers/media.py:240,280,179` | Failed music search plays a **random track**; `enqueue="replace"` is mapped to `play` | **DONE 2026-09-27**: empty/failed MASS search → `FAILURE "No match for …"` (random `get_library` + last-resort raw-query `play_media` deleted); `req.enqueue` passes through to `music_assistant.play_media` unchanged (HA service accepts `play\|replace\|next\|replace_next\|add` — verified) | pytest: `test_media_music_no_random_fallback.py` (5) + plan-driven `test_execution_main.py` update; execution suite 389 passed |
| BUG-26 | P1 | `handlers/media.py:457,546` | Podcast play used the podcast id, not an episode id; the Nextcloud fallback read `ctx.user_context` (AttributeError, swallowed) | **DONE 2026-09-28**: live ABS 2.x removed `/api/items/:id/stream` (404 live), so `play_podcast`'s ABS branch now resolves a real episode — expand the podcast item (`GET /api/items/{id}?expanded=1` → `media.episodes`), pick the requested one (case-insensitive title match) else the latest `publishedAt` — and starts a session (`POST /api/items/{podcast}/play/{episode}`); the device gets an mt-token-signed gateway HLS URL (new `play_podcast_episode` ABS action, `abs_client.get_session_track_url` → gateway `/api/media/stream/abs-session/{sid}/0` + `/{segment}`, m3u8 segment URIs rewritten to the gateway route — ABS host never reaches the device; §7.4). Nextcloud fallback now passes `ctx`. Book play was converted in **BUG-33** | pytest: `test_media_podcast_episode.py` (10, red-first) + gateway `test_abs_session_stream.py` (8); execution 429 passed, gateway 520 passed |
| BUG-27 | P2 | `ha_client.py` `resolve_entity_by_name` | Bonuses for TV/MA outrank the name match | **DONE 2026-10-05**: scoring split into a name-match score and a capability bonus; candidates sort by `(name_score, bonus)` so a well-named entity always beats an unrelated capable one and bonuses only break ties (capable-only fallback preserved when nothing matches by name) | pytest: `test_ha_client.py::test_resolve_name_match_dominates_capability_bonuses` (5 params — TV/MA/video bonuses, tie-break, fallback); execution 583 passed, 10 pre-existing failures unchanged |
| BUG-28 | P2 | `handlers/media.py:659-724`, `android_tv.py`, `roku.py` | TV transport misrouted; missing command maps; wrong bitmask (8424 lacks PLAY_MEDIA=512) | **DONE 2026-10-05**: `handle_media_transport` prefers HA `media_player.*` whenever the entity advertises the command's `supported_features` bit and only falls back to the brand remote handlers otherwise; Android TV keys are the verified uppercase `remote.send_command` set (MEDIA_PLAY, DPAD_CENTER, POWER…) and Roku keys the verified lowercase set (play, forward, volume_mute, power…), with unmappable commands failing fast (no raw fallback); `_find_cast_sibling` play_media mask corrected 8424→512 | pytest: `test_media_transport_routing.py` (9 cases); execution 592 passed, same 10 pre-existing failures |
| BUG-29 | P2 | `execution/main.py:2697` `/execute/groups/media` (+ the `lights`/`patterns` siblings) | Untyped param meant FastAPI read the body as a required query string (every call 422/500); `from schemas_groups import …` inside each handler is a bare import that fails under uvicorn `PYTHONPATH=/app` (`/execute/groups/*` 500 — docs/code_review_2026-08-22.md E3); the gateway-injected `user_context` was dropped, so `owner_user_id` was always `""` | **DONE 2026-10-05**: all three routes take their real Pydantic models (`MediaGroupRequest`, `LightClusterRequest`, `LightPatternRequest`) as typed body params, imported top-level; both group models accept optional `user_context` (empty-context fallback preserved) so creates record the acting user; invalid actions now 422 | pytest: `test_groups_routes.py` (6 cases: list routing, acting user, empty fallback, 422 bad action, lights owner, patterns) |
| BUG-30 | P2 | `tool_registry.py` `_RAVEN_TOOL_TABLE` | Tool payload hints don't match the schemas → 422s | **DONE 2026-10-05**: all execution hints realigned to `execution/schemas.py` (e.g. `brightness_pct`/`color_temp`/`rgb_color`, `command` not `action`, `duration_str`/`time_str` not `duration`, `container_name` not `container`, `file_path` alias for workspace paths, `password` dropped from identity hints); the three model-less execution entries (`StorageListRequest`, `STTRequest`, `AiCapabilitiesRequest` — raw-JSON/bodyless handlers) and the seven non-execution-service entries carry explicit allowlist entries; each hint's first sentence after `payload fields:` is now a clean field list (prose moved after the period) | pytest: `test_raven_payload_hints.py` (54 lockstep cases: every hinted key is a model field/alias, every required field is hinted, allowlists can't silently grow); gateway 899 passed, 2 skipped |
| BUG-31 | P2 | `execution/main.py:2261` `_resolve_mass_ha_creds`; `media_status.py:15` | Uncached HA config-entry lookups and a full `/api/states` read on every 3s poll | TTL cache (60s) keyed by user; status comes from the event hub cache (§7.2) | pytest: 2 calls → 1 upstream |
| BUG-32 | P2 | `execution/main.py:495` `TEMP_AUDIO_CACHE`, video downloads | Never evicted | LRU with max entries/bytes; delete videos older than 24h on a periodic task | pytest |
| BUG-33 | P1 | `handlers/audiobookshelf.py` `_handle_play`/`_handle_resume`, `abs_client.get_stream_url`, `gateway/main.py` `stream_audiobookshelf` | After BUG-26, **book** play and resume still used the legacy `abs_client.get_stream_url` → `GET /api/media/stream/audiobookshelf/{id}` route, whose upstream `/api/items/:id/stream?format=mp4&token=<key>` is **404 on live ABS 2.x** — book playback was dead in production. The first live probe of a book session showed no playlist, which looked like a broken transcode pipeline | **DONE 2026-09-28**: books now start a session like podcasts (`POST /api/items/{bookId}/play`) and stream the gateway HLS route; the track index comes from the expanded item's `media.tracks[0].index` (1-based for books) and a missing/invalid track fails loudly. The live probe showed the missing playlist was a **cold-transcode race** (404 until ffmpeg probed the source; 5291 × 6s segments, warm sessions ready in 0.2s), so `stream_abs_session` now takes the 302 `Location` without following it, polls the playlist (`ABS_PLAYLIST_POLL_INTERVAL`=2s, `ABS_PLAYLIST_MAX_ATTEMPTS`=45, `ABS_PLAYLIST_READY_TIMEOUT`=90) and answers **504** naming the session if it never appears (a root-relative `/hls/...` Location is joined to the *origin*, not the configured base path). The dead `abs_client.get_stream_url` and the gateway route were deleted (they were the only place an ABS key went into an upstream query string) | pytest: `test_media_book_session.py` (9, red-first) + `test_abs_session_stream.py::TestAbsSessionPlaylistReadiness` (3); execution 459 passed, gateway 536 passed |

### 3.3 Frontend: bugs (Phase 3)
Paths are relative to `services/ui/src/`.

| ID | Sev | Where | Problem | Fix / required test |
|---|---|---|---|---|
| BUG-40 | P1 | `pages/Media.tsx:1310-1318`, `lib/maWebPlayer.ts:616-644` | **Leaving /media kills web-player audio** | Move the player to an app-level provider (Phase 4). E2E: play → navigate to `/` → the audio element is still playing and the mini-player is visible |
| BUG-41 | P1 | `Media.tsx:288-293` `formatTime` | No hours: a 10h audiobook shows `605:12` | Shared `formatDuration()` → `H:MM:SS` when ≥1h. Unit test: 0, 59, 3600, 36012, NaN |
| BUG-42 | P1 | `Media.tsx:705-738,814,852,877,949` | Explorer spinner can never show: async calls are not awaited (the `finally` clears immediately) and the keys never match (`ma-${uri}` vs `uri`) | Await the promise; one `itemKey()` helper. Component test: click → spinner visible until the promise resolves |
| BUG-43 | P1 | `Media.tsx:992-993` | ABS **podcast** passed as `'playlist'` → plays a raw podcast id via MA (broken) | Podcasts open the Podcast detail view (episode list); play an episode. Component test |
| BUG-44 | P1 | `Media.tsx:1428` | `availablePlayers.find(...)?.name`, but `MediaStatus` has `friendly_name`, not `name` | Use `friendly_name`. Unit test on the label selector |
| BUG-45 | P1 | `Media.tsx:1329-1334` | Remote position ignores `pos===0` while playing, so a new track keeps counting from the old position | Reset when `media_content_id` or title changes. Extrapolate with `media_position_updated_at` (backend adds this field, §7.1). Unit test |
| BUG-46 | P1 | `maWebPlayer.ts:402,492,568-590` | connect/play race → "JSON-RPC WebSocket not connected" | `connect()` returns a single shared promise; `play()` awaits it. Unit test with fake sockets |
| BUG-47 | P1 | `maWebPlayer.ts:509-513,613` | Stale closure resets volume to 70 on reconnect | Read from a ref/store. Unit test |
| BUG-48 | P1 | `maWebPlayer.ts:372-374` | JSON-RPC timeout leaks listeners; pending calls are never rejected on close | Pending map keyed by `message_id`; reject all on close. Unit test |
| BUG-49 | P1 | `maWebPlayer.ts:437-440,498-504,602-612,1019-1027` | `<audio>` element leaked on every retry/disconnect; Sendspin WS left open on failure | Exactly one audio element, owned by the engine. Unit test: 3 connect/disconnect cycles → 1 `<audio>` in the DOM |
| BUG-50 | P1 | `maWebPlayer.ts:521-561` | No auto-reconnect; `connectionState` only ever becomes CONNECTED | Exponential backoff reconnect (1s, 2s, 4s … max 30s, with jitter) that drives `CONNECTING/CONNECTED/RECONNECTING/FAILED`. Unit test with fake timers |
| BUG-51 | P1 | `maWebPlayer.ts:285-288,313-314,291` | Previous track's art/title sticks; favorite never applied (`null` vs `undefined`) | Replace metadata wholesale on a track change. Unit tests on the event reducer |
| BUG-52 | P1 | `maWebPlayer.ts:262,268` | Player-id filter uses `includes` both ways | Strict equality. Unit test |
| BUG-53 | P1 | `maWebPlayer.ts:726,744,762,787,804` | Transport sends with `expectResult=false` → MA errors are swallowed | Await results; surface a toast. Unit test |
| BUG-54 | P1 | `ActiveMediaWidget.tsx:129-145,246-252,55-58` | Seek fires a request on every pointermove; volume on every step; position never resets | Widget uses the shared store and `<Scrubber>`/`<VolumeSlider>` (commit on release, debounce 150ms) |
| BUG-55 | P1 | `android/.../widgets/MediaWidget.java:39-61` | Parses `detail.player`, but the API returns `detail.active` | Parse `detail.active`. Manual device check + a JUnit test if the Android test harness exists (it probably doesn't, so use the manual checklist) |
| BUG-56 | P1 | `maWebPlayer.ts:116-127,1062-1098` | Lock-screen artwork uses the raw MA path (broken); no `setPositionState`/seek/stop handlers; stale session after unmount | Full MediaSession (§6.7). Unit test with a mocked `navigator.mediaSession` |
| BUG-57 | P2 | `Media.tsx:1151-1163` | Web player auto-connects on every page visit (it registers an MA player speculatively) | Connect lazily: first time the Web Player is chosen as output, or when restoring a session that was playing |
| BUG-58 | P2 | `Media.tsx:1839` | Side effect inside a state updater (`setLocalVolume(prev => { setLocalIsPlaying(true) … })`) | Removed by the store rewrite |
| BUG-59 | P2 | `Media.tsx:2237-2239,2278` | Every card shows a spinner whenever *anything* loads; `isDisabled` always false | Per-item pending state from the store's `pendingAction` |
| BUG-60 | P2 | `Media.tsx:2250-2255` | Playlists "Refresh" shows a spinning icon when *not* loading | Proper error state with a Retry button |
| BUG-61 | P2 | `Media.tsx:749-751` modal; `components/ui/Modal.tsx` | No `role=dialog`, no Esc, no focus trap, no scroll lock; `92vh` breaks under the mobile URL bar | New `<Sheet>` primitive (§6.4) using `dvh` |
| BUG-62 | P2 | `Media.tsx:681-687` | Search fires per keystroke with `retry: 2` | Use the existing `hooks/useDebounce.ts` (250ms), `retry: 1`, `placeholderData: keepPreviousData` |
| BUG-63 | P2 | `Media.tsx:689,1393` | The same query key `['ma-playlists']` with different options | Central query-key factory + options (§6.3) |
| BUG-64 | P2 | `Media.tsx:300`, `maWebPlayer.ts:99,111` | API token in `<img>` and WS URLs | Signed short-lived media token (§7.4) for img/stream; WS auth via the first message or `Sec-WebSocket-Protocol` (§7.4) |
| BUG-65 | P2 | device list | The same speaker appears twice (HA entity from the MA integration + `ma:` player) | Dedupe by the MA player id ↔ HA entity mapping (`services/shared/ma_player.py`). Unit test |
| BUG-66 | P2 | `lib/webPlayer.ts` | Multi-tab coordination is dead code; `destroy()` permanently nulls the BroadcastChannel handler | Rewrite as `features/media/engine/tabLock.ts` (§6.6) |
| BUG-67 | P2 | `main.tsx:43` | `CapacitorApp.removeAllListeners()` removes everyone's listeners | Keep handles; remove only its own |
| BUG-68 | P2 | `ActiveMediaWidget.tsx`, `LocalAudioPlayer.tsx`, `Modal.tsx` | Missing aria-labels, keyboard support, and touch targets under 44px | Covered by the new components + axe tests |
| BUG-69 | P2 | Hard-coded `cyan/purple/pink/amber` classes | Media ignores the active theme | Use `var(--site-*)` tokens + dynamic artwork accent (§6.5) |
| BUG-70 | P2 | `maWebPlayer.ts` (256, 471, …) and `Media.tsx` | Dozens of `console.log` calls on hot paths | A `mediaLog` helper gated by `localStorage['jarvis_debug_media']==='1'` |
| BUG-71 | P2 | `vite.config.ts:40-46` | The `ma-stream-test` debug harness ships in the production build | Remove it from `rollupOptions.input`; delete `src/ma-stream-test.ts` and `e2e/ma-stream.spec.ts` once the new tests cover streaming |
| BUG-72 | P2 | `services/ui/debug-media*.mjs` | Hard-coded LAN IP + credentials; write to `/tmp` | Delete them |
| BUG-73 | P1 | `e2e/web-player-sendspin.spec.ts:15-36` | Throws at module load when env vars are missing, which breaks collection of the **whole** Playwright suite | Replace with `test.skip(!process.env.X, …)` inside `describe`; move it to the `@live` project (§9.3) |

### 3.4 UI/UX issue ledger (every item must be resolved; none is optional)
These are the design and usability problems in the current UI: `pages/Media.tsx`, `ActiveMediaWidget.tsx`, and the explorer modal. "Resolved by" points to the section or task that fixes it. "Verify" is how you prove it. **P6-T5** (below) is a dedicated pass that walks this whole table.

| ID | Current problem (where) | Resolved by | Verify |
|---|---|---|---|
| UX-01 | The device selector is the first thing on the page and dominates it. It's a horizontal pill strip titled "Select Device" (Media.tsx:120-221) | Output chip in the header + Output sheet (§4.2) | Visual baseline; E2E output.spec |
| UX-02 | The Web Player tile always shows a **green "online" dot**, even when disconnected or failed (Media.tsx:141) | Output rows show the real `connection.web` state (connecting/connected/reconnecting/failed) | Component test for each state |
| UX-03 | Jargon for users: "Browser / Android App", "(HA/MA Device)", "Connected to MA", "Music Assistant" as a tab name, "Identity service" (Media.tsx:144,174,542,568,770,2206) | Copy rules (§3.5): plain language only ("This device", "Kitchen Speaker", "Music", "Audiobooks") | `grep -rn "HA/MA\|Identity service\|Music Assistant" src/features/media` finds only settings links |
| UX-04 | Broken copy: "Web Player Connection Failed — " ends with a dangling dash (Media.tsx:544) | StateViews error copy + Retry button | Component test |
| UX-05 | A "Select a device above to enable playback" warning shows even though the Web Player is a valid default (Media.tsx:557-563) | There is always an active output (defaults to this device), so the message is removed | Component test: no warning on first load |
| UX-06 | The empty state shows a big **Play icon that isn't a button** ("No Active Playback"). It looks clickable and does nothing (Media.tsx:399-403,431) | Nothing loaded → no mini-player. Listen Now shelves are the call to action | Visual baseline |
| UX-07 | Cover art is tiny (64–80px) with no hero moment (Media.tsx:393) | Now Playing sheet with large art + blurred backdrop (§4.2) | Visual baseline |
| UX-08 | Mobile layout stacks art → text → transport → volume with borders between them; controls are not thumb-reachable | Mini-player (bottom) + Now Playing with controls in the lower third (§4.2) | Mobile visual baseline |
| UX-09 | A red **Stop** square sits next to Next. It looks destructive, is rarely needed, and is easy to mis-tap (Media.tsx:453-458) | Stop moves to the Now Playing "…" menu; mini-player has only play/pause + next | Component test |
| UX-10 | The volume readout shows "M" when muted, in monospace (Media.tsx:473) | `VolumeSlider`: icon state + `aria-valuetext`; no cryptic letters | Component test |
| UX-11 | The desktop scrubber thumb is invisible until hover. Keyboard seek uses a stale `currentTime` and 5s steps with no Shift modifier (Media.tsx:492-516) | Radix `Scrubber`: visible thumb on focus/hover, time bubble while dragging, ←/→ 5s, Shift 30s | Component test + shortcuts.spec |
| UX-12 | A "LIVE" badge shows whenever duration ≤ 0, including while a normal track is still loading (Media.tsx:526-535) | `isLive` comes from the item kind (radio/stream), not from duration; loading shows a skeleton scrubber | Reducer test |
| UX-13 | No shuffle, repeat, queue view, play-next, or add-to-queue anywhere | §4.2 Now Playing, Queue sheet, ItemContextMenu | queue.spec, journeys 4/5 |
| UX-14 | "Jump Back In" shows **only 3** items, icon-only (no artwork). Its Refresh button shows only *while loading* (Media.tsx:2194-2201,1463) | "Continue listening" + "Recently played" shelves with artwork and See all | Component tests |
| UX-15 | Playlists and all lists are text rows with a generic icon and no artwork (Media.tsx:616-636) | `MediaCard` / `TrackRow` with `Artwork` | Visual baseline |
| UX-16 | Setup hints are jargon with **no link** to where to fix it (Media.tsx:2206,2260) | Empty state CTA button → `/settings` identity section (confirm the route in `App.tsx`) | Component test clicks the CTA |
| UX-17 | "Browse All Media" hides the real library behind a modal that duplicates the home page | Real routes: Library, Search, and detail pages (§4.1); modal removed | E2E journeys 3/4 |
| UX-18 | You can't browse by artist or album; the ABS library is capped at 50 with no paging (Media.tsx:667) | Library tabs + infinite scroll + detail pages | LibraryPage tests |
| UX-19 | Search result count reads "(0)" while loading (Media.tsx:803,967) | Skeleton rows while loading; no count until results | Component test |
| UX-20 | Author results are **disabled** when no device is picked, although tapping one only runs a search (Media.tsx:1007) | Author opens a search/author view; it's never disabled | Component test |
| UX-21 | Errors show as a red text banner at the top with raw technical messages, mixed with toasts (Media.tsx:2124-2129) | One pattern: inline `ErrorRetry` for data, `toast.error` for failed actions, both with human copy naming the service | Component tests; grep that there's no top banner |
| UX-22 | Playing an item from the explorer gives **no feedback**: the modal stays open, no spinner (BUG-42), no confirmation | Tapping an item shows pending on that item, then the mini-player updates, with a polite live-region announcement | Journey 1 |
| UX-23 | Items are disabled with **no explanation** when there's no target (Media.tsx:812,852,875) | Never disabled; there's always an output | Component test |
| UX-24 | Play affordances are **hover-only** (`opacity-0 group-hover:opacity-100`), so they're invisible on touch (Media.tsx:612,634,824…) | The whole card is the play target; a visible play button overlay on the art for touch (`@media (hover: none)`) | Mobile visual baseline |
| UX-25 | Weak hierarchy: a plain `h1 Media`, uppercase 10–12px section labels everywhere | Typography scale (§6.5): page title, shelf titles 18–20px semibold, sentence case | Visual baseline |
| UX-26 | Emoji in the UI ("🎧 Browser Audio") (Media.tsx:125) | Removed; lucide icons only | grep for emoji in `features/media` |
| UX-27 | A constant `animate-pulse` on the Web Player tile and banners distracts and ignores reduced-motion (Media.tsx:138,540) | Motion only on state change; honor reduced motion (§4.4) | Reduced-motion E2E |
| UX-28 | The same Music icon is used for tracks, albums, artists, and playlists; type is only a tiny pill | Artwork + kind-specific fallback icons + round art for artists | Visual baseline |
| UX-29 | **Contrast failures:** `text-slate-500/600` at 10px on near-black (Media.tsx:125,144,520, etc.) | Minimum 12px text; muted text uses `--site-text-muted`, checked ≥4.5:1 | axe color-contrast rule (not disabled) |
| UX-30 | Choosing a device only changes which device the controls drive. It doesn't move the music, and nothing says so | Output sheet offers "Play here" (transfer) vs "Control" for a busy device, with clear labels | output.spec |
| UX-31 | The heart shows only while playing and is hard-coded red (off-theme) (Media.tsx:417-427) | Heart on the mini-player, Now Playing, rows, and context menu; uses `--media-accent` | Component tests |
| UX-32 | The dashboard `ActiveMediaWidget` looks and behaves differently from the page (pink gradients, no labels) | P5-T9 rebuild on shared components | Visual baseline for the widget |
| UX-33 | Spinners instead of skeletons cause layout shift; no pull-to-refresh on mobile | `.skeleton` tiles at the exact card size; pull-to-refresh on Listen Now (mobile only; invalidates `mediaKeys.all`) | E2E `slow` scenario: no CLS (via a `PerformanceObserver` 'layout-shift' total < 0.05) |
| UX-34 | The mobile modal can't be swiped to close; `92vh` gets hidden under the browser bars | vaul Sheet with drag-to-dismiss, `dvh` units | E2E mobile |
| UX-35 | Desktop wastes space: a single `max-w-4xl` column | Responsive grid: shelves widen to 6 columns, desktop dock player, and at ≥1440px an optional right-side queue panel that stays open | Desktop visual baseline |
| UX-36 | Unknown times render as "0:00" | Render "--:--" when unknown | format test |
| UX-37 | Device chips don't show **what each device is playing**, only an online dot | "Playing in your home" strip + Output rows show the current title | Journey 2 |
| UX-38 | The remote volume slider snaps back because of the poll, and fires a request per step | Optimistic volume, 150ms debounce, 1.5s grace (§6.3) | Store tests |
| UX-39 | `heavy` haptics on every play feel harsh | Haptics map (§4.4) | Unit test on the haptic calls |
| UX-40 | A first-run user with nothing configured sees three empty sections stacked | A single onboarding empty state for Listen Now: "Connect your music" with the CTA | Component test (`empty-library` + not-configured scenario) |
| UX-41 | Remote playback UI lags up to 3s behind real state after an action | Optimistic state + event stream (≤1s) | Journey 2/6 timing assertion (`expect.poll` within 1500ms) |
| UX-42 | The explorer resets its tab and search every time it closes, so context is lost | Routes preserve state; search query in the URL (`?q=`) | E2E back/forward keeps the query |
| UX-43 | The ABS header shows "Loading..." forever when `status` is missing (Media.tsx:936) | StateViews driven by query state, not payload fields | Component test |
| UX-44 | Audiobook lists have no cover, duration, or progress | `MediaCard` with `ProgressRing` + "2h 13m left" | Component test |
| UX-45 | **"Reconnecting…" toast spam**: status polling goes through the axios retry interceptor with toasts (api.ts:242-296) | Background requests (polling, the event-stream fallback, heartbeats) pass an axios config flag `silent: true` that skips the retry toast. Show the single offline banner (§4.5) instead | Vitest on the interceptor; E2E `offline` sees 0 toasts |
| UX-46 | Failures are labelled "Jarvis server" because `describeFailedTarget` doesn't know `/execute/media/*` (api.ts:230) | Add media prefixes → "Media service" | Unit test |
| UX-47 | BottomNav already has 7–8 cramped items. The mini-player must not make the bottom of the screen claustrophobic | Mini-player floats with an 8px gap and rounded corners, auto-hides on scroll down in long lists and reappears on scroll up, and never overlaps the FAB/toasts (z-scale §6.4) | Mobile visual baseline + scroll E2E |
| UX-48 | Landscape phones and tablets are not considered | Now Playing switches to a two-column layout (art left, controls right) when `aspect-ratio > 1` and height < 500px | Visual baseline at 844×390 |
| UX-49 | No visible focus styles on custom buttons | Global `:focus-visible` ring using `--site-accent-focus` in `features/media` | Keyboard E2E + visual |
| UX-50 | Inconsistent naming ("Jump Back In", "Browse All Media", "Select Device", "Explorer") | Copy table (§3.5) | grep |
| UX-51 | No "resume where I left off" after an app restart; the page forgets the last output and item | Persisted slice (§6.3) → mini-player restores in a paused state with the last item | E2E reload |
| UX-52 | The web player's album name is never shown (`displayAlbum` is undefined for web; Media.tsx:305) | Store `item.album` for every output | Reducer test |
| UX-53 | Favorite, seek, volume, and mute failures on remote players are silently ignored (`catch { /* ignore */ }`, Media.tsx:2076,2080,2094,2098) | Rollback + toast (§6.3 rules) | Store tests with a failing adapter |

### 3.5 Copy guide (UX-03/50)

| Old | New |
|---|---|
| Select Device / Web Player / Browser Audio | **Playing on** · **This device** |
| HA/MA Device, Music Assistant (tab) | *(room/device name)* · **Music** |
| Jump Back In | **Continue listening** / **Recently played** |
| Browse All Media | **Library** |
| No Active Playback | *(no mini-player; Listen Now is the call to action)* |
| Requires … Identity service | **Connect your music services** → [Open settings] |
| Web Player Connection Failed — | **Couldn't connect this device.** [Try again] |
| Failed to load playlists. Check your server connection. | **Couldn't load playlists from your music server.** [Retry] |

Sentence case everywhere. No emoji. No product or protocol names in primary UI (settings screens may use them).

---

## 4. PRODUCT VISION: what "award-winning" means here

### Principles

1. **Music never stops because of the UI.** Navigation, rotation, backgrounding, and reconnects are invisible.
2. **One tap to sound.** From opening the app to audio in ≤ 2 taps (Home → a "Jump back in" card).
3. **The room is the interface.** You always know *where* audio is playing and can move it anywhere (like Spotify Connect / AirPlay).
4. **Artwork-first, calm chrome.** Big art, dynamic color, glass surfaces, generous spacing, no walls of pills.
5. **Instant feedback.** Every control reacts in <100ms (optimistic), reconciles with the server, and rolls back with a toast on failure.
6. **Accessible by default.** WCAG 2.2 AA, full keyboard, screen-reader labels, reduced-motion support, 44×44px targets.

### 4.1 Information architecture (routes)

| Route | Screen |
|---|---|
| `/media` | **Listen Now** (home) |
| `/media/search?q=` | Unified search |
| `/media/library/:tab` | Library; `tab` ∈ `playlists \| artists \| albums \| tracks \| audiobooks \| podcasts \| radio \| favorites` |
| `/media/album/:provider/:id` | Album detail |
| `/media/artist/:provider/:id` | Artist detail |
| `/media/playlist/:provider/:id` | Playlist detail |
| `/media/book/:id` | Audiobook detail (chapters, progress, resume) |
| `/media/podcast/:id` | Podcast detail (episodes, per-episode progress) |
| (overlay, any route) | **Now Playing** full-screen sheet: open with `?np=1` so the Android back button closes it |
| (overlay, any route) | **Queue** sheet, **Output (device) picker** sheet |

All detail routes are deep-linkable, and the browser/Android back button works.

### 4.2 Screens: exact content
**Listen Now (`/media`)**, top to bottom:
1. Header row: title "Listen Now", search field (desktop inline; mobile icon that routes to `/media/search`), output chip showing the current output ("Kitchen Speaker ▾") that opens the Output picker.
2. **"Playing in your home"** strip: one card per player currently `playing`/`paused` (any room). Each card shows art, title, room name, and a tiny play/pause. Tap = make it the controlled player. Hidden when none.
3. **Continue listening**: horizontal shelf of audiobooks + podcast episodes in progress, each with a **progress ring** on the artwork and "2h 13m left".
4. **Recently played**: artwork grid (2 columns mobile, 4–6 desktop). Tap = play.
5. **Your playlists**: horizontal shelf.
6. **Favorites**: shelf (tracks/albums).
7. **Radio**: shelf (if MA has radio items).
Each shelf has a "See all" → the matching Library tab. Loading = `.skeleton` tiles with the exact card dimensions (no layout shift). Error = inline card with a message + Retry. Empty = friendly illustration-free message + CTA ("Connect Music Assistant in Settings → Identity").

#### Mini-player (global, every route, when anything is loaded)
- Mobile: floats **above BottomNav** (bottom offset = BottomNav height + safe area + 8px), height 64px, rounded `--radius-card`, glass with an artwork-tinted background. Content: 44px art, title/artist (marquee on overflow is **not** allowed; use truncation), play/pause, next. A 2px progress line along the bottom edge.
- Desktop (≥1024px): a full-width **bottom dock** (72px) inside the main column. Left: art + title + artist + heart. Center: prev / play / next + scrubber with times. Right: queue button, output button, volume slider.
- Gestures (mobile): tap = open Now Playing; swipe up = open Now Playing; swipe left/right on the text = next/previous (with haptic `light`).
- Page content must get bottom padding so the mini-player never covers content.

#### Now Playing (full-screen sheet)
- Background: artwork blurred (`filter: blur(60px) saturate(1.4)`) + a dark scrim; the accent color is extracted from the art (§6.5).
- Large square art (max 420px desktop / `min(86vw, 52dvh)` mobile) with a shared-element transition from the mini-player art (framer-motion `layoutId="np-art"`).
- Title (large), artist (tap → artist page), album (tap → album page), heart.
- Scrubber (big hit area, a time bubble while dragging, commit on release). Elapsed / **remaining** (`-3:12`).
- Transport: shuffle, previous, **play/pause (72px)**, next, repeat (off/all/one). For audiobooks/podcasts, replace shuffle/repeat with **−15s / +30s** and show a **speed** button (0.75×, 1×, 1.25×, 1.5×, 1.75×, 2×) **(VERIFY MA supports playback speed for the target; if not, show speed only for the Web Player via `audio.playbackRate`)**.
- Bottom row: output chip (device name + icon), **sleep timer** (15/30/45/60 min, end of chapter/track), queue button, lyrics button (only if lyrics metadata exists **(VERIFY MA exposes lyrics)**).
- Volume slider (hidden on mobile native, where hardware keys control volume, but visible on mobile web).
- Audiobook: a chapter list button, and the current chapter name under the title.
- Swipe down / Esc / back button closes it.

**Queue sheet**: "Now playing" item, then "Up next" list with drag handles (reorder), swipe-left to remove (mobile) or ✕ (desktop), "Clear queue", "Play next" / "Add to queue" in item context menus everywhere. Virtualized if >100 items.

**Output picker sheet**: grouped sections: "This device" (Web Player), "Speakers", "TVs", "Groups". Each row: icon, name, room, state (playing title if busy), availability dot, per-row volume slider for the active/grouped ones, a **checkbox to add to group** (sync) for MA players **(VERIFY `players/cmd/group_many` / `players/cmd/ungroup`)**. A "Transfer" action moves the current queue to the chosen player **(VERIFY `player_queues/transfer`)**. Deduped (BUG-65). Unavailable players are shown dimmed with "Offline".

**Search (`/media/search`)**: autofocus input, debounced 250ms, results grouped **Top result** (big card), **Songs**, **Artists**, **Albums**, **Playlists**, **Audiobooks**, **Podcasts**, **Authors**, merging MA and ABS searches in parallel. A filter chip row limits to one type. Recent searches (last 10, localStorage with try/catch). Full keyboard: ↑/↓ moves, Enter plays/opens. Desktop `⌘K`/`Ctrl+K` and `/` focus search from any media route.

**Album / Playlist / Artist**: hero with large art + dynamic gradient, "Play" and "Shuffle" buttons, track list (number, title, artists, duration, heart, "…" menu: Play next, Add to queue, Go to artist/album, Add to playlist **(VERIFY playlist add API)**). Artist: top tracks, albums grid, singles.

**Audiobook detail**: cover, title, author, narrator, series, duration, progress bar + "Resume at 3:12:44", chapters list (tap = play from chapter), "Mark finished" **(VERIFY ABS API)**, description.

**Podcast detail**: cover, episodes list (newest first) with per-episode progress and a played state, "Play latest".

### 4.3 Desktop keyboard shortcuts (when focus is not in a text input)
`Space` play/pause · `←/→` seek −5/+5s (`Shift` = −30/+30) · `↑/↓` volume ±5 · `M` mute · `N` next · `P` previous · `S` shuffle · `R` repeat cycle · `Q` queue · `F` Now Playing · `/` search · `?` shortcut help dialog.

### 4.4 Motion and haptics
- framer-motion for sheets (spring `stiffness: 400, damping: 40`), the shared art transition, and list item enter (stagger 20ms, max 10 items).
- `prefers-reduced-motion: reduce` → no spring or blur transitions (fade only), no marquee, no parallax.
- Haptics via the existing `hooks/useHaptics.ts`: `light` for transport/toggles, `medium` for output change, `heavy` never (it's too strong for frequent actions).

### 4.5 States that must exist for every data view
`loading` (skeleton), `empty` (message + CTA), `error` (message + Retry, with the service named: "Music Assistant is unreachable"), `offline` (a banner when `navigator.onLine === false` or the event stream is disconnected >5s), `partial` (MA works, ABS down: show MA shelves plus a small inline notice for ABS).

---

## 5. STACK CHANGES (dependencies)

Add, pinned to latest stable at implementation time (check with `npm view <pkg> version`):

| Package | Why |
|---|---|
| `@tanstack/react-virtual` | Virtualized queue, library, and track lists |
| `@dnd-kit/core`, `@dnd-kit/sortable` | Accessible drag-to-reorder for the queue (keyboard support built in) |
| `@radix-ui/react-dialog`, `@radix-ui/react-slider`, `@radix-ui/react-dropdown-menu` | Accessible sheet/dialog (focus trap, Esc, aria), sliders (keyboard, aria-valuetext), context menus. Style them with our tokens |
| `vaul` | Mobile bottom-sheet with drag-to-dismiss (built on Radix Dialog) |
| `colorthief` (or `fast-average-color`) | Artwork accent extraction (§6.5) |
| dev: `msw` | API mocking for vitest component tests |
| dev: `@axe-core/playwright` | Automated a11y checks in E2E |
| dev: `mock-socket` | Fake WebSocket for maWebPlayer/engine unit tests (or hand-roll a `FakeWebSocket` in `src/test/fakes/`) |

Backend (Python): no new framework. Use existing `aiohttp` and FastAPI `StreamingResponse` for SSE. If `aioresponses` is not already in `requirements-test.txt`, add it for HTTP mocking.

Android: a new native `MediaSessionPlugin` (§6.8). No third-party Capacitor media plugin, because compatibility with Capacitor 8 is unverified.

Do **not** add Redux, styled-components, or another router. Keep zustand + react-query + Tailwind v4.

---

## 6. FRONTEND TARGET ARCHITECTURE

### 6.1 Core idea
```
<App>
  <QueryClientProvider>
    <AuthProvider>
      <MediaEngineProvider>         ← NEW: mounted ONCE, above <Routes>, survives navigation
        <AppShell>                  ← NEW: single persistent shell (Sidebar/Header or MobileShell)
          <Routes/>                 ← pages render inside the shell
          <MiniPlayer/>             ← global
          <NowPlayingSheet/> <QueueSheet/> <OutputSheet/>   ← global overlays
        </AppShell>
      </MediaEngineProvider>
```
- **`useMediaStore` (zustand)** is the ONLY source of truth for playback state. Components read with selectors. No component keeps its own copy of position, volume, and so on.
- **PlayerAdapter interface.** One implementation per backend kind. The UI calls `store.actions.play(item)`, and the store routes to the adapter for the current output. This removes the 3× duplication in `Media.tsx`.
- **Events, not polling.** The store is fed by (a) Web Player events from Sendspin/JSON-RPC and (b) the new server **media event stream** (§7.2) for all remote players. Polling remains only as a fallback (every 10s, and only while the event stream is disconnected).

### 6.2 File layout (create exactly this)
```
services/ui/src/features/media/
  index.ts                         # public exports
  types.ts                         # MediaItem, Player, QueueItem, PlaybackState, RepeatMode, OutputKind…
  format.ts                        # formatDuration, formatRemaining, formatTimeAgo, normalizePlayedAt (moved from Media.tsx)
  log.ts                           # mediaLog (BUG-70)
  api/
    keys.ts                        # react-query key factory
    queries.ts                     # useRecent, usePlaylists, useSearch, useAlbum, useArtist, useBook, usePodcast, useFavorites, useContinueListening…
    client.ts                      # thin typed wrappers around services/api.ts media methods (move media methods here; api.ts re-exports for back-compat)
  store/
    mediaStore.ts                  # zustand store (6.3)
    selectors.ts
  engine/
    MediaEngineProvider.tsx        # mounts adapters, event stream, MediaSession, keyboard shortcuts, tab lock
    adapters/
      PlayerAdapter.ts             # interface
      webPlayerAdapter.ts          # wraps SendspinEngine
      maPlayerAdapter.ts           # MA JSON-RPC commands for ma:<id>
      haPlayerAdapter.ts           # POST /execute/media/* for media_player.* entities
    sendspinEngine.ts              # extracted, fixed core of lib/maWebPlayer.ts (non-React class)
    maRpc.ts                       # JSON-RPC client: pending map, timeouts, reconnect (BUG-46/48/50)
    eventStream.ts                 # SSE client for /api/media/events (7.2)
    mediaSession.ts                # navigator.mediaSession + native plugin bridge (6.7/6.8)
    tabLock.ts                     # BroadcastChannel single-tab audio ownership (BUG-66)
    shortcuts.ts                   # keyboard shortcuts (4.3)
    artworkColor.ts                # accent extraction + cache (6.5)
  components/
    Artwork.tsx                    # image with proxy URL, lazy, fallback gradient, sizes
    Scrubber.tsx                   # Radix slider; drag preview; commit on release; aria-valuetext "1 minute 12 seconds of 3 minutes"
    VolumeSlider.tsx
    TransportControls.tsx
    MiniPlayer.tsx
    NowPlayingSheet.tsx
    QueueSheet.tsx
    OutputSheet.tsx
    MediaCard.tsx                  # artwork tile (grid/shelf)
    TrackRow.tsx                   # list row with context menu
    Shelf.tsx                      # horizontal scroller with snap + "See all"
    ProgressRing.tsx
    SleepTimerMenu.tsx
    ItemContextMenu.tsx
    StateViews.tsx                 # Skeleton / Empty / ErrorRetry / Partial
  pages/
    ListenNowPage.tsx
    SearchPage.tsx
    LibraryPage.tsx
    AlbumPage.tsx  ArtistPage.tsx  PlaylistPage.tsx  BookPage.tsx  PodcastPage.tsx
  __tests__/                       # vitest unit + component tests (mirrors the structure)
services/ui/src/components/ui/Sheet.tsx     # generic vaul/Radix sheet, reused by Media and later other features
services/ui/src/components/layout/AppShell.tsx
```
`pages/Media.tsx` becomes: `export { default } from '../features/media/pages/MediaRoutes'`, which holds the nested `<Routes>` for §4.1.
Delete when replaced: `components/LocalAudioPlayer.tsx`, `lib/webPlayer.ts`, `lib/maWebPlayer.ts` (after the engine takes over), `src/test/MediaPlayback.test.tsx` (it tests dead code), `src/ma-stream-test.ts`.

### 6.3 Store contract (implement exactly; extend only if needed)
```ts
// features/media/types.ts
export type OutputKind = 'web' | 'ma' | 'ha';
export interface Output { id: string; kind: OutputKind; name: string; room?: string; icon: 'speaker'|'tv'|'web'|'group';
  available: boolean; state: PlaybackStatus; volume: number /*0-100*/; muted: boolean; groupMembers?: string[]; maPlayerId?: string; haEntityId?: string }
export type PlaybackStatus = 'idle' | 'loading' | 'playing' | 'paused' | 'buffering' | 'error';
export type RepeatMode = 'off' | 'all' | 'one';
export type MediaKind = 'track' | 'album' | 'artist' | 'playlist' | 'radio' | 'audiobook' | 'podcast' | 'episode';
export interface MediaItem { uri: string; kind: MediaKind; title: string; subtitle?: string; artists?: {name:string; uri?:string}[];
  album?: {name:string; uri?:string}; imageUrl?: string /*raw upstream path; Artwork builds proxy url*/; durationSec?: number;
  source: 'ma' | 'abs'; favorite?: boolean; progress?: number /*0-1*/; chapters?: {title:string; startSec:number}[] }
export interface QueueItem { queueItemId: string; item: MediaItem }
export interface NowPlaying { item: MediaItem | null; positionSec: number; positionUpdatedAt: number /*ms epoch*/; durationSec: number;
  status: PlaybackStatus; shuffle: boolean; repeat: RepeatMode; speed: number; isLive: boolean }

// features/media/store/mediaStore.ts
interface MediaState {
  outputs: Record<string, Output>;           // keyed by Output.id (web = 'web', ma = 'ma:<id>', ha = entity_id)
  activeOutputId: string | null;             // what the controls drive
  userPickedOutput: boolean;
  nowPlaying: Record<string, NowPlaying>;    // per output id
  queue: Record<string, QueueItem[]>;        // per output id (MA/web only)
  connection: { web: 'idle'|'connecting'|'connected'|'reconnecting'|'failed'; events: 'connecting'|'open'|'closed' };
  pending: Record<string, true>;             // per action key e.g. 'play:<uri>', 'transport:next'
  sleepTimer: { endsAt: number | null; mode: 'time'|'endOfItem'|null };
  ui: { nowPlayingOpen: boolean; queueOpen: boolean; outputOpen: boolean };
  actions: {
    selectOutput(id: string): Promise<void>;
    play(item: MediaItem, opts?: { option?: 'replace'|'next'|'add'; startSec?: number }): Promise<void>;
    togglePlay(): Promise<void>; next(): Promise<void>; previous(): Promise<void>;
    seek(sec: number): Promise<void>; skip(deltaSec: number): Promise<void>;
    setVolume(v: number): void /*debounced 150ms internally, optimistic*/; toggleMute(): Promise<void>;
    setShuffle(on: boolean): Promise<void>; cycleRepeat(): Promise<void>; setSpeed(x: number): Promise<void>;
    queueMove(queueItemId: string, toIndex: number): Promise<void>; queueRemove(queueItemId: string): Promise<void>; queueClear(): Promise<void>;
    transferTo(outputId: string): Promise<void>; setGroup(leaderId: string, memberIds: string[]): Promise<void>;
    toggleFavorite(item: MediaItem): Promise<void>; setSleepTimer(mins: number | 'endOfItem' | null): void;
    ingest(event: MediaEvent): void;          // reducer for server/web events (pure; unit-tested heavily)
  };
}
```
Rules:
- **Optimistic, then reconcile.** Every action sets the optimistic state + `pending[key]`, calls the adapter, then clears pending. On error it **rolls back** to a snapshot and shows `toast.error('<Service>: <message>')`.
- **Position** is never stored as a ticking number. Store `positionSec` + `positionUpdatedAt`. Components compute the live position with `useLivePosition(outputId)`, a hook using `requestAnimationFrame` throttled to 4Hz that re-renders **only** the scrubber/time, never the page (this fixes the whole-page 1s re-render).
- **Volume grace:** ignore incoming volume events for 1.5s after a local volume change (replaces the ad-hoc 4s refs).
- Persist (zustand `persist`, localStorage, wrapped in try/catch) **only**: `activeOutputId`, `userPickedOutput`, last web `nowPlaying.item` + position (for "resume where you left off" after an app restart), recent searches.

`PlayerAdapter`:
```ts
export interface PlayerAdapter {
  kind: OutputKind;
  play(outputId: string, item: MediaItem, opts): Promise<void>;
  pause(outputId: string): Promise<void>; resume(outputId: string): Promise<void>;
  next(outputId: string): Promise<void>; previous(outputId: string): Promise<void>;
  seek(outputId: string, sec: number): Promise<void>;
  setVolume(outputId: string, v: number): Promise<void>; setMute(outputId: string, m: boolean): Promise<void>;
  setShuffle?(outputId: string, on: boolean): Promise<void>; setRepeat?(outputId: string, m: RepeatMode): Promise<void>;
  setSpeed?(outputId: string, x: number): Promise<void>;
  getQueue?(outputId: string): Promise<QueueItem[]>; queueMove?(…): Promise<void>; queueRemove?(…): Promise<void>; queueClear?(…): Promise<void>;
  transfer?(fromId: string, toId: string): Promise<void>; group?(leaderId: string, memberIds: string[]): Promise<void>;
  capabilities(outputId: string): Capabilities;  // UI hides controls the output can't do
}
```
- `webPlayerAdapter`: wraps `SendspinEngine` + `maRpc` with `queue_id = webPlayerId`.
- `maPlayerAdapter`: `maRpc` commands (§7.3).
- `haPlayerAdapter`: `POST /execute/media/transport|play` and the new queue/shuffle/repeat commands (§7.1); `capabilities` come from HA `supported_features`.
- ABS items: on MA-backed outputs, play the MA ABS URI **(VERIFY the format: the current code uses both `audiobookshelf://<id>` in Media.tsx and `library://audiobookshelf/book/<id>` in maWebPlayer.ts:672-685; test both against MA, keep the one that works, and delete the other)**. Progress write-back is §7.5.

Query-key factory (`api/keys.ts`):
```ts
export const mediaKeys = {
  all: ['media'] as const,
  recent: () => [...mediaKeys.all, 'recent'] as const,
  playlists: () => [...mediaKeys.all, 'playlists'] as const,
  search: (q: string, type?: string) => [...mediaKeys.all, 'search', q, type ?? 'all'] as const,
  album: (p: string, id: string) => [...mediaKeys.all, 'album', p, id] as const,
  // artist, playlist, book, podcast, favorites, continue, library(tab, page)…
};
```
Defaults: `staleTime: 60_000`, `retry: 1`, `refetchOnWindowFocus: false`, `placeholderData: keepPreviousData` for search and paging.

### 6.4 Shell and overlays
- **P4-T1: `AppShell`.** Refactor `App.tsx` so a single `<AppShell>` wraps all protected routes via a layout route (`<Route element={<ProtectedLayout/>}>` + `<Outlet/>`). This stops the shell remounting per navigation (required for the mini-player to persist). Keep the `isMobile` logic from `MobileShell`/`ProtectedRoute` unchanged.
- Fix the double bottom padding: `MobileShell.tsx` pads `main` by 5.5rem, **and** `.safe-area-bottom` pads again. Define CSS vars `--bottom-nav-h: 64px` and `--mini-player-h: 64px` (0 when hidden). `main` gets `padding-bottom: calc(var(--bottom-nav-h) + var(--mini-player-h) + env(safe-area-inset-bottom) + 16px)`.
- z-index scale (add to `index.css`): `--z-nav: 40; --z-mini: 45; --z-sheet: 60; --z-toast: 70`. Today BottomNav and Modal are both `z-50`.
- `components/ui/Sheet.tsx`: vaul `Drawer` on <768px, Radix `Dialog` side/center panel on ≥768px. Required: `role=dialog`, `aria-modal`, labelled title, Esc closes, focus trap + restore, body scroll lock, heights in `dvh`. The Android back button closes the top sheet: listen to `@capacitor/app` `backButton` and keep a sheet stack in the store.

### 6.5 Visual design system for Media
- Use theme tokens only: `var(--site-accent)`, `--site-text`, `--site-text-muted`, `--site-card-bg`, `--color-surface-0/1/2`, `--radius-card`, `--radius-panel`, and the `.glass-*` classes. **No raw `cyan-*/purple-*/pink-*/amber-*` classes in `features/media`.** Enforce it with an ESLint `no-restricted-syntax` rule scoped to `src/features/media/**` that flags className strings matching `/\b(cyan|purple|pink|amber|fuchsia|violet)-\d{2,3}/`.
- **Dynamic accent:** `artworkColor.ts` loads the proxied artwork into an offscreen canvas (the image must be same-origin via the proxy, so there's no CORS taint), picks a vibrant color, and adjusts it to ≥4.5:1 contrast against the dark surface (tweak the HSL lightness until the contrast passes; write a `contrastRatio()` util and unit-test it). It sets `--media-accent` and `--media-accent-soft` on the Now Playing sheet and mini-player root. Cache by image URL (a Map, max 100). Fall back to `--site-accent`.
- Typography: keep Outfit. Title in Now Playing `clamp(1.5rem, 4vw, 2.25rem)` weight 700. Numbers/times use `font-variant-numeric: tabular-nums` (no monospace font for times).
- Artwork: square, `border-radius: var(--radius-card)`, `box-shadow: 0 30px 80px -20px color-mix(in oklab, var(--media-accent) 45%, transparent)`. Fallback = gradient from `--media-accent` to the surface, with a lucide icon for the kind.
- Touch targets: min 44×44px everywhere (the transport buttons in the mini-player too).
- Layout grid: shelves use CSS scroll-snap, `gap: 12px` on mobile and `16px` on desktop; card width `clamp(132px, 38vw, 184px)`.

### 6.6 Web Player engine (rewrite of `lib/maWebPlayer.ts`)
Plain TS classes, no React:
- `maRpc.ts`: `connect(): Promise<void>` returns one shared promise (BUG-46). `call(command, args, {timeoutMs=10000})` uses a pending `Map<message_id, {resolve, reject, timer}>` (BUG-48). On close, reject all pending calls. Auto-reconnect with backoff + jitter (BUG-50). `onEvent(cb)` delivers MA server events (`player_updated`, `queue_updated`, `queue_items_updated`, `queue_time_updated`) to the store's `ingest`. Filtering uses strict equality on `player_id`/`queue_id` (BUG-52).
- `sendspinEngine.ts`: owns **exactly one** `<audio>` element (BUG-49), created lazily and removed on `dispose()`. `unlockAudio()` is a single function (it replaces the 4 copies at maWebPlayer.ts:165-188, 657-670, 714-723, 818-827), called from the first user gesture handler installed by the provider. Keep the access to Sendspin private fields (`core.wsManager.ws`, `scheduler`) behind **one** adapter file `sendspinInternals.ts` with a comment and a unit test that fails if the shape changes after a library upgrade.
- `tabLock.ts`: the BroadcastChannel `jarvis-media`. When tab B starts web playback, it broadcasts `claim`; tab A pauses its web audio and shows "Playing in another tab — Take over". Heartbeat every 2s; the lock expires after 6s. Unit-test it with a fake BroadcastChannel.
- The engine lives for the app lifetime. The page never calls `destroy` (BUG-40). The provider disposes only on logout.

### 6.7 MediaSession (web and Android WebView)
In `mediaSession.ts`, subscribed to the store:
- `metadata`: title, artist, album, `artwork: [96,192,256,512].map(s => ({src: proxiedArtworkUrl(img, s), sizes:`${s}x${s}`, type: 'image/jpeg'}))`. It must go through the proxy (BUG-56).
- `playbackState` mirrors the status.
- `setPositionState({duration, position, playbackRate})` on every position anchor change. Skip it for live streams.
- Handlers: `play`, `pause`, `stop`, `previoustrack`, `nexttrack`, `seekto`, `seekbackward` (15s), `seekforward` (30s). Wrap each `setActionHandler` in try/catch (not every browser supports every action).
- On logout or when nothing is loaded: `metadata = null`, `playbackState = 'none'`.

### 6.8 Android native: real background playback + lock-screen controls
Why: Android System WebView does not turn `navigator.mediaSession` into a system notification, and the manifest declares `FOREGROUND_SERVICE_MEDIA_PLAYBACK` without a service. This part carries risk, so do it last (Phase 8).
1. `android/app/src/main/java/com/jarvisos/app/media/MediaPlaybackService.java`: a foreground `Service` using `androidx.media:media` (`MediaSessionCompat` + `NotificationCompat.MediaStyle`), `foregroundServiceType="mediaPlayback"`. It holds a partial `WakeLock` and a `WifiLock` while playing.
2. `MediaSessionPlugin.java` (Capacitor `@CapacitorPlugin(name="MediaSession")`) with methods `update({title, artist, album, artworkUrl, durationMs, positionMs, playing, speed})` and `stop()`. Media button presses are sent to JS as `notifyListeners("action", {action})`. Register it in `MainActivity.java` next to the existing StepCounter/TokenBridge/ApkInstall plugins.
3. Manifest: `<service android:name=".media.MediaPlaybackService" android:exported="false" android:foregroundServiceType="mediaPlayback"/>` + `POST_NOTIFICATIONS` permission (Android 13+; request it at first playback).
4. `src/plugins/mediaSession.ts`: `registerPlugin('MediaSession')` with a web no-op fallback. `mediaSession.ts` (6.7) calls it when `Capacitor.isNativePlatform()`.
5. Artwork is loaded natively from the proxied URL with an `Authorization` header or signed token (§7.4).
6. Fix BUG-55 (`MediaWidget.java` parses `detail.active`) and BUG-67 in the same phase.

---

## 7. BACKEND TARGET ARCHITECTURE

### 7.1 Enrich player status (execution)
`handlers/media_status.py`: add to each player dict: `media_position_updated_at` (ISO, from the HA attribute), `media_content_id`, `shuffle`, `repeat`, `group_members`, `app_name`, `icon_kind` ('tv' if the device class is tv or the entity is a TV brand, else 'speaker'), and `ma_player_id` when it's an MA player (via `services/shared/ma_player.py`). Add schema fields in `execution/schemas.py`. Extend `MediaTransportRequest.command` Literal with `shuffle_set`, `repeat_set`, `join`, `unjoin` → HA services `media_player.shuffle_set` / `repeat_set` / `join` / `unjoin`.

### 7.2 Real-time media event stream (NEW)
- **Endpoint:** `GET /api/media/events` (gateway), Server-Sent Events, auth via signed media token `?mt=` (§7.4) because EventSource can't set headers. Also accept an `Authorization` header for native clients.
- **Server:** one `MediaEventHub` per user (gateway module `services/gateway/media_events.py`):
  - Subscribes to **HA WebSocket** `subscribe_entities` filtered to `media_player.*` **(VERIFY the HA version supports `subscribe_entities`; if not, use `subscribe_events` with `event_type: state_changed` and filter)**.
  - Subscribes to **MA** events on a persistent `ma_ws_client` connection (fix the reconnect first, BUG-23): `player_updated`, `queue_updated`, `queue_items_updated`, `queue_time_updated`.
  - Normalizes both into one event shape and fans out to all SSE subscribers of that user. The hub starts on the first subscriber and stops 60s after the last one leaves.
  - It keeps a **snapshot cache** of all players. The first SSE message is `event: snapshot` with the full player list. `/execute/media/status` reads from this cache when it's warm (BUG-31).
- **Event shape** (TS in `features/media/types.ts`, Pydantic in `gateway/media_events.py`):
  ```json
  {"type":"player","output_id":"media_player.kitchen","state":"playing","item":{"uri":"…","title":"…","artists":["…"],"album":"…","image":"…","duration":213},"position":42.1,"position_updated_at":"2026-09-26T12:00:00Z","volume":35,"muted":false,"shuffle":false,"repeat":"off","available":true,"group_members":[]}
  {"type":"queue","output_id":"ma:abcd","items_changed":true}
  {"type":"heartbeat"}   // every 15s
  ```
- Send `retry: 3000` in the stream. The client's `eventStream.ts` reconnects and re-requests the snapshot. While disconnected, the store falls back to polling `/execute/media/status` every 10s.
- Caddy/nginx: make sure SSE is not buffered. Check `Caddyfile` and `services/ui/nginx.conf`, and add `flush_interval -1` / `proxy_buffering off` for `/api/media/events` **(VERIFY the current config)**.

### 7.3 MA JSON-RPC allowlist (BUG-04) and the commands the UI uses
Allowlist (verify each name against MA's `/api-docs` or the `docs/MEDIA_UPSTREAM_API_NOTES.md` you create; **all are (VERIFY)**):
```
players/all, players/get, players/cmd/play, players/cmd/pause, players/cmd/play_pause, players/cmd/stop,
players/cmd/seek, players/cmd/volume_set, players/cmd/volume_mute, players/cmd/group, players/cmd/group_many, players/cmd/ungroup,
player_queues/all, player_queues/get, player_queues/items, player_queues/play_media, player_queues/next, player_queues/previous,
player_queues/play_index, player_queues/move_item, player_queues/delete_item, player_queues/clear, player_queues/shuffle,
player_queues/repeat, player_queues/transfer, player_queues/seek, player_queues/skip,
music/search, music/item_by_uri, music/recently_played_items, music/in_progress_items, music/recommendations,
music/favorites/add_item, music/favorites/remove_item,
music/albums/library_items, music/albums/album_tracks, music/artists/library_items, music/artists/artist_albums, music/artists/artist_tracks,
music/playlists/library_items, music/playlists/playlist_tracks, music/playlists/add_playlist_tracks, music/playlists/remove_playlist_tracks,
music/tracks/library_items, music/radios/library_items, music/podcasts/library_items, music/podcasts/podcast_episodes
```
Put it in `services/gateway/ma_allowlist.py` as a `frozenset`, and unit-test that a non-listed command is rejected with `{"error_code": "forbidden", "message_id": <same id>}`. Also enforce a server-side **per-user player scope**: commands whose args contain `player_id`/`queue_id` must reference a player the user may access (same rules as `verify_entity_access`, mapped via `ma_player.py`), except the user's own web player id.

### 7.4 Signed media tokens (replace `?token=<api key>`)
- `POST /api/media/token` (authenticated with the normal API key header) returns `{token, expires_at}`. The token is HMAC-SHA256 over `{user, scope:"media", exp}`, signed with the existing `INTERNAL_SECRET` (or a new `MEDIA_TOKEN_SECRET` env), TTL 1h. Put it in `services/shared/media_token.py` with `sign()` and `verify()`. Unit-test expiry, tampering, and the wrong scope.
- Accepted as `?mt=` by: `/api/media/imageproxy`, `/api/media/stream/*`, `/api/media/events`, the execution 8888 file server, `WS /api/sendspin`, and `WS /api/ma-jsonrpc`.
- The UI keeps a `mediaToken` in memory (not storage), refreshes it 5 min before expiry, and `Artwork`/engine build URLs with it. On a 401/403 from the proxy, refresh once and retry.
- Keep accepting `?token=` for **one release** behind env `MEDIA_ALLOW_LEGACY_TOKEN=true`, logged as a deprecation warning (redacted). The Android widget (`WidgetApi.java`) must be updated in Phase 8 before legacy support is removed.

### 7.5 New/changed REST endpoints for the new UI (gateway, all authenticated)

| Method + path | Purpose | Implementation |
|---|---|---|
| `GET /api/media/home` | One call for Listen Now: `{recent, continue, playlists, favorites, radio, errors:{ma?:str, abs?:str}}` | Parallel `asyncio.gather` of the existing MA/ABS fetches with a timeout of 4s each; partial results + `errors` (§4.5 partial state) |
| `GET /api/media/search?q=&types=&limit=` | Unified search `{top, tracks, artists, albums, playlists, audiobooks, podcasts, authors}` | Parallel MA `music/search` + ABS server search (BUG-14); top result = best exact-title match, else the first MA track |
| `GET /api/media/item?uri=` | Album/artist/playlist/book/podcast detail with children (tracks, albums, chapters, episodes) | MA: `item_by_uri` + `album_tracks`/`artist_albums`/`playlist_tracks`. ABS: `GET /api/items/{id}?expanded=1` |
| `GET /api/media/library/{tab}?offset=&limit=&order_by=` | Paginated library | MA `*/library_items`; ABS libraries. Replaces the broken HA browse path (BUG-18) for MA |
| `GET /api/media/favorites` | List favorites | MA `library_items` with `favorite=true` **(VERIFY the param)** |
| `POST /api/media/abs/progress` | `{item_id, episode_id?, current_time, duration, is_finished?}` | ABS `PATCH /api/me/progress/{id}[/{episode}]`. The UI calls it every 15s while an ABS item plays on **any** output, and on pause/stop |
| `GET /api/media/events` | SSE (§7.2) | new |
| `POST /api/media/token` | §7.4 | new |

Response models are Pydantic in `services/gateway/media_models.py`. Mirror them as TS types in `features/media/types.ts`. **Contract test:** a pytest that dumps each model's JSON schema to `services/ui/src/features/media/__generated__/schemas.json`, and a vitest test that validates fixture responses against those schemas (use `ajv` as a dev dependency). CI then fails if they drift.

### 7.6 Docs
Rewrite `docs/MEDIA_PLAYER.md` to describe the new architecture (the current doc describes endpoints that no longer exist). Update `docs/api_reference.md` with §7.5.

---

## 8. PHASED DELIVERY PLAN (execute in order; each task = one commit)

Each task lists **Do**, then **Done when**. "Gate" = §9.1 commands green.

### Phase 0: Test infrastructure and baseline (no behavior change)
- **P0-T1** Create `.tmp/` if missing and add it to `.gitignore` if absent. Run the full gates once and save the output to `.tmp/baseline.txt`. **Done when:** you know which tests fail *before* your changes. List them in `docs/MEDIA_OVERHAUL_BLOCKERS.md` under "pre-existing failures" and don't fix unrelated ones.
- **P0-T2** Fix BUG-73 (sendspin spec module-load throw). **Done when:** `npx playwright test --list` succeeds with no env vars set.
- **P0-T3** Split Playwright into projects in `playwright.config.ts`:
  - `hermetic-desktop` (Desktop Chrome 1440×900)
  - `hermetic-mobile` (Pixel 7)
  - `hermetic-iphone` (iPhone 14, WebKit; skip audio assertions if WebKit can't autoplay in CI)
  - `live` (existing specs, `grep: /@live/`, only when `LIVE=1`)

  The hermetic projects use `webServer: { command: 'npx vite --port 5179 --strictPort', url: 'http://localhost:5179', reuseExistingServer: true }` and `baseURL: 'http://localhost:5179'`. Tag every existing media spec `@live`. Change `fullyParallel` for hermetic to true with `workers: 4`. **Done when:** `npx playwright test --project=hermetic-desktop` runs (0 tests is fine at this point).
- **P0-T4** Create `e2e/fixtures/mediaMocks.ts`:
  - `seedAuth(page)`: `page.addInitScript` sets `localStorage.jarvis_api_key='test-key'` and a `jarvis_user` JSON (check `src/lib/storage.ts` for the web storage backend and key names), plus `page.route('**/api/users/me', …)` returning an admin profile.
  - `mockMediaApi(page, scenario)`: `page.route` handlers for every media endpoint in §7.5 and §1 of the audit (`/execute/media/*`, `/api/media/**`), serving JSON fixtures from `e2e/fixtures/media/*.json`.
  - `mockMaJsonRpc(page)`: `page.routeWebSocket('**/api/ma-jsonrpc**', ws => …)` fake MA that replies to allowlisted commands and can push events.
  - `mockSendspin(page)`: `page.routeWebSocket('**/api/sendspin**', …)` that accepts the handshake. Audio bytes are not required; assert state, not sound.
  - `mockEvents(page)`: route `**/api/media/events**` returning `text/event-stream` bodies.
  - Scenarios: `happy`, `abs-down`, `ma-down`, `empty-library`, `slow` (2s delays), `offline`.

  Also catch unmocked `/api/**` calls and **fail the test** (so nothing silently hits a real server). **Done when:** a smoke spec `e2e/media/hermetic-smoke.spec.ts` loads `/media` with mocks and sees the heading, on all hermetic projects.
- **P0-T5** Vitest infra: add `msw` with `src/test/msw/handlers/media.ts` reusing the same JSON fixtures (import from `e2e/fixtures/media`). Add a `FakeWebSocket` in `src/test/fakes/FakeWebSocket.ts` and a `FakeBroadcastChannel`. Add a `navigator.mediaSession` mock to `src/test/setup.ts`. **Note:** `setup.ts` has uncommitted user changes, so append only and don't reformat. **Done when:** a trivial msw-backed test passes.
- **P0-T6** Add `@axe-core/playwright`, plus a helper `expectNoA11yViolations(page)` (fails on `serious`/`critical`).
- **P0-T7** Backend test fixtures: `services/gateway/tests/conftest_media.py` (or extend the existing conftest) with a fake identity resolver, an authenticated TestClient, and `aioresponses` for upstream MA/ABS/HA.

### Phase 1: Security (BUG-01…09)
One task per bug, in ID order: **P1-T1 = BUG-01 … P1-T9 = BUG-09**. BUG-07/08 depend on §7.4, so do **P1-T0: implement `services/shared/media_token.py` + `POST /api/media/token` + tests** first.
**Phase done when:** all P0 bug tests pass, and `grep -rn "token=" services/gateway/main.py | grep -i log` shows no unredacted logging.

### Phase 2: Backend correctness (BUG-10…32) + new endpoints
- P2-T1 … P2-T23: one task per bug BUG-10 … BUG-32.
- P2-T24: status enrichment (§7.1).
- P2-T25: `MediaEventHub` + `/api/media/events` (§7.2) with tests:
  - HA and MA fake event → a normalized SSE message
  - snapshot is sent first
  - heartbeat
  - the hub stops 60s after the last subscriber (use an injectable clock)
  - user isolation: user A never receives user B's players
- P2-T26: MA allowlist + player scope (§7.3).
- P2-T27 … P2-T32: each endpoint in §7.5 with pytest (happy path, upstream down → partial/502, auth required).
- P2-T33: the schema-dump contract test (§7.5).
- P2-T34: rewrite `docs/MEDIA_PLAYER.md` and update `docs/api_reference.md`.
**Phase done when:** `pytest services/gateway services/execution services/tests -q` is green, and a manual `curl` of each new endpoint against the dev stack returns the documented shape (save the outputs to `.tmp/phase2-curl/`).

### Phase 3: Frontend foundations (no visual change yet)
- P3-T1 `features/media/format.ts` + tests (BUG-41). Replace the `formatTime` copies in `Media.tsx` and `ActiveMediaWidget.tsx`.
- P3-T2 `types.ts`, `api/keys.ts`, `api/client.ts`, `api/queries.ts` + tests with msw (BUG-62/63).
- P3-T3 `engine/maRpc.ts` + tests (BUG-46/48/50/52/53).
- P3-T4 `engine/sendspinEngine.ts` + `sendspinInternals.ts` + tests (BUG-47/49).
- P3-T5 `store/mediaStore.ts` + `ingest` reducer + tests. Include a table-driven test with ≥15 event sequences covering track change, position anchors (BUG-45), volume grace, metadata replacement (BUG-51), favorite, player going unavailable, and queue updates.
- P3-T6 Adapters (web/ma/ha) + tests with fakes. For every capability, assert the exact command and args sent.
- P3-T7 `engine/eventStream.ts` + tests (reconnect, snapshot, polling fallback when closed >5s).
- P3-T8 `engine/tabLock.ts` + tests (BUG-66).
- P3-T9 `engine/mediaSession.ts` + tests (BUG-56).
- P3-T10 `MediaEngineProvider` + `useLivePosition` + tests.
**Phase done when:** coverage for `src/features/media/{engine,store,api,format}` is ≥ 90% lines (`npx vitest run --coverage src/features/media`; add `@vitest/coverage-v8` if it's missing).

### Phase 4: Persistent shell and global player (fixes BUG-40)
- P4-T1 `AppShell` layout route (§6.4). E2E: navigate across 5 routes, and assert via a `data-testid="app-shell"` element identity check (`page.evaluate` storing a marker on it) that the shell did not remount.
- P4-T2 Mount `MediaEngineProvider` above the routes. Remove the `destroyWebPlayer()` unmount from `Media.tsx`.
- P4-T3 `components/ui/Sheet.tsx` + a11y tests (BUG-61).
- P4-T4 `MiniPlayer` (mobile floating + desktop dock) with the bottom padding vars and z-index scale.
- P4-T5 `NowPlayingSheet` (shared art transition, dynamic accent, all controls in §4.2, reduced motion).
- P4-T6 `OutputSheet` (dedupe BUG-65, group, transfer, per-row volume).
- P4-T7 `QueueSheet` (dnd-kit reorder, remove, clear, virtualized).
- P4-T8 Keyboard shortcuts + the `?` help dialog.
- P4-T9 Sleep timer.
**Phase done when these E2E pass on all hermetic projects:**
- `persistence.spec.ts`: play on the web player → go to `/`, `/calendar`, and back → the store status is still `playing`, the mocked Sendspin WS is still open (not closed/reopened), and the mini-player shows the same title.
- `now-playing.spec.ts`: open via tap and via swipe-up (mobile); scrub by drag → exactly **one** seek command sent with the release position; Esc/back closes it.
- `output.spec.ts`: switching output sends the correct adapter commands; the duplicate speaker appears once.
- `queue.spec.ts`: drag item 3 to position 1 → `player_queues/move_item` sent with the correct args; keyboard reorder works too.
- `shortcuts.spec.ts`: each shortcut in §4.3 triggers the right action; shortcuts don't fire while typing in search.

### Phase 5: Screens
- P5-T1 `ListenNowPage` using `/api/media/home` (all §4.5 states).
- P5-T2 `SearchPage` (unified, grouped, keyboard, recent searches, ⌘K).
- P5-T3 `LibraryPage` (tabs, infinite scroll with react-virtual).
- P5-T4 `AlbumPage`, `ArtistPage`, `PlaylistPage`.
- P5-T5 `BookPage` (chapters, resume, progress write-back every 15s via `/api/media/abs/progress`, speed, −15/+30).
- P5-T6 `PodcastPage` (episodes, per-episode progress; fixes BUG-43).
- P5-T7 `ItemContextMenu` everywhere (Play next, Add to queue, Go to artist/album, Favorite).
- P5-T8 Replace `pages/Media.tsx` with the nested routes. Delete dead code (§6.2 list). Delete `debug-media*.mjs` (BUG-72), the `ma-stream-test` entry (BUG-71), and `MediaPlayback.test.tsx`.
- P5-T9 Rebuild `ActiveMediaWidget` on the store + shared components (BUG-54, BUG-68).
**Phase done when:** each page has a component test (msw) for loading/empty/error/partial/happy, and an E2E spec per page on all hermetic projects.

### Phase 6: Visual polish and theming
- P6-T1 The ESLint color rule (§6.5) passes for `features/media/**`.
- P6-T2 Dynamic accent + the `contrastRatio` util and tests.
- P6-T3 Visual regression: `e2e/media/visual.spec.ts` uses `toHaveScreenshot()` for Listen Now, Now Playing, Queue, Output, Album, Book, Search, on desktop + mobile, with deterministic fixtures, `animations: 'disabled'`, and fonts loaded (`await page.evaluate(() => document.fonts.ready)`). Commit the baselines. Run it under at least 2 theme packs (default + one other from `src/themes/packs`).
- P6-T5 **UX ledger pass.** Walk every row of §3.4 in order. For each UX-xx, confirm that its "Verify" item exists and passes, and add a line to `docs/MEDIA_OVERHAUL_QA.md` (`UX-xx | PASS | test name or screenshot path`). Any row that can't be marked PASS blocks Phase 6 completion.
- P6-T4 Reduced motion: an E2E with `page.emulateMedia({ reducedMotion: 'reduce' })` asserts no transform animations on sheet open (check that the computed `transition-duration` is 0s or the framer `data-reduced` flag you add).

### Phase 7: Accessibility and performance
- P7-T1 axe on every media route and every sheet open state → 0 serious/critical.
- P7-T2 Keyboard-only E2E: from `/media`, reach and play a track, open Now Playing, seek, change output, and reorder the queue, using only the keyboard.
- P7-T3 Screen-reader labels: every icon button has an `aria-label`; the scrubber has `aria-valuetext`; live region announces "Now playing `<title>` on `<output>`" (polite).
- P7-T4 Performance budget:
  - The media route chunk is lazy-loaded (`React.lazy` in `App.tsx`).
  - Initial JS for `/media` must not grow by more than 60KB gzip versus baseline. Measure with `npx vite build` and compare the `dist/assets` sizes; record them in the commit.
  - The idle Media page performs **no** periodic network calls while the event stream is open (E2E: count requests over 15s ≤ 1 heartbeat-free).
  - React Profiler test: while playing, the page component does not re-render per second (only the `Scrubber`/time do). Use a render-count test in vitest with fake timers.
- P7-T5 Images: `loading="lazy"`, `decoding="async"`, and a `w=` param sized to the rendered size ×DPR (BUG-11).

### Phase 8: Android native
- P8-T1 `MediaPlaybackService` + `MediaSessionPlugin` + manifest (§6.8).
- P8-T2 JS bridge `src/plugins/mediaSession.ts` wired into `mediaSession.ts`.
- P8-T3 BUG-55 (widget parsing) + move the widget to signed media tokens.
- P8-T4 BUG-67.
- P8-T5 Build: `cd services/ui && npm run build && npx cap sync android && cd android && ./gradlew assembleDebug`. **Done when** the build succeeds and the §9.4 device checklist passes.

### Phase 9: Cleanup and release
- Remove `MEDIA_ALLOW_LEGACY_TOKEN` support **only after** the Android widget and every caller use `mt`. Confirm with `grep -rn "token=" services/ui/src services/ui/android`.
- Bump the UI version in `services/ui/package.json` (minor).
- Final full gate + full hermetic E2E on all projects + the `live` project once against the dev server (`LIVE=1 UI_URL=… TEST_USER=… TEST_PASS=… npx playwright test --project=live`).

---

## 9. TESTING STRATEGY AND DEFINITION OF DONE

### 9.1 Gate commands (run from repo root unless noted)
```bash
# UI
cd services/ui && npm run lint && npx tsc -b && npx vitest run
cd services/ui && npx playwright test --project=hermetic-desktop --project=hermetic-mobile --project=hermetic-iphone
# Backend (only services you touched; run all three before phase completion)
pytest services/gateway -q
pytest services/execution -q
pytest services/tests -q
```
Outputs from failing runs go to `.tmp/` (for example `npx vitest run > .tmp/vitest.log 2>&1`).

### 9.2 Test pyramid required

| Layer | Tool | What |
|---|---|---|
| Unit | vitest | format, reducer/`ingest`, adapters, maRpc, sendspinEngine, tabLock, mediaSession, artworkColor/contrast, eventStream |
| Component | vitest + RTL + msw | every component in `features/media/components` and every page: all states, interactions, aria |
| Contract | pytest + vitest/ajv | backend Pydantic schemas ↔ UI fixtures |
| Backend unit/integration | pytest + aioresponses | every bug in §3.1/3.2 and every endpoint in §7.5; auth + user isolation |
| E2E hermetic | Playwright + route/routeWebSocket mocks | user journeys on desktop, Pixel, iPhone; axe; visual; reduced motion; keyboard |
| E2E live | Playwright `@live` | smoke against the real dev server: login → play a playlist on the Web Player → navigate → still playing → pause; play on one real speaker only if `LIVE_SPEAKER` env names it (never hardcode "Office TV") |
| Device | manual checklist (§9.4) | Android APK, iOS Safari, desktop Chrome/Firefox/Safari |

Rules for tests:
- **No `waitForTimeout`** in new specs. Use `expect.poll`, `expect(locator).toHaveText`, or `page.waitForRequest`.
- **No conditional `test.skip` based on data** in hermetic specs. Fixtures make the data deterministic.
- Select elements by role/label (`getByRole('button', { name: 'Next track' })`), never by Tailwind class.
- Each bug ID and UX ID appears in its test's name, for example `it('BUG-41 formats hours for long audiobooks', …)` or `it('UX-24 play button visible on touch devices', …)`, so `grep BUG-41` finds it.

### 9.3 Required user-journey E2E list (hermetic, all projects)
1. First visit, nothing playing → Listen Now shelves render → tap a Recently played card → web player plays → the mini-player appears.
2. Something already playing in the Kitchen (event snapshot) → the "Playing in your home" card shows it → tap → controls drive the Kitchen.
3. Search "beatles" → grouped results → Enter on the top result plays it.
4. Open an album → Shuffle → the queue has the album's tracks, shuffled flag on.
5. Queue: reorder, remove, clear.
6. Output: move playback from the Web Player to a speaker (transfer) → the web audio pauses, the speaker plays.
7. Group two MA speakers → both show as grouped in the Output sheet.
8. Audiobook: resume at the saved position → skip +30 → speed 1.5× → progress POST sent within 15s → a chapter tap seeks.
9. Podcast: open → play latest episode.
10. Favorite toggle optimistic + rollback on a 500.
11. `abs-down` scenario → MA shelves render + an ABS notice; `ma-down` → the reverse.
12. `offline` → offline banner; reconnect → snapshot resyncs the UI.
13. Two tabs (two pages in one context) → the second tab playing pauses the first → "Take over" works.
14. Persistence across navigation (Phase 4).
15. Sleep timer 15 min with a fake clock (`page.clock.install()` / `page.clock.fastForward('15:00')`) → pauses.
16. Android back button closes sheets (simulate via `window.dispatchEvent` on the Capacitor `backButton` listener hook you expose for tests).

### 9.4 Manual device checklist (record results in `docs/MEDIA_OVERHAUL_QA.md`: device, OS, build SHA, pass/fail, notes)
**Android APK (a real phone):**
- [ ] Play → lock the screen → the notification shows art, title, and controls; play/pause/next/seek work from the lock screen
- [ ] 15 minutes of screen-off playback without dropouts
- [ ] Bluetooth headset buttons work
- [ ] Incoming call / other app audio pauses us; resume works
- [ ] Rotate while Now Playing is open: no restart
- [ ] Switch app away and back: still playing; UI in sync
- [ ] Home-screen widget shows the correct now-playing (BUG-55)
- [ ] Back button closes sheets before leaving the page

**iPhone Safari:** plays after the first tap; lock-screen controls via MediaSession; the mini-player is not hidden behind the home indicator.

**Desktop Chrome, Firefox, Safari:** keyboard shortcuts; media keys on the keyboard; hardware media overlay shows art.

**Multi-room:** play on a speaker from the phone, then control the same speaker from desktop; both UIs update within 1s (event stream).

### 9.5 Definition of Done (whole project)
- [ ] Every UX-xx row in §3.4 is marked PASS in `docs/MEDIA_OVERHAUL_QA.md` with its evidence (test name or screenshot)
- [ ] Every BUG-xx row has a passing test whose name contains its ID (`grep -rn "BUG-" services/ui/src services/ui/e2e services/*/tests | wc -l` ≥ number of bug rows)
- [ ] All gates in §9.1 are green; no new lint warnings
- [ ] All 16 journeys in §9.3 pass on 3 hermetic projects
- [ ] axe: 0 serious/critical on all media routes and sheets
- [ ] Visual baselines committed for 2 themes × 2 viewports
- [ ] The §9.4 checklist is fully passed and recorded
- [ ] `pages/Media.tsx` < 30 lines; no file in `features/media` > 400 lines
- [ ] No `console.log` in `features/media` except via `mediaLog`
- [ ] No API key in any URL (`grep -rn "token=\${" services/ui/src` only finds the `mt=` media-token builder)
- [ ] `docs/MEDIA_PLAYER.md`, `docs/api_reference.md`, and `docs/MEDIA_UPSTREAM_API_NOTES.md` are up to date

---

## 10. HOW TO VERIFY AN UPSTREAM API (the procedure for every "(VERIFY)")
1. **MA:** open `http://<mass_url>/api-docs` (MA 2.x serves its command reference there), or send the command through the existing admin debug route after BUG-04 makes it admin-only:
   ```bash
   # run in repo root; outputs go to .tmp/
   curl -s -H "X-API-Key: $JARVIS_ADMIN_KEY" "$UI_URL/api/ma-jsonrpc/debug/players" > .tmp/ma-players.json
   ```
   For arbitrary commands, write a tiny script `.tmp/ma_cmd.py` that opens `ws(s)://<gateway>/api/ma-jsonrpc?mt=<media token>` and sends `{"message_id":"1","command":"<cmd>","args":{…}}`. Record the request and response in `docs/MEDIA_UPSTREAM_API_NOTES.md`.
2. **ABS:** `curl -s -H "Authorization: Bearer $ABS_KEY" "$ABS_URL/api/<path>" > .tmp/abs-<name>.json`. Official reference: https://api.audiobookshelf.org.
3. **HA:** REST `GET $HA_URL/api/states/<entity>`, and WebSocket via `.tmp/ha_ws.py` (auth message, then `subscribe_entities`).
4. Turn every recorded response into a fixture under `e2e/fixtures/media/` or `services/*/tests/fixtures/`, so tests use **real** shapes.
If you have no access to live servers, stop at the first (VERIFY) that blocks you, write it in `docs/MEDIA_OVERHAUL_BLOCKERS.md`, and continue with tasks that don't depend on it.

---

## 11. OUT OF SCOPE / RISKS
- Out of scope: video (YouTube/TV casting) UI redesign (only fix BUG-28), iOS native app, lyrics if MA lacks them, playlist creation UI (add-to-existing only, if the API is verified).
- Risk: **Sendspin private fields** can break on upgrade. They're isolated in `sendspinInternals.ts` with a shape test; pin the `@sendspin/sendspin-js` version exactly.
- Risk: **Android foreground service** policies (Android 14+ requires the `mediaPlayback` type + an active MediaSession). Test on Android 14 and 15.
- Risk: **SSE through Caddy/nginx buffering.** Verify with `curl -N` through the real proxy before building UI on top of it.
- Risk: **Removing the first-user fallback** may break flows that relied on it (Android widget, voice). Search `resolve_first_user` callers and cover each with a test before removing it.

---

## Critical files (quick index)
- UI: `services/ui/src/pages/Media.tsx`, `src/lib/maWebPlayer.ts`, `src/lib/webPlayer.ts`, `src/components/widgets/ActiveMediaWidget.tsx`, `src/services/api.ts` (media methods ~1384-1540, 1779-1792), `src/App.tsx`, `src/components/layout/{MobileShell,BottomNav,Sidebar,Header}.tsx`, `src/index.css`, `src/themes/siteTheme.ts`, `src/hooks/{useHaptics,useDebounce}.ts`, `src/main.tsx`, `vite.config.ts`, `playwright.config.ts`
- Android: `services/ui/android/app/src/main/java/com/jarvisos/app/MainActivity.java`, `…/widgets/MediaWidget.java`, `…/WidgetApi.java`, `android/app/src/main/AndroidManifest.xml`
- Gateway: `services/gateway/main.py` (~8281-8640 media routes, 8537 user ctx, 8760-9102 sendspin/jsonrpc, 9269 MA stream, 9603 imageproxy, 9706 detail, 9721 favorite, 2255 logging), `services/gateway/ma_ws_client.py`, `services/gateway/media_device_cache.py`, `services/gateway/tool_registry.py`
- Execution: `services/execution/main.py`, `handlers/{media,media_status,mass_client,mass_ha_client,audiobookshelf,android_tv,roku,video}.py`, `abs_client.py`, `ha_client.py`, `media_playback_service.py`, `media_playback_registry.py`, `schemas.py`
- Shared: `services/shared/ma_player.py`; identity keys in `services/identity/schemas.py:40-43`
