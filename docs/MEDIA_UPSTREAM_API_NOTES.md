# Media upstream API verification notes

Record of every upstream API assumption the Media Overhaul relies on, and how
it was verified. Update this file as each item is confirmed against a live
service.

## Music Assistant JSON-RPC command allowlist (P1-T4 / BUG-04)

- **Source:** the allowlist in `docs/MEDIA_OVERHAUL.md` §7.3, implemented as
  `MA_COMMAND_ALLOWLIST` in `services/gateway/ma_allowlist.py`.
- **Verified so far:**
  - Cross-checked against every MA command the legacy UI actually sends over
    WS `/api/ma-jsonrpc` (`services/ui/src/lib/maWebPlayer.ts`,
    `services/ui/src/pages/Media.tsx`): `players/all`, `players/cmd/*`,
    `player_queues/play_media|next|previous|seek`. All are in the allowlist.
  - Frame shape confirmed: `{"message_id": <str>, "command": <str>, "args": {}}`
    with MA answering `{message_id, result|error_code}` — error frame shape
    `{"error_code": "forbidden", "message_id": <same id>}` matches what
    `sendJsonRpc` in `maWebPlayer.ts` already parses.
- **Live verification 2026-10-05:** fetched
  `http://<mass_url>/api-docs/commands.json` from the running MA instance
  (reachable at `http://ha.sumemail.com:8095`). It lists 310 commands. All 47
  allowlisted names exist upstream (`missing upstream: []`), every one is
  `authenticated: true`, and MA marks them with the expected scopes
  (`players.read|control`, `queues.read|control`, `library.read|write`).
  Parameter names captured for the per-user player scope: `player_id`,
  `queue_id`, `target_player`, `target_queue_id`, `source_queue_id`,
  `child_player_ids` (list).
- **Still (VERIFY):** none for the allowlist itself. New commands the UI starts
  sending must be added to §7.3 and re-checked against
  `/api-docs/commands.json` (Swagger UI at `/api-docs/swagger`, spec at
  `/api-docs/openapi.json` — the OpenAPI spec only carries examples, the
  full command list is `commands.json`).

## Signed media tokens (P1-T0 / §7.4)

- Implemented in `services/shared/media_token.py` (HMAC-SHA256,
  `MEDIA_TOKEN_SECRET` or `INTERNAL_SECRET` fallback, TTL 3600s).
- **Device-facing TTL (2026-09-27):** `DEVICE_TOKEN_TTL_SECONDS = 43200` (12h)
  is used by `media_file_url` (8888) and `abs_client.get_stream_url`
  (gateway ABS stream) because HA/Cast/Roku fetch those URLs for hours with no
  `?mt=` refresh path — §7.4's "refresh 5 min before expiry" only applies to
  the UI. Verification is TTL-agnostic (checks `exp` only), so no verify site
  changed; the default UI TTL stays 3600s.
- **Still (VERIFY):** expiry behavior of `?mt=` acceptance across each caller
  (imageproxy, stream, events, 8888 file server, sendspin WS) — covered as
  each caller is wired in BUG-07/08 tests.

## Device-facing ABS stream URL (P1-T7 follow-up, 2026-09-27)

- The URL handed to HA/MA must be reachable from the LAN, so it is built from
  `EXECUTION_EXTERNAL_HOST` (`http://<lan-ip>:11435`, Caddy publishes :11435
  and routes `/api/*` → gateway). The previous
  `http://gateway:11435/...` value is a docker-internal alias that devices
  cannot resolve — verified: `GATEWAY_INTERNAL_URL` (`.env:143`) is the compose
  service name, and no `GATEWAY_EXTERNAL_HOST` exists in the repo. Fallback
  chain when `EXECUTION_EXTERNAL_HOST` is unset: `GATEWAY_INTERNAL_URL` →
  `http://localhost:11435`.
- **Stream proxy timeouts:** `stream/abs` and `stream/ma` upstream sessions
  used `ClientTimeout(300.0, ...)` (aiohttp *total* covers body streaming),
  which cut any uninterrupted stream at ~5 minutes. Now
  `total=None, connect=15.0`. Behavior verified by code inspection; a live
  long-stream check against production is still (VERIFY).

## Imageproxy host gate → rebase (P1-T5 rework, 2026-09-27)

- The original BUG-05 fix (hard 400 for any full URL whose host ∉ configured
  mass/abs/ha hosts) was verified against production and **broke real cover
  art**: `path=http://192.168.2.20:8095/imageproxy/...` (the actual MA
  image host, probed live → 400) plus Nabu Casa / docker-alias HA
  `entity_picture` URLs. Deviation approved under the owner directive
  "ensure your changes DO NOT break ANY Player functionality" (2026-09-27),
  same precedent as the BUG-08 `.part` decision; plan row amended.
- **New behavior (verified by `gateway/tests/test_imageproxy_ssrf.py`):**
  configured host → fetched as-is; unknown host → rebased onto the
  configured base for the path-implied service (metadata IP never contacted);
  `/api/image/serve/...` now implies `ha` (was `ma` — HA entity_picture on
  Nabu Casa-style hosts); 400 only when the implied service has no base.
- Probed live: test-key identity has MA configured (reaches MA via
  `/api/media/detail`) yet its host matches none of the earlier allowlist
  candidates — mismatched-host rebasing is therefore required, not
  theoretical. **Still (VERIFY):** re-probe the real user's cover URL after
  deploy.

## TLS verification default (P1-T9 / BUG-09, 2026-09-27)

- Plan row cites `ha_client.py:20`, Roku handlers, `handlers/media.py:549`
  (actual line: media.py:550 PROPFIND). No `allow_insecure_tls` identity
  flag exists anywhere in Identity → per plan, env gate added:
  `MEDIA_ALLOW_INSECURE_TLS` (default off; documented in `.env.example`).
- **aiohttp 3.14.3 verified:** `TCPConnector` `ssl` default = `True` (uses
  default verification); `verify_ssl=False` is merged to `ssl=False` by
  `_merge_ssl_params` (genuinely unverified). Therefore:
  - `http_client.get_session(verify=False)` (plain connector, no ssl arg)
    was a **no-op** — sessions verified regardless; the bug was intent +
    explicit `ssl=False`/`verify_ssl=False` sites.
  - Direct `TCPConnector(verify_ssl=False)` sites (webos.py:124/212,
    audiobookshelf.py:516, ha_config.py:178) genuinely disable
    verification. **Left as-is deliberately:** not in BUG-09 plan scope
    and enabling verification could break self-signed TVs/ABS endpoints
    (owner directive: do not break player functionality). Recorded here
    so the choice is visible.
  - `announce_handlers.py` / `device_discovery.py` `ssl=False` LAN scans
    are intentional (plain-HTTP discovery), out of scope.
- Changes: `get_session`/`request` default `verify=True`; `verify=False`
  is now an env-gated escape hatch (warning + verify anyway unless
  `MEDIA_ALLOW_INSECURE_TLS=true`, then "TLS verification DISABLED"
  warning); session cache keyed by `(host, effective_verify)`;
  `_ha_session`/`_abs_session`/`_mass_ha_session` defaults flipped to
  `True` (behavior-neutral — they previously passed verify=False into the
  no-op branch); Roku ECP `verify_ssl=False` removed (requests are
  `http://...:8060` — SSL never involved, zero behavior change);
  `media.py:550` keeps explicit `verify=False` as the self-signed
  Nextcloud escape hatch (effective behavior unchanged unless the env
  gate is enabled).
- Unit test on the session factory: `execution/tests/test_http_client_tls.py`
  (6 tests: default verified + no warning; env-off → warn + verified;
  env-on → ssl=False + DISABLED warning; cache keyed per effective
  verification; env-off sharing; signature defaults).

## Imageproxy cache/stream + `w=`→`size=` (P2-T2 / BUG-11, 2026-09-27)

- **MA imageproxy `size` param (plan VERIFY):** partially verified against
  live MA `192.168.2.20:8095`:
  - MA's own production image URLs (observed in HA `entity_picture` data)
    are `.../imageproxy/<image_id>?size=512&fmt=jpg` — so `size=` is the
    param name MA itself emits/accepts on this route.
  - Functional probes from a workstation: `/imageproxy?path=<urlencoded
    remote url>&size={64,512}&fmt=jpg` → 400 empty body; path-segment form
    `/imageproxy/<urlsafe-b64 url>?size=…` → 400 "Invalid image id" (the id
    must be an MA-internal image id, not an arbitrary URL) — i.e. probes
    could not exercise resizing end-to-end without a real library image id.
    **Still (VERIFY):** full resize round-trip with a real MA image id.
  - `/api-docs` returns the HTML docs shell (not machine-readable openapi).
- Behavior shipped: `w=` accepted on `/api/media/imageproxy`; only forwarded
  when the resolved upstream is MA (`svc == "ma"`), replacing any existing
  `size=` in the upstream query. HA/ABS fetches ignore `w=`.
- 200 responses: `Cache-Control: private, max-age=86400, immutable` +
  upstream `ETag` forwarded; client `If-None-Match` forwarded upstream;
  upstream 304 returned as empty 304. Body is streamed in 64 KiB chunks
  (was fully buffered); request timeout raised to total=30s (streaming
  covers body delivery; images previously had total=10s end-to-end).
- Tests: `gateway/tests/test_imageproxy_cache.py` (4). Note: the gateway's
  request log also captures logging-service POSTs — helpers must filter
  upstream requests by host.

## ABS API route verification (P2-T3 / BUG-12, 2026-09-27)

- **Authority:** ABS server source `advplyr/audiobookshelf`
  `server/routers/ApiRouter.js` @ master (fetched this day; router mounted
  under `/api`). Live probes of `https://abs.sumemail.com` are useless for
  route discovery: ABS auth runs before routing and 401s every path
  (including nonexistent `/api/v1/...` ones).
- Verified YES: `GET /api/items/:id` (L108), `GET /api/me/progress/:id`
  (L183), **`PATCH /api/me/progress/:id`** (L185 — code used POST+wrong
  path), `GET /api/me/items-in-progress` (L191), `POST /api/items/:id/play`
  (L117), `POST /api/session/:id/sync` (L242), `POST /api/session/:id/close`
  (L243), `GET /api/libraries/:id/search` (L84), `GET
  /api/libraries/:id/personalized` (L82), plus `POST /session/local` (L238),
  `PATCH /me/progress/batch/update` (L184), `GET /me/listening-sessions`
  (L179), `GET /libraries/:id/collections` (L80), `GET /libraries/:id/series`
  (L78), `GET /me/progress` (L176).
- Verified NO: `/api/v1/items/:id`, `/api/v1/users/:id/progress`,
  `/users/:id/progress` — no such routes (why get_book/progress were dead).
- Changes in `execution/abs_client.py`: fixed get_book (+`?expanded=1`),
  get_progress (→ `/api/me/progress`, token-scoped; `user_id` kept for
  compat), get_book_progress, update_progress (new `abs_patch` helper,
  PATCH), sync_session_position/close_session/sync_local_session (+
  `/api` prefix), get_listening_sessions, batch_update_progress (PATCH),
  get_library_collections/series (+ `/api` prefix); NEW `play_item`,
  `search_library_items`, `get_personalized_shelves`.
- `?expanded=1` param: route verified; the expanded param itself is from
  ABS docs/client usage (accepted; harmless if ignored).
- Tests: `execution/tests/test_abs_client_paths.py` — 15 parametrized
  method+path assertions (includes the aiohttp 3.14 `stream_writer` shim
  for aioresponses, same as gateway conftest_media).
- yarl normalizes query strings alphabetically (`limit` before `q`).

## ABS progress join (P2-T4 / BUG-13, 2026-09-27)

- `GET /api/me/items-in-progress` response verified from ABS source
  (`server/controllers/MeController.js` `getAllLibraryItemsInProgress`,
  L485-518): items = `toOldJSONMinified()` + `progressLastUpdate` ONLY —
  no `currentTime`/`progress`/`isComplete`. Percent cannot come from this
  endpoint alone (the plan's literal single call would still show 0%).
- `GET /api/me/progress` (`getAllMediaProgress` L112-115) returns
  `{mediaProgress: [...]}`; each record (`MediaProgress.getOldMediaProgress`
  L155-176) carries `libraryItemId`, `duration`, `currentTime`, `isFinished`,
  `progress` (0..1), `lastUpdate` — enough for percent + is_complete.
- Fix: handlers join the two with ONE `/api/me/progress` fetch
  (`_all_progress_by_item`), so upstream calls are fixed (2) regardless of
  item count, vs old 1+N for last_played; the `progress` action now shows
  real percentages. Plan row amended accordingly (test = fixed call count,
  no per-item fetch; progress value passed through).
- Graceful degradation: progress fetch error → warning log, books still
  returned at 0% (same UX as the old broken per-item path, minus N calls).

## ABS search response shapes + author-chip path (P2-T5 / BUG-14, 2026-09-27)

- Book-library `GET /api/libraries/{id}/search` verified from ABS source
  (`server/libraries/filters/bookFilters.js` `search` L1093): returns
  `{book: [{libraryItem: toOldJSONExpanded}], narrators: [{name,numBooks}],
  tags, genres, series: [{series, books: [<plain item JSON>]}],
  authors: [{id, name, numBooks}]}`. The book where-clause matches
  **title/subtitle/asin/isbn only** — author, narrator and series names are
  separate result keys, so an author-name query returns ZERO books.
- Podcast-library search (`podcastFilters.js` `search` L361): returns
  `{podcast: [{libraryItem}], tags, genres, episodes: [{libraryItem with
  recentEpisode}]}` — matches title+author; no authors/series keys.
- `limit` applies per result section (default 12); handler requests
  `max(req.limit, 25)` per library.
- Author chip flow (Media.tsx): tapping a chip re-runs search with the
  author's NAME as the query and renders whatever `books` come back — so
  author-name queries must keep returning books. Solved WITHOUT the
  forbidden `/items` listing via `GET /api/authors/{id}?include=items`
  (ApiRouter L217, `AuthorController.findOne` L40-86) whose `libraryItems`
  key holds the author's books. Chip entries get the real ABS author id;
  books from that endpoint are fetched in parallel with `asyncio.gather`.
- Narrator-name queries: `narrators` matches carry only names (no ids) and
  no non-`/items` endpoint resolves a narrator's books, so narrator chips
  are listed but their books are not auto-added (documented limitation;
  ABS's items filter does support `narrators:{name}` if ever needed).
- Plan row BUG-14 amended to include the authors-endpoint requirement.

## ABS liveness ping route (P2-T7 / BUG-16, 2026-09-27)

- `GET /ping` **verified**: ABS `server.js` registers it on the ROOT router
  (L385, `router.get('/ping', …)` → `{success: true}`) — mounted via
  `app.use(RouterBasePath, router)` (L318) BEFORE `router.use('/api', auth, …)`
  (L338), so it is unauthenticated and needs no token — ideal liveness probe.
- `/api/books` **does not exist**: no match anywhere in `server/routers/
  ApiRouter.js` — the old status probe hit a route ABS never had (only worked
  when ABS's SPA/static fallback answered 200).
- Endpoint now resolves the caller's identity and pings the user's own
  `audiobookshelf_url` (trailing-slash normalized) instead of reading the
  global identity settings list (which also left `abs_url` unbound on a
  non-200 settings response → UnboundLocalError → generic ERROR).

## MA direct search: typed failure + filter params (P2-T8 / BUG-17, 2026-09-27)

- `mass_client.search` (WS `music/search`) now raises `MASearchError` for
  every failure mode: connection errors, receive-loop timeout (20s),
  socket CLOSED/ERROR before a result, and MA `error_code` responses.
  Previously all of these returned `[]`, which `search_ma` treated as an
  authoritative "no matches" — the HA-proxy fallback never ran.
- Legitimately-empty results still return `[]` on purpose (do NOT fall
  through to HA on empty — that produced spurious FAILUREs, see comment
  in `search_ma`).
- `library_only=True` keeps `config.providers=["library"]` (explicitly
  listing every provider hangs in MA 2.9.x, per the original docstring);
  `library_only=False` omits `config` so MA's own provider defaults apply.
- `artist`/`album` are applied as casefold substring filters over the
  returned tracks client-side (MA's `music/search` has no author/album
  filter args; the HA `music_assistant.search` service does the same kind
  of narrowing server-side). Params were previously dropped entirely on
  the direct path.
- Scope note: `mass_ha_client.search` (HA proxy path) already accepted and
  forwarded all three params; only the direct path was missing them.

## HA music_assistant.get_library service contract (P2-T9 / BUG-18, 2026-09-27)

Source: Home Assistant core `dev` branch, integration lives IN HA core at
`homeassistant/components/music_assistant/` — verified by fetching
`services.yaml`, `services.py`, `const.py`, `schemas.py`, `helpers.py`
(cached in `.tmp/ha_ma_*`).

- `get_library` service schema (`services.yaml:176`):
  - `config_entry_id`: **required: true** (`config_entry` selector, integration
    `music_assistant`). Omitting it makes HA reject the call
    (`ServiceValidationError`).
  - `media_type`: required select, options = **lowercase singular**:
    `artist, album, audiobook, playlist, podcast, track, radio`.
    Uppercase plurals like `"TRACKS"` are invalid (Coerce(MediaType) fails).
  - optional: `favorite` (bool), `search` (text), `pagination.limit` (1..500,
    default 25), `pagination.offset`, `order_by` (name/name_desc/sort_name/
    sort_name_desc/timestamp_added/timestamp_added_desc/last_played/...).
- Response (`services.py:312-321`): `handle_get_library` returns
  `LIBRARY_RESULTS_SCHEMA` = `{"items": [...], "limit": int, "offset": int,
  "order_by": str, "media_type": MediaType}` (`const.py`: `ATTR_ITEMS="items"`).
  There is **no per-type key** (`tracks`/`playlists`/... do NOT exist). HA's
  service API wraps it: `{"changed_states": [...], "service_response": {...}}`
  — the old code read top-level keys, so browse always returned `[]`.
- Item dicts come from `schemas.media_item_dict_from_mass_item`: always
  `media_type, uri, name, version, image` (+ favorite/explicit and per-type
  extras such as duration/discart/fanart).
- Callers pass `media_type` case/plural-insensitively; `get_library`
  normalizes (lowercase + strip one trailing `s`) before sending and echoes
  the normalized singular as each item's `type`.
- The browse endpoint forwards `mass_config_entry_id` from resolved creds as
  `config_entry_id`; when it is empty the field is omitted entirely (never
  sent as `""`), keeping older HA installs that predate `required: true`
  working.

## MA stream start vs bytes (P2-T10 / BUG-19, 2026-09-27)

- Split: `POST /api/media/stream/music-assistant` = start (play_media
  `option=replace`, then up to 15s of stream-URL discovery over the MA WS via
  `player_queues/get`, cached per user+URI for 2h); `GET` = bytes only. A GET
  that finds no stream URL answers **409 "Playback session not started for
  this URI. Start playback first."** — it never calls `play_media`
  (acceptance: two Range GETs → 0 `play_media` calls,
  `tests/test_ma_stream_start_split.py`).
- Cold-GET discovery order: `get_stream_url` → queue-state
  `current_item.media_item.stream_url` (only with a matching `queue_item_id`)
  → `current_item.stream_url` → constructed
  `http://{host}/flow/{queue_id}/{queue_item_id}/{flow_player_id}.mp3`.
  Results feed `MAWebSocketClient.ingest_queue_state` (synchronous, no I/O) so
  the cold path reuses the same `_extract_stream_url` as live events.
- The `responseURL` helpers in `services/ui/src/services/api.ts`
  (`getAudiobookStreamUrl`, `getMusicAssistantStreamUrl`) had **zero callers**
  (repo-wide grep) — deleted with their stale "Mobile-local audio streaming"
  comment.
- Interaction to re-verify on the live stack: `@live` e2e
  `e2e/media-playback.spec.ts:578/:749` await a **200** from
  GET `/api/media/stream/music-assistant` after clicking Web Player. The
  browser Web Player (`lib/maWebPlayer.ts`) starts playback itself through
  `/api/ma-jsonrpc` (+ sendspin audio straight to MA), so the queue is playing
  before the GET — but a cold queue now yields 409 instead of auto-starting.

## Gateway test config freeze pitfall (P2-T10 follow-up, 2026-09-27)

- `services/gateway/config.py` freezes `INTERNAL_SECRET` and
  `ALPACA_AUDIO_URL` at its **first import**, while the root `conftest.py`
  pre-sets `INTERNAL_SECRET=test-secret`. Whichever test module imports
  gateway `main` first therefore wins: `test_music_proxy.py` (which sets both
  to its own values at module import) got 401/503 whenever an
  earlier-alphabetical file (e.g. `test_ma_jsonrpc_allowlist.py`) imported the
  app first — full-suite order dependence, not a product bug.
  `tests/conftest_media.py` now **force-sets** both values (it loads before
  every module in the directory), so config can only ever freeze the gateway
  test values. Prefer forcing over `setdefault` here.

## Gateway emit_log pollutes HTTP-client mocks (P2-T11 / BUG-20, 2026-09-27)

Any gateway test that mocks `get_http_client()` (or `shared_http_client()`)
and counts/queues `post` side effects must ignore the request-logging
middleware: `emit_log` (`gateway/main.py:995`) posts every REQUEST/RESPONSE
log line to `{LOGGING_SVC}/log` through the same client, with exceptions
swallowed. Those calls consume `side_effect` lists and inflate call counts
(observed: real call + 2 log emits = 3). Filter doubles by URL (e.g. count
only `/execute/…` targets) or return a benign stub for non-target URLs —
see `tests/test_execution_proxy_retry.py::ExecPost`.

## MA WS client reconnect/error/state hardening (P2-T14 / BUG-23, 2026-09-27)

Five sub-bugs fixed in `services/gateway/ma_ws_client.py` (plan row 112; the
row's refs `444-455,501,525,552,670,680` were pre-P2-T11 and stale):

1. **Reconnect never started** — `_message_loop` logged the close and set
   `_connected = False`, but nothing ever started the existing `_reconnect`
   (it could only self-reschedule after a first attempt had run). The loop
   now arms `_schedule_reconnect()` from both `ConnectionClosed` (any close
   code, so a graceful MA restart with 1000/1001 reconnects too) and the
   generic-error exit. Guards: no-op when `_shutdown_event` is set
   (deliberate `disconnect()`) or a reconnect task is already pending.
2. **ERROR frames did not resolve futures** — `send_command` waited the FULL
   timeout after MA had already rejected the command. Both error branches
   (typed `{"type":"ERROR","message_id":…,"error":{…}}` and the no-type
   `error_code`/`details` shape) now call `_fail_pending_response(msg_id,
   details)` so the waiting future raises `RuntimeError("MA error: …")`
   immediately, and both record `_ma_error_code`/`_ma_error_details` so
   `get_ma_error()` sees typed errors too. Both main.py call sites already
   wrap `send_command` in `except Exception` → HTTP 502, so they now fail
   fast with the real reason instead of a timed-out `None`.
3. **`player_updated` overwrote queue state** — the payload is player-shaped
   (volume/active source); assigning it to `_queue_state` destroyed
   `current_item`/`queue_id`. Player events no longer touch `_queue_state`;
   registered callbacks still receive every event.
4. **"Jitter" was a constant, not random** — the old expression
   `delay * RECONNECT_JITTER * (1.0 - 2.0 * hash(str(time.time())) % 1)`
   evaluates `2.0 * int % 1`, which is identically `0.0` (any integer-valued
   float ≥ 2^52 has no fractional part), so the bracket was always `1.0` and
   the jitter was a fixed `0.5 * delay` offset with zero spread across
   processes. Replaced per plan with `random.uniform(0, delay *
   RECONNECT_JITTER)`.
5. **No `queue_id` filter** — any queue event (including another player's)
   replaced our queue state and stream URL. New `_queue_id` is adopted from
   (a) `ingest_queue_state` (authoritative: the `player_queues/get` answer),
   (b) the first queue event seen, (c) `queue_id` on outgoing command args
   (`_track_queue_id`), which arms the filter before the first event can
   arrive. Gateway commands address queues by player id — main.py passes
   `queue_id=target_player_id` to both `play_media` and `player_queues/get`
   (MA keys the per-player queue by the player id) — so command args are a   reliable source. Events whose `queue_id` differs from the tracked one are
   ignored for state/stream extraction but still dispatched to callbacks;
   events with no `queue_id` are accepted (cannot be attributed).

Tests: `test_ma_ws_client.py::TestBug23Hardening` (6 — one per sub-bug plus
command-arg tracking). Plan-driven updates to 3 existing tests: the
player-event dispatch test now asserts queue state is preserved, the
full-command integration test's event carries the command's `queue_id`
(`player_1`), and the message-loop close test patches `_reconnect` and
asserts the reconnect task is armed. Gateway suite: **482 passed / 2 skipped
/ 0 failed**.

## aiosqlite registry connections hang test-process exit (P2-T15 / BUG-24, 2026-09-27)

Pre-existing infra pitfall found while gating BUG-24: any pytest run that opens
`services/execution/media_playback_registry.py::_db` (or `device_registry.py::_db`)
never exits cleanly. aiosqlite's connection worker thread is non-daemon and only
stops when `Connection.close()`/`stop()` sends its sentinel (or `__del__` fires on
GC). Both registries hold the connection in a module global (`_db`) that is never
closed and never garbage-collected, so at interpreter shutdown
`threading._shutdown` joins the blocked worker forever — the pytest summary is
printed first, then the process hangs.

Verification: faulthandler dump showed main thread in `threading._shutdown` joining
`_connection_worker_thread`; a minimal script that opens the registry connection
hangs the same way, while the same script with `await db.close()` (or a local
`db` that gets GC'd) exits cleanly.

Until a session-end fixture closes both registries, wrap execution-suite runs in
`timeout N pytest … > log 2>&1` and read the summary from the log (exit code 124
is expected and does NOT mean the tests failed).

## MASS search enqueue pass-through + no random fallback (P2-T16 / BUG-25, 2026-09-27)

Upstream check: HA core's `music_assistant` integration `services.yaml`
(`play_media.service_data.enqueue`) declares `select` options
**`play | replace | next | replace_next | add`** (fetched from the HA repo,
cached `.tmp/ha_music_assistant_services.yaml`). Our schema
(`MediaPlayRequest.enqueue: Literal["add", "next", "replace"]`) is a subset,
so `req.enqueue` maps 1:1 and must pass through to `music_assistant.play_media`
unchanged — the old code rewrote `replace` → `play`, silently dropping
clear-queue semantics.

Contract change in `services/execution/handlers/media.py::play_music`
(BUG-25): when the MASS `search` call returns no usable track/album/artist/
playlist/radio item, the endpoint now returns `FAILURE "No match for '<query>'."`
instead of the two fallbacks: `music_assistant.get_library` with
`order_by=random` (playing a random library track) and a last-resort
`play_media` with the raw query string as `media_id` (upstream rejects or
mis-resolves arbitrary strings). If `play_media` on a *found* match fails, the
error is surfaced as `FAILURE "Failed to play '<query>': <reason>"`.

Tests: `test_media_music_no_random_fallback.py` (5 — no-match ×2, enqueue
pass-through ×3 incl. direct-URI) + plan-driven update of
`test_execution_main.py::test_media_play_valid` (now supplies a real search
match; adds `test_media_play_no_match_is_failure`). Execution suite: **389
passed / 1 failed (pre-existing, unrelated family_games) / 59 skipped**.

## ABS 2.x session-based playback (P2-T17 / BUG-26, 2026-09-28)

Live probes against `https://abs.sumemail.com` (the `/audiobookshelf`
prefix base and the root base serve the same instance; the prefix works for
every route below).

- **The legacy item stream route is gone.**
  `GET /api/items/:id/stream?format=mp4` → **404 for both books and
  podcasts**. The old gateway `stream_audiobookshelf` endpoint and
  `abs_client.get_stream_url` book flow therefore 404 live — book playback
  was NOT switched to the session flow in BUG-26 (see book-HLS caveat
  below); that path is a separate bug (surfaced to user, 2026-09-28).
- Podcast library search matches via `GET /api/libraries/{id}/search?q=…`
  (param is `q`, not `search`); its `episodes` section returns *podcast*
  items matching the title, wrapped in `libraryItem`.
- `GET /api/items/{podcastId}?expanded=1` → `media.episodes[]` = FLAT
  episode objects: `id`, `title`, `publishedAt` (ms), `pubDate`, `season`,
  `episode`, `audioTrack`, `duration`, `libraryItemId`, `podcastId` (88
  episodes for Culture Apothecary). `GET /api/items/{episodeId}` → 404 —
  episodes are NOT standalone items.
- **Starting a session:** book → `POST /api/items/{bookId}/play` (no body);
  podcast episode → `POST /api/items/{podcastItemId}/play/{episodeId}`
  (no body) → 200 session `{id, userId, libraryItemId, episodeId,
  mediaType: "podcast"|"book", …}`.
- **Audio:** `GET /public/session/{sid}/track/{i}` — unauthenticated (the
  session id is the capability; the `/public` router is mounted without API
  auth). Podcasts: track **0** (index 1 serves the same playlist). Books:
  track **1** (index 0 → 404). The track URL 302-redirects to HLS:
  `Location: {abs-root}/hls/{sid}/output.m3u8` (absolute URL uses the root
  base, but the prefix-base equivalent works too) → 200
  `application/vnd.apple.mpegurl`: VOD playlist, RELATIVE segment URIs
  (`output-0.ts`, ~6 s segments, `video/mp2t`, ~105 KB each). The `/hls`
  router is likewise unauthenticated.
- **Book HLS was not live at probe time:** the book session 302s correctly
  but `{abs}/hls/{bookSid}/output.m3u8` → 404 even after waiting ~25 s —
  the transcode pipeline had not produced the playlist. Podcast episode
  HLS was available immediately.
- No item listing route: `GET /api/items?libraryId=…` → 404; use
  `GET /api/libraries/{id}/items` (`{results,total}`) or library search.

Gateway implementation (BUG-26), `services/gateway/main.py`:
- `GET /api/media/stream/abs-session/{sid}/{i}` — mt-token auth (authoritative,
  §7.4) or header auth; resolves the user's `audiobookshelf_url` (400 if
  unset — no key needed, session routes are unauthenticated); fetches
  `/public/session/{sid}/track/{i}` following the 302; mpegurl bodies get
  every relative segment URI rewritten to
  `{request.base_url}/api/media/stream/abs-session/{sid}/{i}/{segment}?user=&mt=`
  — devices fetch segments without headers, so each rewritten URL carries a
  signed media token (the request's own `mt` when present, else a fresh one
  minted for the identity user); non-mpegurl upstream content streams through;
  upstream error statuses pass through with their body.
- `GET /api/media/stream/abs-session/{sid}/{i}/{segment}` — same auth;
  proxies `{abs}/hls/{sid}/{segment}` bytes (Range forwarded;
  Content-Range/Length/Type passed through).
- The m3u8/segment base is the user's configured `audiobookshelf_url` —
  verified 2026-09-28 that `/public` and `/hls` are both served under that
  prefix live; no host guessing (fail-fast).
- Test-side gotcha: aioresponses JSON-encodes `payload=`; raw upstream
  bodies (m3u8 text, ts bytes, plain-text errors) must use `body=` (+
  `content_type=`) — `conftest_media.mock_upstream` now supports both.

### Book sessions work — the missing playlist was a cold-transcode race (BUG-33, 2026-09-28)

Live probe of a book (item `510e38dd-3196-4755-8a9b-7c81208c373b`, "God's Smuggler (Unabridged)", one 8.8h track, `media.tracks[0].index == 1`):

- `POST /api/items/{bookId}/play` (no body) → 200 session. `bookId` is **not** the same value as the item's `libraryItemId` (the item JSON also carries an ebook `bookId` field).
- `GET /public/session/{sid}/track/0` → 404, `track/1` → **302** to `/hls/{sid}/output.m3u8` (root-relative), `track/2` → 404. The redirect appears **immediately**, even while the playlist does not exist.
- `GET /hls/{sid}/output.m3u8` → 404 at first (25s in), then 200 `application/vnd.apple.mpegurl`: VOD, `TARGETDURATION 6`, **5291 segments** (`output-0.ts`…`output-5290.ts`, 136606 bytes). Minutes later a **new** session for the same book was ready in 0.2s (warm transcode cache) — so the earlier "book HLS is broken" conclusion was wrong; it was a cold-start race. Any client (or gateway) must tolerate tens of seconds before the playlist exists.
- `GET /hls/{sid}/output-0.ts` → 200 `video/mp2t` 44932 bytes, and `Range: bytes=0-1023` → 206 + `Content-Range`. `output-1.m3u8`/`index.m3u8` do not exist.
- `POST /api/items/{podcastId}/play` with **no** episode id → session + 302,
  but the playlist **never** appears (404 for a full 120s poll): a podcast
  session is only playable with an episode id, which is exactly what BUG-26
  fixed.

## Skylight chore completions: timed instances need `instance_time` (2026-10-05)

Cross-checked against two community references — `joshuaswarren/pyskylight`
(`client.py`) and `chrischall/skylight-mcp` (`src/tools/chores.ts`) — then
verified live from inside the execution container.

**Contract** (both references agree):

- `PUT /api/frames/{frame}/chores/{series}/completions` — the path takes the
  numeric series id, not the instance id.
- Body: `{status: "complete" | "pending", instance_date: "YYYY-MM-DD", instance_time: "HH:MM"}`.
  `status` is `complete`/`pending` (not `completed`).
- `instance_date` is **date-only**. For a time-of-day routine (recurrence set
  with `BYHOUR`, e.g. `RRULE:FREQ=DAILY;INTERVAL=1;BYHOUR=20`) the time must
  go in its own `instance_time` field — omitting it fails with
  **422 `{"errors":{"instance_time":["can't be blank"]}}`**.
- `category_id` must be omitted for a normally-assigned chore (422 "must be
  blank" otherwise); it is only for up-for-grabs chores.
- Our bodies also carry an extra `id` field (the two references omit it); the
  API tolerates it (live 200) and it is kept deliberately.

**Instance id shapes** returned by `GET /chores?after=&before=`:

- plain occurrence: `{series}-{YYYY}-{MM}-{DD}` (e.g. `52409380-2026-10-04`)
- timed routine occurrence: `{series}-{YYYY}-{MM}-{DD}-{HHMM}` (e.g.
  `46464093-2026-10-05-2000`, "Brush Teeth" at 20:00)

**The bug this replaces:** `_skylight_chore_ids` split the id naively and
returned `instance_date="2026-10-05-2000"` with no `instance_time`. Live probe
2026-10-05 (pending→pending no-op, state verified unchanged afterwards):

- old body → 422 `instance_time can't be blank` → execution maps non-2xx to
  `None` → route returns `{"status":"FAILURE","message":"Failed to uncomplete chore"}`
- corrected body (`instance_date="2026-10-05"`, `instance_time="20:00"`) →
  **200 with the updated chore JSON**

Implementation: `_skylight_chore_ids` in `services/execution/main.py` now
returns `(series, date, "HH:MM" | None)` via a strict regex; the complete and
uncomplete routes add `instance_time` only when the id carried one. Exact-body
tests live in `services/execution/tests/test_skylight_proxy.py`.
