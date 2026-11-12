# homehub

The household's device hub, replacing Home Assistant (HA). It serves HA's own
REST and WebSocket API on port **8123**, so every Jarvis caller that speaks HA
(about 50 call sites across execution, gateway, identity and geo) keeps working
unchanged, and it talks to the devices directly.

## How it fits

```
Jarvis services --HA API--> homehub :8123 --> drivers --> devices (LAN, cloud)
                                  |--> matter-server :5580 (loopback) --> Matter bulbs
                                  `--> music-assistant :8095 --> speakers, TVs
```

- **Same ids as HA.** The importer copies HA's registry, so entity ids, device
  ids, area ids and unique ids are unchanged, and automations keep the names
  they used.
- **Same tokens.** HA access tokens are JWTs signed with a key held by their
  refresh token. The importer copies the refresh tokens (encrypted), so every
  account's stored HA long-lived token authenticates against the hub. The
  stack's `INTERNAL_SECRET` also works as a bearer token.
- **Same URLs, redirected.** Accounts keep the HA URL they were set up with.
  `HA_URL_REDIRECTS` (`services/common/ha_redirect.py`, applied by identity and
  `services/config.py`) points that URL, and Music Assistant's old URL, at the
  hub:
  `HA_URL_REDIRECTS=https://ha.sumemail.com=http://192.168.2.205:8123,http://ha.sumemail.com:8095=http://192.168.2.205:8095`.
- **Credentials at rest** (ESPHome keys, HomeKit pairing keys, TV tokens, cloud
  logins) are Fernet-encrypted with the stack's `FERNET_KEY`. Without a key the
  hub refuses to store them.

## Layout

| File | Role |
|---|---|
| `core.py` | Hub, states, event bus, registry, service dispatch, the `Driver` base class |
| `runtime.py` | Loads the store, runs one supervised task per config entry (setup retry with backoff, polling), records history |
| `store.py` | SQLite: registry, sealed credentials, last states, history (30 days), automations |
| `api.py` | HA REST + WebSocket API, camera proxy, `/hub/*` administration |
| `auth.py` | Bearer tokens: `INTERNAL_SECRET` or HA JWTs |
| `importer.py` | Imports an HA config directory (read-only) |
| `templating.py` | HA's Jinja template API (`/api/template`, automations) |
| `automation.py` | Automations, scripts, scenes, blueprints and the nielsfaber scheduler |
| `builtin.py` | `device_tracker.see` (Jarvis's location relay), `homeassistant.update_entity`, notifications |
| `statistics.py`, `statistics_api.py`, `energy.py`, `dashboard_api.py` | HA's recorder statistics (5-minute and hourly, `statistics.db`), `recorder/*` and `energy/*` WS commands, the HA statistics importer (`POST /hub/statistics/import`), energy settings and the Rooms/Energy/Climate pages' data; see each module's docstring |
| `mobile_app_webhook.py`, `mobile_app_api.py`, `ha_auth.py`, `ha_auth_api.py` | The HA companion apps: webhooks, registration, pushes, `/auth/*` login and token refresh. See `MOBILE_APP.md` |
| `drivers/` | One module per HA integration; see `drivers/__init__.py` |

## Integrations

| HA integration | Driver | Notes |
|---|---|---|
| matter | `matter.py` | WebSocket client of `matter-server` (matter.js 1.5.5, the add-on's server, on the add-on's fabric). See `MATTER.md`. |
| wiz | `wiz.py` | UDP 38899, no library |
| esphome | `esphome.py` | aioesphomeapi, push; stale IP falls back to `<device_name>.local`; user actions become `esphome.<node>_<action>` services |
| tasmota | `tasmota.py` | HTTP `cm?cmnd=` (HA used MQTT through its own broker, now gone) |
| switch_as_x | `switch_as_x.py` | Mirrors the wrapped switch |
| cast, roku, webostv, samsungtv, androidtv_remote, dlna_dmr | media drivers | Same client identities HA used, so no TV asks to pair again |
| music_assistant | `music_assistant.py` | MA's WebSocket API; registers the `music_assistant.*` services. See `MUSIC_ASSISTANT.md`. |
| homekit_controller | `homekit.py` | aiohomekit, on HA's stored pairings or new ones (`pairing.py`); every standard HAP service mapped by type (`homekit_services.py`) |
| esphome_builder | `esphome_builder.py` | Where admins open the ESPHome Device Builder container (`ESPHOME_BUILDER.md`); no entities |
| roborock, emporia_vue, srp_energy | cloud drivers | Stored logins, no re-login |
| mjpeg, reolink | camera drivers | `/api/camera_proxy/<entity>` |
| open_meteo, nws, sun | `weather.py`, `sun.py` | `weather.get_forecasts` |
| mobile_app | `mobile_app.py` | The companion app's own webhooks first (sensors, location, pushes; `MOBILE_APP.md`), Jarvis's reports from geo when it is quiet; persons, zones, `notify.mobile_app_*` |
| local_todo, shopping_list | `todo.py` | |

## Cameras

HA's own camera routes, plus the hub's camera API for Jarvis's Cameras page
(the gateway calls it with the user's HA token; any token the hub accepts works).
The contract a camera driver follows is at the top of `drivers/mjpeg.py`.

| Route | What |
|---|---|
| `GET /api/camera_proxy/<entity>` | One JPEG (bearer or the picture's `?token=`) |
| `GET /api/camera_proxy_stream/<entity>` | Live `multipart/x-mixed-replace`: an MJPEG camera's own stream relayed; others built from snapshots (Reolink every 0.5 s, others every 1 s), stopping when the client leaves. 503 when the first frame fails |
| `GET /api/homehub/cameras` | Every camera: `entity_id`, `name`, `area`, `platform`, `state`, `recordings`, `live`, `stream` (`camera` or `snapshots`), `snapshot_interval`, and for Reolink `battery`, `sleeping`, `model` |
| `GET /api/homehub/cameras/<entity>/recordings?start=&end=` | Recordings, newest first: `file`, `start`, `end`, `duration` (s), `triggers` (`person`, `vehicle`, `animal`, `motion`...), `size`, `stream`. Default the last 24 h; at most 7 days |
| `GET /api/homehub/cameras/<entity>/recordings/play?file=` | One recording as MP4, streamed, `Range` passed on (206 with `Content-Range` when the recorder supports it). Only files a listing returned for that camera |
| `POST .../live/webrtc`, `WS .../live/ws`, `GET .../live/stream.mp4` | Real-time video through go2rtc (the `go2rtc` integration, `live_video.py`; admin settings at `/hub/live-video`). A camera go2rtc can play has `realtime` in the list |

Reolink recordings come from the Home Hub's storage (`Search`), so listing
never wakes a battery camera; a live view does, every frame. Live Reolink video
is snapshots rather than the RTSP/FLV stream, because relaying H.264 to a
browser would need ffmpeg in the image.

## New devices (discovery)

`discovery.py` replaces HA's "Discovered" panel: mDNS continuously (ESPHome,
commissionable Matter, unpaired HomeKit, Cast, Android TV Remote, Tasmota), SSDP
and a WiZ `getSystemConfig` sweep hourly and on demand. Each finding says whether
the registry already knows it; admins get one notice per new device. `flows.py`
adds one (HA's data keys and unique_ids): WiZ, Cast, Roku and Tasmota with no
input, ESPHome with its key when encrypted, Matter by setup code through the
Matter Server; HomeKit, Android TV, Samsung, LG, DLNA and Reolink pair through
`pairing.py`, and Devices > Integrations reconfigures, renames and removes
(unpairs) what is set up (see `PAIRING.md`). The Devices page's "New devices" panel drives it
through the gateway (`/api/homehub/...`).

| Route (admin) | What |
|---|---|
| `GET /hub/discovered[?new=1]` | Findings: `known`, `new`, `ignored`, `adoptable`, `needs`, `unsupported` |
| `POST /hub/discovered/scan` | SSDP + WiZ now (at most once a minute) |
| `POST /hub/discovered/{id}/adopt` | `{"name"?, "area_id"?, "input"?}` -> `status: adopted` or `needs_input` (+ `needs`); 409 already set up, 501 not supported |
| `POST /hub/discovered/{id}/ignore` | `{"ignored": false}` takes it back |
| `POST /hub/matter/commission` | `{"code", "name"?, "area_id"?}` |

**Firewall.** A stateful firewall drops the unicast replies to a multicast
M-SEARCH or a WiZ broadcast, so both scans also probe every address of the /24
(`HOMEHUB_SCAN_SUBNET` overrides it); those replies are tracked and get through.
To also accept the broadcast replies, fix the source ports
(`HOMEHUB_SSDP_PORT`, `HOMEHUB_WIZ_DISCOVERY_PORT`) and open them on the LAN zone.
`HOMEHUB_DISCOVERY=0` turns discovery off.

## Known issues

- **Garage door sensor (hardware): needs fixing.** `binary_sensor.garage_door_sensor`
  on the garage ESPHome node has read `on` continuously since at least
  2026-11-01, through every open and close (under HA too). Until it is
  repaired: nothing reports whether the door is open, the "Close Garage if
  open for more than 15 minutes" automation cannot fire, and because the opener
  is a toggle, "open the garage" closes an open door. Use
  `camera.garage_door_garage_camera` to see the door.

Not carried over: HA Assist (whisper, piper, wake word, voice satellites'
pipelines), timers and `input_*` helpers. Automations that need them load
disabled with the reason in `/hub/status`.

## Running

`docker-compose.yml` runs `homehub`, `matter-server` and `music-assistant`, all
on the host network. Images: homehub is built by CI (`build-images.yml`); the
two others are upstream images, pinned to the versions HA's add-ons ran.

State lives in `./data/homehub` (the hub), `./data/matter-server` and
`./data/music-assistant`. The HA config to import goes in
`./data/homehub-import/homeassistant` (mounted read-only at `/import`).

## Importing from HA

1. Copy HA's `/config` to `./data/homehub-import/homeassistant` on the server
   (from a backup or from the HAOS disk, mounted read-only).
2. Copy the add-ons' data: the matter.js storage (`MATTER.md`) and Music
   Assistant's data (`MUSIC_ASSISTANT.md`).
3. `POST /hub/import` with the internal secret:
   `curl -XPOST -H "Authorization: Bearer $INTERNAL_SECRET" -d '{"history_days": 10}' http://127.0.0.1:8123/hub/import`.
   The import replaces the registry (it can be repeated) and restarts every driver.
4. Check `GET /hub/status`: every config entry with its state and unavailable
   count, every automation with its status or the reason it is disabled.
5. Fix device addresses that changed without HA's discovery:
   `PATCH /hub/entries/<entry_id>/options {"host": "192.168.2.x"}` reloads that entry.

**Never run HA and the hub against the same devices at the same time.** In
particular the Matter fabric storage must have only one live controller.

## Tests

`pytest services/homehub/tests` (hermetic: fakes for every device and server).
