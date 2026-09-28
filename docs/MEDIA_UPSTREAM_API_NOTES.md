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
- **Still (VERIFY):** each command name against a live MA `/api-docs`
  (MA 2.x serves it at `http://<mass_url>/api-docs`). Requires a running
  Music Assistant instance; do this before Phase 2 ships (P2-T26 re-checks the
  list). Any name MA does not recognize gets recorded here with the date.

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
