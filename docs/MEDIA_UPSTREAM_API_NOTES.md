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
