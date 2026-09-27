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
