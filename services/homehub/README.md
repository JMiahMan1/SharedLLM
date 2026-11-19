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
  `HA_URL_REDIRECTS=https://ha.example.com=http://192.0.2.205:8123,http://ha.example.com:8095=http://192.0.2.205:8095`.
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
| `helpers.py`, `helpers_api.py` | HA's helpers (toggles, numbers, texts, dropdowns, dates, buttons, counters, timers) and `/hub/helpers` |
| `automation_editor.py`, `automation_api.py`, `climate_schedule.py` | Editing automations/scripts/scenes/schedules while running (one item rebuilt at a time), run traces, the stored entity remap (`/api/homehub/automations/remap`), and thermostat programs with holds, away and an energy rule (`/api/homehub/climate`); routes in `automation_api.py`'s docstring |
| `builtin.py` | `device_tracker.see` (Jarvis's location relay), `homeassistant.update_entity`, notifications |
| `statistics.py`, `statistics_api.py`, `energy.py`, `dashboard_api.py` | HA's recorder statistics (5-minute and hourly, `statistics.db`), `recorder/*` and `energy/*` WS commands, the HA statistics importer (`POST /hub/statistics/import`), energy settings and the Rooms/Energy/Climate pages' data; see each module's docstring |
| `mobile_app_webhook.py`, `mobile_app_api.py`, `ha_auth.py`, `ha_auth_api.py` | The HA companion apps: webhooks, registration, pushes, `/auth/*` login and token refresh. See `MOBILE_APP.md` |
| `drivers/` | One module per HA integration; see `drivers/__init__.py` |

## Integrations

| HA integration | Driver | Notes |
|---|---|---|
| matter | `matter.py` | WebSocket client of `matter-server` (matter.js 1.5.5, the add-on's server, on the add-on's fabric). See `MATTER.md`. |
| wiz | `wiz.py` | UDP 38899, no library. See WiZ bulbs below. |
| esphome | `esphome.py` | aioesphomeapi, push; stale IP falls back to `<device_name>.local`, looked up by mDNS only (never DNS, see the event loop and its thread pools below); user actions become `esphome.<node>_<action>` services |
| tasmota | `tasmota.py` | HTTP `cm?cmnd=` (HA used MQTT through its own broker, now gone); relays, lights, energy |
| switch_as_x | `switch_as_x.py` | Mirrors the wrapped switch |
| group (light) | `group.py` | A light group: commands fan out to the member lights, the state follows them. See Light groups below. |
| cast, roku, webostv, samsungtv, androidtv_remote, dlna_dmr | media drivers | Same client identities HA used, so no TV asks to pair again |
| music_assistant | `music_assistant.py` | MA's WebSocket API; registers the `music_assistant.*` services. See `MUSIC_ASSISTANT.md`. |
| homekit_controller | `homekit.py` | aiohomekit, on HA's stored pairings or new ones (`pairing.py`); every standard HAP service mapped by type (`homekit_services.py`) |
| esphome_builder | `esphome_builder.py` | Where admins open the ESPHome Device Builder container (`ESPHOME_BUILDER.md`); no entities |
| roborock, emporia_vue, srp_energy | cloud drivers | Stored logins, no re-login |
| mjpeg, reolink | camera drivers | `/api/camera_proxy/<entity>` |
| open_meteo, nws, sun | `weather.py`, `sun.py` | `weather.get_forecasts` |
| mobile_app | `mobile_app.py` | The companion app's own webhooks first (sensors, location, pushes; `MOBILE_APP.md`), Jarvis's reports from geo when it is quiet; persons, zones, `notify.mobile_app_*` |
| local_todo, shopping_list | `todo.py` | |
| common_sense_media | `accounts.py` | App vetting's Common Sense Media source (docs/APP_VETTING.md); no entities. Telemetry reads it with the internal secret at `GET /hub/integrations/{domain}`. Added under Devices > Add & fix > Add by address > Cloud accounts. (The abuse.ch key is not a hub integration any more: it is in Accounts & Integrations, moved once by `abuse_ch_move.py`.) |

What each integration's devices can do natively against what the hub exposes
(and the gaps left) is in `docs/DEVICE_CAPABILITIES.md`. Drivers register the
entities they own when the registry lacks them (`Driver.ensure_entity`), and
report the ones their own listing no longer has (`Driver.gone_entities`) to the
prune dry run, which can now be undone (`POST /hub/prune/restore`).

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
| `GET/PUT /hub/camera-recording` | Admins: motion recording for cameras that cannot record themselves (below) |

go2rtc's WebRTC settings are saved into `data/go2rtc/go2rtc.yaml`, which must
exist and hold a mapping (at least `{}`): go2rtc 1.9.15 answers "no such file"
when it is missing and crashes merging into an empty one. With the WebRTC
address set to an IP (`192.0.2.10` in this example), go2rtc listens on port 8555 there
only, not on every address the server has.

Each camera in the list also has `rotation` (0, 90, 180 or 270): how far
Jarvis turns its picture, for a camera mounted on its side. Admins set it with
`PATCH /hub/entities/<camera>` `{"rotation": 90}`; it is kept in the entity's
options (`{"camera": {"rotation": 90}}`) and the video itself is untouched.

### Motion recording (`camera_recording.py`, `camera_recording_api.py`)

Reolink cameras keep their own recordings. A camera with nowhere to keep any
(an ESPHome ESP32 camera, an MJPEG camera such as a 3D printer's) can be
recorded by the hub, with the settings a Reolink camera has: sensitivity,
seconds kept before and after motion, longest clip, pictures a second, a
schedule, and how many days clips are kept. Set it up on the Cameras page
("Motion recording", admins).

- **Pictures** are single snapshots, `fps` a second, never the camera's MJPEG
  stream: an ESP32's web server serves one stream at a time, and holding it
  would leave the live view without a picture.
- **Motion** is found in the picture: each frame, shrunk to 96x72 grey, is
  compared with the previous frame and with a slowly updated background; a
  pixel moves when it differs from both (one alone flickers, or leaves a
  "ghost" after something leaves), and motion needs two frames in a row. A
  jump in the whole picture's brightness (lights, night mode) starts over
  instead of counting. A PIR or any other sensor can be added as a trigger later.
- **Clips** start `pre_s` before the motion and end `post_s` after it (or at
  `max_s`). Frames keep their real timing and become H.264 MP4 through ffmpeg
  (installed in the hub's image).
- **Turned cameras record turned:** a clip is saved with the camera's
  `rotation` (entity options) applied, so it is upright in Nextcloud too; its
  name ends `_r90` (or `_r180`, `_r270`) and the recordings list carries
  `rotation`, which the app subtracts so it never turns a clip twice.
- **Storage** is Nextcloud over WebDAV: `<folder>/<camera>/<yyyy-mm-dd>/
  <yyyymmdd-hhmmss>_<seconds>s_motion[_r<turn>].mp4` (local time), folder default
  `Jarvis/Cameras`. The account is Jarvis's own (`NEXTCLOUD_URL`,
  `NEXTCLOUD_USER`, `NEXTCLOUD_PASS`, read once when the hub starts and copied
  into the integration when chosen) or another one with an app password.
  Credentials are stored encrypted and never returned. Days older than the
  camera's retention are deleted hourly.
- A clip Nextcloud cannot take waits in `<data>/camera-recordings/pending`, is
  still listed and playable, and is uploaded on the next minute's sync.
- Clips show in the camera's Recordings tab (`recordings: true` once the camera
  is in the recorder's settings). Motion is also a `binary_sensor.<camera>_motion`
  (device class `motion`, on the camera's device) for automations.
- Battery cameras are never watched: every picture would wake them.

Reolink recordings come from the Home Hub's storage (`Search`), so listing
never wakes a battery camera; a live view does, every frame. Live Reolink video
is snapshots rather than the RTSP/FLV stream, because relaying H.264 to a
browser would need ffmpeg in the image.

## Media and speech notes

- `media_player.join` / `unjoin` group Music Assistant players (MA's `players/cmd/set_members`). A member may be
  named by its MA entity, by the entity id MA's Home Assistant player provider uses, or by another entity on the
  same device (a Cast entity and its MA twin).
- `POST /api/tts_get_url` speaks with Jarvis's voice and returns `{url, path}` (Music Assistant's text
  announcements use it). Spoken clips are served at `HOMEHUB_EXTERNAL_URL` or `http://$EXECUTION_EXTERNAL_HOST:8123`;
  with neither set, text to speech fails and says so (a loopback URL is useless to a speaker).
- `tts.speak`'s `options.voice` is a Kokoro voice (`af_heart`, or a comma blend) or a cloned voice enrolled on
  alpaca's audio server, by profile id or name. Clones are spoken through `ALPACA_WEB_URL` (`/api/audio/tts`
  with `clone`); with it unset, or naming a profile nobody enrolled, the call fails and says which.
  `GET /api/homehub/automations/voices` lists both kinds for the automation editor, with the reason a kind
  could not be listed.
- `assist_satellite.announce` on an ESPHome voice satellite plays the spoken message through the node's own
  announcement call. `start_conversation` speaks first and then listens, through the hub's voice pipeline
  (below); it is refused by name on a node that does not offer it.
- Not served: `media_player/browse_media` and `media_source/*` (no Jarvis caller uses them), `androidtv.adb_command`
  (no ADB integration), and the `/api/diagnostics/*` routes.

## Voice satellites (`voice_pipeline.py`, `voice_api.py`)

The hub does what HA's Assist did for ESPHome voice satellites: on every connection to a node whose
`voice_assistant_feature_flags` include `VOICE_ASSISTANT` it subscribes to the node's voice requests
(a reconnect subscribes again), so the node stops reporting that it cannot reach its server. One run:

1. The node hears its wake word itself and starts a run. Audio comes over the API (`API_AUDIO`) or as UDP
   to a port opened for the run. A node set to detect the wake word on the server is told the hub has no
   wake word engine (set its wake word engine location to On device).
2. The audio streams to speech to text while an energy VAD finds the end of the command: the silence from
   the satellite's "Finished speaking detection" select (relaxed 1.25 s, default 0.7 s, aggressive 0.25 s),
   the node ending its audio, or `HOMEHUB_VOICE_MAX_S`. Nothing heard in `HOMEHUB_VOICE_NO_SPEECH_S` ends the run.
3. Speech to text over Wyoming at `HOMEHUB_VOICE_STT`. Point it at execution's recognising proxy
   (`VOICE_STT_PORT`), not at Whisper: the proxy records who spoke, and the gateway answers as that
   person only on a confident match, otherwise as the household.
4. The transcript goes, unchanged, to the gateway's `/api/chat` at `HOMEHUB_VOICE_JARVIS_URL`, the path HA's
   conversation agent used, with `voice_context` (the satellite, its area, the conversation id, and what a
   `start_conversation` asked).
5. The reply is spoken with Jarvis's voice (`tts.make_clip`) and the node fetches the URL; a node with its own
   speaker (`SPEAKER`) is also sent the audio over the API. A reply that ends in a question sets
   `continue_conversation`, so the node listens again.

Each stage sends HA's event sequence (`RUN_START`, `STT_START`, `STT_VAD_START`/`END`, `STT_END`,
`INTENT_START`/`END`, `TTS_START`/`END`, `RUN_END`) so the node's lights and screen follow, and every failure
is an `ERROR` with a code and a plain message, then `RUN_END`. The `assist_satellite` entity shows
`idle`/`listening`/`processing`/`responding`, and is unavailable while the node is offline.

These are read once when the hub starts; change one and restart the hub.

| Setting | Default | What |
|---|---|---|
| `HOMEHUB_VOICE_STT` | required | Wyoming speech to text, `host:port` |
| `HOMEHUB_VOICE_JARVIS_URL` | required | The gateway's base URL (serves `/api/chat`) |
| `HOMEHUB_VOICE_LANGUAGE` | `en` | Sent to speech to text |
| `HOMEHUB_VOICE_TTS_VOICE` | the hub's TTS voice | Kokoro voice for replies |
| `HOMEHUB_VOICE_MAX_S`, `HOMEHUB_VOICE_NO_SPEECH_S` | 15, 8 | Longest command; how long to wait for speech |
| `HOMEHUB_VOICE_STT_TIMEOUT_S`, `HOMEHUB_VOICE_JARVIS_TIMEOUT_S` | 30, 90 | Per-stage limits |
| `HOMEHUB_VOICE_VAD_MIN_RMS` | 40 | Quietest 16-bit RMS that counts as speech (the room's noise floor x 2.5 usually decides; a run that heard nothing this loud still asks speech to text) |
| `HOMEHUB_VOICE_CONTINUE` | `1` | `0` never continues a conversation |
| `HOMEHUB_VOICE_INPUT_GAIN` | `auto` | Boost for the audio sent to speech to text. `auto` lifts a quiet or far voice toward a clear level (up to 16x, never clipping); a number is a fixed multiplier. The loudness gate always measures the unboosted audio. Each run's `levels` (peak, noise floor, speech threshold, boost used) show what to tune |

A missing setting fails the run with an `ERROR` naming it. Per satellite, entry option `voice_pipeline: false`
turns the pipeline off (`PATCH /hub/voice/{entry_id}` `{"enabled": false}` sets it on the live connection).
`GET /hub/voice` (admin) lists the satellites with their features, audio path, area, run count, last run
(outcome, what ended it, per-stage timings) and last error. Transcripts and replies are logged at debug
level only and never kept.

### Sentence triggers and conversation services (`conversation.py`)

An automation with `trigger: conversation` owns its sentences (HA template syntax: `[optional]`, `(a|b)`,
`{slot}` wildcards; `<rules>` load the automation disabled). The gateway's `/api/chat` asks
`POST /hub/conversation/triggers` `{"text", "satellite_id"?, "conversation_id"?}` before anything else, so
typed chat and voice satellites both reach them. Every matching automation whose conditions pass runs, with
`trigger.sentence`, `trigger.slots`, `trigger.details`, `trigger.device_id` (the satellite's device) and
`trigger.user_input`; the reply is the first `set_conversation_response` (or "Done"), waited for up to 10 s.
No match, or only failed conditions, answers `{"matched": false}` and Jarvis answers as usual.
`trigger: tag` fires on `tag_scanned` (phones send it) for its `tag_id`, and only from `device_id` when given.
`conversation.process` and `ai_task.generate_data` are answered by Jarvis at `HOMEHUB_VOICE_JARVIS_URL`;
`ai_task` with a `structure` must get back one JSON object holding the required fields, or it fails.

### Time and date sensors (`time_date.py`)

`sensor.time`, `sensor.date`, `sensor.date_time`, `sensor.date_time_utc`, `sensor.date_time_iso`,
`sensor.time_date` and `sensor.time_utc` are published on every hub at the start of each minute, in the
household time zone, with HA's state text. HA's `time_date` entries load as replaced.

## New devices (discovery)

`discovery.py` replaces HA's "Discovered" panel: mDNS continuously (ESPHome,
commissionable Matter, unpaired HomeKit, Cast, Android TV Remote, Tasmota), SSDP
and a WiZ `getSystemConfig` sweep every 15 minutes (`HOMEHUB_DISCOVERY_INTERVAL_S`, 900) and on
demand. Those sweeps also make the kernel learn every live address, so the neighbour table
the DHCP/ARP and HTTP checks read is current; an address nothing identified is fingerprinted
over HTTP (below) within a minute of appearing there. Each finding says whether
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

Discovery also reads the kernel's neighbour table after each sweep (DHCP/ARP: HA's
manifest MAC prefixes and host names find Reolink, Roborock and Tasmota, and every
finding gets its MAC), browses `_hap._udp` (HomeKit over Thread, paired over
CoAP through a Thread border router; `homekit_thread.py` says plainly when the host has no route to the
Thread network) and `_mass._tcp` (Music Assistant). A set-up device found at a new
address is listed with `moved` and offered as an update to its entry
(`POST /hub/discovered/{id}/update-address`), and admins get one notice per new address.

Devices set up by IP are also **followed by MAC on their own** (`address_follow.py`, every
minute in discovery's housekeeping): while an entry works, its device learns the MAC the
neighbour table shows at its address; when that MAC turns up at another address, every
address the entry keeps (`host`, `url`, a camera's `mjpeg_url`/`still_image_url`, in its data
or options) is rewritten, the entry reloads, and admins get one notice that it happened. A
followed device that stopped answering triggers an early sweep (at most every 10 minutes)
rather than waiting for the next scan. An extender's shared MAC never counts, and a host
name is left alone. A camera's stream and snapshot URLs can also be changed by hand
(`PATCH /hub/entries/{id}`).

Every driver can be added by hand (`GET /hub/flows`, grouped as on the network,
servers, cloud accounts and helpers; `manual_flows.py`); phones add themselves.

## Tasmota (`drivers/tasmota.py`)

**Finding them.** A stock Tasmota build does not announce itself over mDNS, and one given its own
name in its settings no longer has a `tasmota-*` DHCP host name, so neither mDNS nor the DHCP rules
see it (HA found them through MQTT discovery, on a broker that went with HA). Discovery therefore
fingerprints over HTTP every neighbour-table address whose MAC the registry does not know and that
no other protocol identified: `GET /cm?cmnd=Status 0` on port 80, 2.5 s, 16 at a time. Each new
MAC (or a known one at a new address) is asked once in the minute's housekeeping and again at every
active scan. Only Tasmota answers in these ways:

| Reply | What it is |
|---|---|
| `Server: Tasmota/<version> (<chip>)` header | Tasmota (version and chip become the finding's details and model) |
| `Status 0` JSON with `StatusFWR` and `StatusNET` | an open Tasmota: its MAC, device name and firmware |
| HTTP 401, or HTTP 200 `{"WARNING": "Need user=..."}` (older firmware) | a Tasmota with a web password: `locked` |

A finding is `tasmota:<mac>` (the same id as a DHCP host-name match, so the two merge), named
from `Status 0` or the address's DHCP host name. A locked one carries `note: "Needs its web
password"`, its `needs` asks for the password (required), and the admins' one notice per device
says so. Hosts that announce `_http._tcp` over mDNS without being anything known are
fingerprinted too. The probe never sends a password.

**Adding them.** One config entry per device (`flows.adopt_tasmota`): `unique_id` the MAC,
data `{host, mac, username: "admin", password}` (the password only when it has one), encrypted
at rest with the rest of the entry's data (`store.py`, `FERNET_KEY`) and never returned by an
API (`reconfigure` says only whether one is `set`). The add flow reads `Status 0` and registers,
with HA's unique ids: relays `{MAC}_switch_relay_{n}`, a light `{MAC}_light_light_{n}` (the last
`POWER`, as Tasmota numbers a light after the relays), energy `{MAC}_sensor_sensor_ENERGY_{Key}`.
A device already set up (by MAC on any Tasmota entry or device) is refused with 409 naming the
entry to change instead. Add by address (`POST /hub/flows/tasmota`) takes the password too.

**Lights.** The light's type is its PWM channel count (`Channel`, else the `Color` hex), as in
HA's tasmota integration: 1 dimmable (`brightness`), 2 cold/warm (`color_temp`, CT 153-500
mireds, 2000-6536 K), 3 RGB (`hs`), 4 RGBW (`hs`, `white`), 5 RGB with cold/warm (`hs`,
`color_temp`). State comes from `Dimmer`, `CT`, `HSBColor` and `Channel` (white-only channels
lit means `color_temp`/`white` mode). `light.turn_on` sends `HsbColor1`/`HsbColor2` or `CT`,
then `White`, then `Dimmer`, each reply carrying the new state; brightness 0 is `Power<n> Off`.

**Web passwords.** Tasmota's `/cm` takes its password only as `user=admin&password=...`
arguments (its documented form), so that is how the driver sends it; no log line or error text
carries the request URL (`safe_error`). A 401 or the `Need user=` reply is `AuthRequired`
(`core.py`): the entry's reason starts with it, `GET /hub/entries` gives `needs: "password"`,
and the Devices page says "Needs its web password" with an "Enter password" button instead of
"not answering". A password changed on the device later shows the same way on the next poll.

**Managing them.** Every Tasmota entry offers its address and web password for reconfiguring
(`entries.DOMAIN_FIELDS`), even one imported from HA with empty data: the address shown is the
device's `configuration_url`, and saving stores it on the entry as `host`. A new password or
address is tried on the device first and refused if the device answers that the password is
wrong; a device that does not answer is saved anyway. The same two fields are under a device's
Settings, Connection on the Devices page.

**Following them.** A Tasmota entry keeps its `host` where `address_follow.py` rewrites it when
the router moves the device (by MAC, as for every device set up by IP). An entry imported from HA
that kept its address only on the device (`configuration_url`) is followed too
(`DEVICE_URL_FOLLOWED`): the new address is written to the entry's `host` and the device's
`configuration_url`, and the entry reloads.

| Route (admin) | What (`hardware_api.py`) |
|---|---|
| `GET /hub/integrations` | Per integration: entries with state and error, device/entity/unavailable counts, how it is discovered and added |
| `GET /hub/hardware`, `GET /hub/hardware/{id}` | Devices from the live registry (maker, model, firmware, area, IP/MAC, status, last seen, abilities); one device with its entities and states |
| `POST /hub/hardware/{id}/disable`, `PATCH /hub/entities/{id}` | Disable a device (its entities follow), rename/move/hide/disable an entity |
| `POST /hub/entries/{id}/disable`, `/reload`, `/sync` | Unload or start an entry; reconnect it; register devices its driver found (Music Assistant, Roborock, Emporia) |

**Capabilities** (`capabilities.py`, `GET /api/homehub/capabilities?ability=&area=`, any
hub token): what every entity can do, derived from HA's feature flags and state
attributes, the registry, the protocol (ESPHome type, HomeKit aid/iid, Matter
endpoint) and the driver (camera stream types, an optional `entity_abilities`).
The Rooms page draws controls from it, the automation editor offers only the
services a device takes, and execution's announce routing and TV stage ask it
which integration owns a player (`services/execution/capability_index.py`).

**What a device is comes from the device, not from Home Assistant.** The
import is a source of history, statistics, areas, the names people gave, which
devices to reconnect to and their credentials. It is never the source of what a
device is or can do. Each registry record keeps `confirmed`: the info fields
(`ENTITY_INFO_FIELDS`, `DEVICE_INFO_FIELDS` in `core.py`) that a driver or the
flow that probed the device set, plus `state` once a driver published it.

- A driver states what its device said with `confirm_device` / `confirm_entity`;
  a published state that carries feature flags, a device class, a unit or HA's
  capability attributes (colour modes, ranges, options, sources...) confirms
  them too (`Hub.publish`). Either overwrites the imported value.
- The registry's values reach a state only when confirmed or chosen by a
  person (`device_class`, `icon`, a display unit in `options.sensor`);
  `capabilities.py`, the Rooms page, statistics and the automation editor read
  only confirmed values (`EntityEntry.trusted`). Restored attributes of an
  entity no driver has published are ignored.
- What only the import holds is shown, never used: `info_source`
  (`device` / `imported`) on registry records and on `/hub/hardware`, and
  `confirmed`, `source`, `imported_only` and `protocol.registry_imported` on the
  capability index. `GET /api/homehub/capabilities/provenance` counts them.
- Where a protocol cannot tell something, it stays unset: an MJPEG stream
  reports no maker, model or MAC; Cast reports no firmware or MAC; SRP is a
  utility account with no device. Helpers (sun, weather, to-do, switch_as_x,
  go2rtc) describe themselves.
- A hub that never imported anything works the same way: `tests/test_fresh_hub.py`
  finds WiZ, ESPHome, Cast, Roku and HomeKit devices, adds them with an MJPEG
  camera and an Emporia account, and drives them with an empty store. Areas
  are made with `GET|POST /hub/areas`, `PATCH|DELETE /hub/areas/{id}`.
- In the web app a parent adds, renames and removes rooms in two places: the
  Rooms page (**Add room** in its header, or in the empty state when there are
  no rooms yet; **Rename** and remove on each room's card) and Devices >
  **Rooms & places**, which also manages floors. Both use the same code
  (`services/ui/src/hooks/useRoomActions.ts`, `components/devices/RoomForms.tsx`)
  and refresh each other's lists. A parent sees a new room on the Rooms page at
  once, even before it has any devices; everyone else sees a room once it has
  something in it. Only admins see these controls, and the gateway
  (`hub_registry_routes.py`, 403) and the hub (`/hub/areas`, 401) refuse
  everyone else. Removing a room keeps its devices working, with no room.
- Floors, labels, zones and persons are edited the same way (`/hub/floors`,
  `/hub/labels`, `/hub/zones`, `/hub/persons`; `registries_api.py`). An area
  picks a floor; areas, devices and entities carry `labels`. A zone is
  published as `zone.*` for zone triggers; a rename keeps its entity id.
  Zones are every place the family has: HomeHub is the only store of places
  (see "Places" below). A person
  links a Jarvis user (`jarvis_user`) and its `device_trackers`. The import
  also keeps HA's categories and tags (`GET /hub/categories`, `/hub/tags`).

The capability index also lists the live areas (`areas`), gives voice satellites
`announce` (and `converse` only when the device publishes it and the hub can
run it: an ESPHome satellite whose voice pipeline is subscribed and that has
`START_CONVERSATION`),
and gives a Music Assistant player with no area of its own the area of the
player it drives, as `area_source: "inherited"` (never written to the registry).

**Firewall.** A stateful firewall drops the unicast replies to a multicast
M-SEARCH or a WiZ broadcast, so both scans also probe every address of the /24
(`HOMEHUB_SCAN_SUBNET` overrides it); those replies are tracked and get through.
To also accept the broadcast replies, fix the source ports
(`HOMEHUB_SSDP_PORT`, `HOMEHUB_WIZ_DISCOVERY_PORT`) and open them on the LAN zone.
`HOMEHUB_DISCOVERY=0` turns discovery off.

## Places (`registries.py`, `/hub/zones`)

HomeHub is the only store of the family's places. Geo, the gateway, the UI and Jarvis read every place
from here; nothing else keeps a copy.

- **Home** is the hub's own location: the core config (`latitude`, `longitude`, `radius`,
  `location_name`, as HA's `core.config`). An admin moves, resizes or renames it with
  `PATCH /hub/zones/home` (Devices > Rooms & places > Zones > Move Home), or HA's websocket
  `config/core/update`. It is saved at once and survives a restart; `zone.home`, `/api/config`, the sun,
  the weather integration, the trackers' `home` and Jarvis's forecast all follow it. Home is never
  removed (`DELETE /hub/zones/home` is a 409). Its place settings (`important`, `notify`, `pin`) are in
  the kv key `home_place`. The time zone is not changed here: it is the household setting.
- **Zones** drawn on Devices > Rooms & places or imported from Home Assistant, and **places saved in
  Wander**, are one list (kv `zones`). Each may carry `important` (arriving or leaving sends a
  `family_safety` notice), `notify` (Jarvis usernames; empty means everyone), `pin` (the map icon),
  and an owner: `owner` is a hub user id, `owner_name` the Jarvis username Jarvis's gateway named.
  `visibility` is `family` or `private`.
- **Owners are the hub's own users.** A hub login (a standalone hub, no Jarvis) owns what it creates
  and sees its own private places. Jarvis's gateway acts for a person with the internal secret and
  `?viewer=<jarvis username>` (and `owner` in a new place); the hub matches that name to its own user
  (a hub user carrying `jarvis_user`, as users synced from Jarvis do, else a person linking the name to
  a hub user) and keeps the name too, so a place made before that hub user existed stays its owner's.
  The hub never asks Jarvis anything for this.
- **A private place is its owner's alone.** It is never a `zone.*` state and has no entity registry
  row, so `/api/states`, the websocket (`get_states`, `subscribe_entities`), templates, automations
  and the HA frontend never see it, and no tracker shows its name (it is not in the zone table). Only
  the owner (their hub login, or the gateway acting for them) lists or changes it; an admin does not
  see it either. Geo reads it (`?all=1`, internal secret only) for the owner's own arrivals and
  reminders, as `place.<id>`. HA has no per-user state, so leaving it out of the state machine
  entirely is the only way it stays private there.
- **The old mirror is gone.** The read-only `source: geo` zones (`geo_zones`, followed every
  `HOMEHUB_GEO_PLACES_EVERY_S`, `POST /hub/zones/sync`) became ordinary zones at the first start of this
  version (`adopt_mirrored_places`: same id as the place, same `zone.*` entity id). Geo then moves the
  rest of its places in with `POST /hub/zones/import` (internal secret only; idempotent; keeps ids,
  fills only what a zone is missing, so a change made on the hub since is never overwritten).
- With Home Assistant on, its zones can still be imported (`/hub/import`), but they become hub zones:
  HomeHub remains the store.

## Bluetooth

One advertisement bus (`bluetooth.py`) fed by the host's adapter and by ESPHome
Bluetooth proxies, used for BLE sensors and for room presence. Admin API in
`bluetooth_api.py` (`/hub/bluetooth`, `/hub/bluetooth/devices`,
`PATCH /hub/bluetooth/scanners/{id}`, `/hub/bluetooth/map...`).

**Host adapter.** `HOMEHUB_BLUETOOTH_ADAPTER` names it by address
(`AA:BB:CC:DD:EE:FF`, preferred: `hciN` numbers follow USB enumeration and can
swap across reboots) or as `hciN`. Unset, the host adapter is off and
`GET /hub/bluetooth` says why. Use an adapter no other program owns: the Matter
server takes its commissioning adapter over raw HCI, so give the hub the other
one. `HOMEHUB_BLUETOOTH_SCAN_MODE`: `auto` (default; passive when bluetoothd
runs with `--experimental` and so offers advertisement monitors, otherwise
active, LE only, every advertisement), `active`, or `passive` (refused, with
the reason, when BlueZ cannot). The container needs:

- `/run/dbus/system_bus_socket` mounted (BlueZ is reached over D-Bus; the hub
  runs as a normal user, which BlueZ's default D-Bus policy allows to scan);
- on an SELinux host (Fedora), `security_opt: [label=disable]`: the policy
  denies `container_t` the system bus socket (checked: `EACCES` with the default
  label, a scan works with `label=disable`). `docker-compose.yml` sets both.

A scanner that hears nothing for 5 minutes is restarted; errors and the scan
mode are on `GET /hub/bluetooth`.

**ESPHome proxies.** A node whose firmware has `bluetooth_proxy:` reports it in
`device_info`, and the ESPHome driver then subscribes to its advertisements (raw
when the node offers them). Nothing on the node is changed. The node's device
area is its scanner's area unless one is set with the PATCH route. To make a node
a proxy, add to its YAML and flash it from the ESPHome Device Builder:

```yaml
esp32_ble_tracker:
  scan_parameters:
    active: true
bluetooth_proxy:
  active: true
```

**BLE sensors** (`bluetooth_parsers.py`): the Bluetooth-Devices parser libraries
HA uses (BTHome, Xiaomi, Qingping, SensorPush, ThermoPro, INKBIRD, Ruuvi, Govee;
versions pinned in `requirements.txt`). A sensor a parser understands is listed
under New devices with what the parser says it is; adding it asks for the
bindkey when the parser sees encryption, and checks that the key decrypts.
`drivers/ble.py` publishes every reading as it arrives and registers readings
that appear later. The bindkey is sealed in the entry's data; no API returns it.

**Room presence** (`bluetooth_presence.py`). Tracked beacons are iBeacons
(`<uuid>-<major>-<minor>`):

- a phone's beacon, sent by the companion app as `app_data.ble_beacon`
  (`update_registration` or `register`) or by the gateway for a Jarvis phone
  (`PATCH /hub/mobile_app/phones/{entry_id}` `{"app_data": {"ble_beacon": {...} | null}}`);
  `null` means the beacon was turned off on the phone;
- an "iBeacon tracker" added by an admin (from New devices or by hand).

Each gets `sensor.<name>_room` (the area name, `not_home`, or `unknown`; attributes
`area_id`, `distance_m`, `rssi`, `scanner`, `scanners_heard`, `last_seen`,
`source: ble`, `method`, `confidence`) and `sensor.<name>_room_distance` (m). A
phone's room sensors use the phone's unique-id prefix (`<prefix>_ble_room`).
`person.*` entities with such a phone carry `room` and `room_area_id`, read
from the room sensor whenever the person is published (not stored); the
capability index reports `presence_room` for both.

The estimate: per scanner, an exponential moving average of RSSI (alpha 0.35)
over readings from the last 20 s, from scanners that heard anything in the last
60 s. Until two rooms are mapped, the room is the area of the loudest scanner
(after its `rssi_offset`); with a map, the RSSI vector is scored against each
mapped room (per scanner, a Gaussian over RSSI and the probability of being
heard at all; a scanner that has gone offline is left out), and the tempered
posterior is the confidence. An unmapped room wins only through its own scanner
when that is 8 dB louder than every other room's and at least -75 dBm. A new
room must hold for 12 s before the sensor switches; 180 s unheard is
`not_home`. Distance: `10 ** ((P - rssi) / 20)` with `P` the tracker's
`tx_power`, else the beacon's advertised power, else the scanner's `ref_power`,
else -59 dBm.

**The room map.** `POST /hub/bluetooth/map/sessions {"area_id", "tracker", "duration_s"}`
records the tracker in a room (one sample of every online scanner per ~2 s
window); sessions are stored (`bluetooth_map` in the store's kv) and survive
restarts. `GET /hub/bluetooth/map` gives each room's samples and sessions,
leave-one-session-out accuracy (a room needs two sessions), the confusion
matrix, and `stale_reason` when a scanner was added, moved to another area or
went quiet after mapping (map the rooms again).

## Light groups (`light_groups.py`, `drivers/group.py`)

HA's group helper of type light: several lights that switch as one. Each group is a config entry
(domain `group`, HA's options shape `{group_type: light, entities, hide_members, all}`, so an
imported HA light group is the same thing) with one `light` entity whose unique id is the entry id.
The entity has no device: its name and room are its own, so it shows in its room like any light and
schedules, automations and voice ("turn off the kitchen lights") can name it.

- **Commands fan out.** `light.turn_on` / `turn_off` (with brightness, colour, transition) become one
  call on the members that can be reached; `toggle` turns them all off while any is on. One member
  that does not answer does not stop the others.
- **The state follows the members**: `on` while any member is on, `off` otherwise, `unavailable`
  only when none can be reached. Brightness and colour temperature are averaged over the members that
  are on; colour modes, features and effects are what they offer together. The `entity_id` attribute
  lists the members (HA's), which is how Jarvis's fast path finds a group's lights.
- **Members** are lights only, never another group (a command cannot loop). A member deleted later is
  skipped and listed as missing until the group is edited; one hidden as a duplicate copy is still
  a member and still switched.
- `GET|POST /hub/light_groups`, `PATCH|DELETE /hub/light_groups/{entry_id}` (`light_groups_api.py`),
  admins; the gateway lets parents in (`/api/homehub/light_groups`, Devices > Your devices > Light
  groups, and Admin > Device Groups > Light Groups shows the same panel).
- Admin's old "light clusters" (identity `light_cluster:*`) were names for lights that nothing could
  switch. `light_cluster_move.py` makes a light group of each one once (its lights in the hub, its
  room when the hub has it), deletes the cluster, and records what it did in the store's
  `light_clusters_moved` marker; identity now refuses new clusters (410). Light patterns apply to a
  light group's members.

## Duplicate devices (`duplicates.py`)

The same bulb is often in the hub twice, through Matter and through WiZ. Deleting the Matter copy
would take it off the fabric, so a person hides one copy instead (Devices > Your devices >
Duplicates). A hidden device (`DeviceEntry.hidden_by = "user"`; its entities get
`hidden_by: "device"`) keeps working: states update, automations and a service call naming it or
its device still run. It is left out of the rooms dashboard, the voice exposure list,
`/api/homehub/hidden_entities` consumers (execution's `/discovery/entities`, so Jarvis's entity
lists, fast-path cache and RAG sync), capability-index room lookups and a service call aimed at a
whole area. It survives restarts, resyncs and a re-import of Home Assistant.

Duplicates are devices of different integrations with the same real MAC or the same serial
number, or (when the copies do not both report one) the same name in the same area; one
integration's devices are never grouped, and two different MACs or serials are never one device.
An IP address is never evidence: DHCP hands it to the next device. A WiZ bulb reports its MAC
itself; a Matter node reports its Wi-Fi MAC in General Diagnostics' NetworkInterfaces (`0/51/0`,
field 4), which the Matter driver now confirms onto its device (`node_macs`), so a bulb paired to
the fabric and found on the LAN pairs on its MAC whatever each copy is called (the bathroom bulbs:
"Upstairs Bathroom 1" on Matter is "WiZ Tunable White ..." on WiZ). Discovery does not index a
Matter device's MAC, so an address change found on the LAN still goes to the LAN integration.
`GET /hub/devices/duplicates`, `PATCH /hub/devices/{id}` `{"hidden"}`,
`POST /hub/devices/duplicates/hide` `{"hide_domain"?, "keep_domain"?}` and
`POST /hub/devices/duplicates/unhide` `{"domain"}`, admins only.

### Combining duplicates: a main and a fallback

Instead of hiding a copy, two copies can be combined into one device (Devices > Duplicates >
Combine). A person picks the **main**; the other copy is kept as its **fallback**
(`DeviceEntry.hidden_by = "combined"`, its entities `hidden_by: "device"`).

- **One device.** The main keeps its entity ids. Each controllable entity (light, switch, fan,
  cover, lock, climate, media player, valve, humidifier, vacuum, water heater, siren) of the main
  is paired with the fallback's entity of the same kind when each copy has exactly one; sensors and
  diagnostics stay each copy's own. A name or room only one copy has is carried over to the main
  (device name, area, labels; entity labels and aliases), and an entity named only "Light" takes
  the device's name, so nothing loses its name or its room.
- **Commands** to either copy's id (by entity, device, area, a light group or `homeassistant.*`) go
  to the main once. If it fails (an error, `DeviceUnavailable`, or no answer within
  `COMBINED_MAIN_TIMEOUT_S`, 6 s) the same command goes to the fallback and the hub logs
  `light.turn_on on light.x used fallback light.y (reason)` at INFO. A main whose own state is
  unavailable while the fallback's is not is skipped straight away. Only when both fail does the
  call fail, naming both reasons. This lives in `Hub._dispatch_entities` (`_call_combined`), so
  voice, the app, schedules, automations, light groups and the HA-compatible REST and websocket
  APIs all get it.
- **State.** `Hub.copy_states` keeps each copy's own last state; both ids show one shared state:
  the main's while it is available, else the fallback's, so the combined device is unavailable
  only when both copies are. After the fallback carried out a command, its state is shown until
  the main publishes again (the main may still report the state from before it stopped answering).
- **Choosing.** Per pair (`{"main", "fallback"}`), swap (`/swap`), and a household rule per pair of
  integrations ("Prefer Matter, fall back to WiZ"): `POST .../combine` `{"main_domain",
  "fallback_domain"}` combines every uncombined pair of those two integrations that way and saves
  the rule; `{"group"}` alone uses the rule; `POST .../rule` `{"clear": [a, b]}` forgets it. A pair
  is never combined without someone choosing the main (or the rule saying so).
- **Uncombine** (`POST .../uncombine` `{"device"}`) puts both copies back as they were: hidden flags,
  names, rooms and labels it changed (a field a person changed since is left as they set it), and
  each id shows its own state again. A combined copy cannot be hidden or shown on its own (409), and
  the bulk hide/show leaves combined groups alone. Combinations live in the store (`kv`
  `combined_devices`, rules in `duplicate_rules`) and are in place before any driver publishes at
  start; one whose device was removed is dropped.

| Route (admins) | Body |
|---|---|
| `POST /hub/devices/duplicates/combine` | `{"main","fallback"}`, `{"group","main_domain"?}` or `{"main_domain","fallback_domain"}` |
| `POST /hub/devices/duplicates/uncombine` | `{"device"}` (either copy) |
| `POST /hub/devices/duplicates/swap` | `{"device"}` (either copy) |
| `POST /hub/devices/duplicates/rule` | `{"main_domain","fallback_domain"}` or `{"clear": [a, b]}` |

The gateway passes them through at `/api/homehub/devices/duplicates/{combine,uncombine,swap,rule}`
(`discovery_routes.py`, each action's own fields only).

## WiZ bulbs (`drivers/wiz.py`)

- **Its own socket, no resolver.** Each request opens a UDP socket itself and hands it to
  `create_datagram_endpoint(sock=...)`. Passing `remote_addr` instead made uvloop resolve the bulb's
  numeric address on libuv's thread pool, where it queued behind slow DNS lookups (see below) and timed
  out after 3 s: working bulbs were logged `unreachable: TimeoutError` and flicked to unavailable.
- **Unavailable takes two missed polls.** A poll that gets no reply asks once more a second later; a
  bulb is marked unavailable only after `MISSES_BEFORE_UNAVAILABLE` (2) such polls in a row (about
  20 s for a bulb switched off at the wall). Until then it keeps its last state and is polled every
  5 s. A request the hub could not send at all (`WizTransportError`) never counts against the bulb.
- **A command always goes to the bulb.** A bulb shown unavailable is sent `setPilot` straight away (no
  `getPilot` first); if it answers it is online again. Each command has `COMMAND_BUDGET_S` (5 s) in
  all, with resends every 0.6 s, and the read-back after it at most 1.5 s, so one silent bulb cannot
  hold a room's reply. A room is one service call with several entities, and the hub sends every
  entity's command at once (`Hub._dispatch_entities`); execution's `/execute/lights` also fans out.

## The event loop and its thread pools (`loop_monitor.py`)

Everything runs on one asyncio loop (uvloop, under uvicorn) with two thread pools behind it: the
default executor (`asyncio.to_thread`) and libuv's pool, which uvloop uses for every `getaddrinfo`
and for the address of every `create_datagram_endpoint(remote_addr=...)`, even a numeric one.
`main.py` raises libuv's pool from 4 threads to 16 (`UV_THREADPOOL_SIZE`, before anything starts it).

What happened on 2026-11-18: seven ESPHome nodes were offline, and for each reconnect aioesphomeapi
looked up `x.local` and also the bare name `x` through `getaddrinfo`. On the server a single-label
name takes about 4 s to fail, and a cancelled lookup still holds its thread until then, so the pool's
four threads were busy for 4 to 8 s at a time (seen in `/proc` as libuv workers waiting on a DNS reply
for `kauf-plug-d67371`, `water-valve`, `jarvis-cyd2`...). The loop itself was fine (`/health` never
took 0.4 s over 7 minutes). Fixed three ways: WiZ no longer uses the pool, ESPHome resolves `.local`
by mDNS itself and hands aioesphomeapi an address, and the pool is larger.

The monitor makes the next one visible. It logs a WARNING (logger `homehub.loop`, at most one per kind
a minute, with a count of the ones held back) when:

- the loop wakes more than 0.5 s late: the warning includes the loop thread's stack, taken by a
  watchdog thread while the loop was still stuck, so it names the blocking code;
- a no-op job waits more than 1 s in the default executor or in the resolver pool (probed every 30 s;
  the resolver probe opens one UDP socket to 127.0.0.1, which under uvloop is exactly one pool job).

`GET /health` carries the counts under `loop` (`stalls`, `worst_stall_s`, `last_stall`, `pool_waits`
and the last probe times in `pools_s`).

## Known issues

- **A door sensor stuck on one value is a hardware fault, not a hub one.** A
  contact sensor that reads `on` through every open and close (under HA too)
  leaves nothing reporting whether the door is open: an automation that waits for
  it to be open for a while never fires, and when the opener is a toggle, "open
  the garage" closes an open door. Repair the sensor; until then a camera on the
  door is the way to see it.

Not carried over: HA Assist (whisper, piper, wake word, voice satellites'
pipelines).

Helpers (`input_boolean`, `input_number`, `input_text`, `input_select`,
`input_datetime`, `input_button`, `counter`, `timer`) are kept by the hub
(`helpers.py`, routes in `helpers_api.py`): HA's services, states and timer
events, restored on restart, imported from HA's `.storage` files and managed
under Automations > Helpers in the app. An automation that names a helper which
does not exist loads disabled and says which one; creating it reloads the
automations so it loads.

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
   `PATCH /hub/entries/<entry_id>/options {"host": "192.0.2.x"}` reloads that entry.

**Never run HA and the hub against the same devices at the same time.** In
particular the Matter fabric storage must have only one live controller.

## Tests

`pytest services/homehub/tests` (hermetic: fakes for every device and server).
