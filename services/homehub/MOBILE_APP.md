# Phones: the HA companion app and Jarvis

The hub speaks the HA companion app's native API, so a phone that was set up
against Home Assistant keeps reporting to the hub with no change on the phone,
once its server name reaches the hub again. Phones without the companion app
get the same sensors from Jarvis's own phone reports.

## What works

| Piece | Where | Notes |
|---|---|---|
| Webhooks (`POST /api/webhook/<id>`) | `mobile_app_webhook.py` | `register_sensor`, `update_sensor_states`, `update_location`, `update_registration`, `get_config`, `get_zones`, `render_template`, `fire_event`, `call_service`, `enable_encryption`, `stream_camera` (MJPEG path, no HLS), `scan_tag` (fires `tag_scanned`). Encrypted payloads (libsodium secretbox, the entry's `secret`, current and legacy keys) both ways |
| Registration (`POST /api/mobile_app/registrations`) | same | A user's bearer token; a new `mobile_app` config entry, device, `device_tracker`, `notify` entity; an app registering again keeps its entry and its sensors move to the new webhook id |
| Sensors | same | Unique id `<webhook_id>_<app's unique id>`, as HA's registry has them, so imported entity ids and history carry on |
| Notifications | `drivers/mobile_app.py` + manager | `notify.mobile_app_<device>` and the `notify.*` entity: the app's WebSocket channel (`mobile_app/push_notification_channel`, with confirmations), else the push relay in the registration's `app_data.push_url`, else a Jarvis notice to the phone's user. Commands (`request_location_update`, `command_*`, `clear_notification`, `TTS`) go to the app |
| Login | `ha_auth.py`, `ha_auth_api.py` | `/auth/token` (`refresh_token`, `authorization_code` with PKCE), `/auth/revoke`, `/auth/providers`, `/auth/login_flow`, `/auth/authorize` (a small login page), WS `auth/current_user` |
| Jarvis fallback | `drivers/mobile_app.py` | Tracker, `*_battery_level`, `*_steps_sensor`, `*_detected_activity` from geo when the companion app has been quiet longer than `companion_fresh_minutes` |
| Admin | `mobile_app_api.py`, gateway `phone_routes.py`, UI Devices > Phones | List phones and sensors (with their source), map a phone to a Jarvis user, add a Jarvis-only phone, remove a phone, settings |

Not here: HA's web frontend. The app's dashboard view (a web view of HA's
frontend) has nothing to show; sensors, location, notifications and the app's
own settings work. HLS camera streams and `conversation_process` are not
implemented.

## Secrets

- Refresh tokens: the importer keeps only the SHA-256 of each token value and
  its client id, sealed with `FERNET_KEY` (blob `ha_auth`). A presented token is
  hashed and compared against every stored hash.
- Passwords: HA's bcrypt hashes, sealed in the same blob. Five failures for one
  username, or thirty overall, within 15 minutes refuse further logins for a
  while (the proxies rewrite client addresses, so limiting by address would
  only ever see the proxy).
- Webhook ids and encryption secrets stay in each entry's sealed `data`. A
  non-admin's WebSocket never sees a phone sensor's full unique id (it embeds
  the webhook id). Unknown webhook ids answer 200 with nothing, as HA does;
  deleted ones 410, which makes the app register again.
- Push relays come from the phone, so the hub only calls relays on the
  `push_url_allowlist` setting (HA's public relay by default).

## Settings

`GET/PUT /hub/mobile_app/settings` (UI: Devices > Phones > Settings):

| Key | Default | Meaning |
|---|---|---|
| `external_url`, `internal_url` | empty | The URLs reported in `/api/config` |
| `companion_fresh_minutes` | 30 | How long a companion report keeps Jarvis out (the app reports at least every 15 minutes) |
| `jarvis_fallback` | true | Jarvis fills in for quiet phones |
| `auto_jarvis_phones` | true | A Jarvis-only phone for every Jarvis user that reports a location and has no phone |
| `jarvis_phone_name` | `{name}'s Phone` | Name (and so entity ids) of a Jarvis-only phone |
| `push_url_allowlist` | HA's relay | Relay URL prefixes a registration may name |

Per phone (`PATCH /hub/mobile_app/phones/<entry_id>`): `jarvis_user`, and
`jarvis_fallback: false` to keep Jarvis off a stale old install. A Jarvis user
with several phones has Jarvis's data written to the one whose companion app
reported last; with no evidence, to none.

## Reaching the hub from outside

Caddy serves `http://$HOMEHUB_EXTERNAL_HOST` on its `:80` server (host port
8080) and passes only `/api/webhook/*`, `/api/mobile_app/registrations`,
`/auth/*` and `/api/websocket` to the hub (`HOMEHUB_UPSTREAM`, default
`host.docker.internal:8123`); everything else answers 404. TLS ends on the
public reverse proxy in front of the stack, which must forward that host name
to Caddy with the `Host` header kept, the same way it forwards the Jarvis host.

1. `.env`: `HOMEHUB_EXTERNAL_HOST=<the name the apps use>`; recreate `caddy`.
2. Public reverse proxy: a site for that name, with a certificate, proxying to
   the stack's port 8080 (WebSocket upgrades allowed).
3. Public DNS: that name to the address whose port 443 reaches the public proxy.
4. LAN DNS: the same name to the public proxy's LAN address (or no override,
   if the router loops public addresses back), so phones at home get the same
   certificate.
5. Hub setting: `external_url` = `https://<that name>`.

Phones whose app has an internal URL of the old HA box use it on the home
Wi-Fi; set it to `http://<hub address>:8123` in the app, or clear it.
