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
| homekit_controller | `homekit.py` | aiohomekit on HA's stored pairing (ecobee, Honeywell T5) |
| roborock, emporia_vue, srp_energy | cloud drivers | Stored logins, no re-login |
| mjpeg, reolink | camera drivers | `/api/camera_proxy/<entity>` |
| open_meteo, nws, sun | `weather.py`, `sun.py` | `weather.get_forecasts` |
| mobile_app | `mobile_app.py` | Locations from geo (`/people?source=jarvis`) and `device_tracker.see`; persons, zones, `notify.mobile_app_*` sent through Jarvis notices |
| local_todo, shopping_list | `todo.py` | |

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
