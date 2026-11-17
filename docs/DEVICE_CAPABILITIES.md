# Device capabilities: what each integration can do, and what homehub exposes

homehub (`services/homehub`) replaced Home Assistant (HA). The app's per-kind
controls read HA-standard entities (`media_player`, `remote`, `vacuum`,
`cover`...), their HA attributes (`supported_features`, `source_list`,
`fan_speed_list`...) and call HA's services. This page lists, per integration
the household uses, what its devices can do natively (the protocol, and what
HA's integration of the same name exposes) against what homehub exposes, and
marks the gaps.

Legend: **ok** = parity with HA; **fixed** = closed by the change that added
this page; **gap** = still missing (why is given); **n/a** = the protocol
cannot do it.

Two hub-wide behaviours matter for every row:

- **Entities are created by the integration, as in HA.** The hub's registry came
  from HA's. A driver now registers the entities it owns when the registry
  lacks them (`Driver.ensure_device` / `ensure_entity` in `core.py`): an entity
  HA lost, or one a device gained since, appears without re-adding the device.
  A device another integration already has with the same MAC is joined, as HA's
  device registry merges them. An entity that exists, disabled or not, is never
  replaced. Done for: Android TV Remote, LG webOS (its remote), ESPHome (every
  node entity), WiZ (an entry with none).
- **Stale registry rows are found by the integration, removed by a person.**
  `Driver.gone_entities` reports entities an integration's own complete listing
  no longer has (Music Assistant dropped the player; an ESPHome node's firmware
  no longer has the entity). The prune dry run (`GET /hub/prune/plan`) lists
  them as `entity_gone` with the reason; "unavailable" (a TV that is off, a
  laptop that is away) is never "gone". Apply now keeps the removed rows, and
  `POST /hub/prune/restore {"plan_id"}` puts them back (`GET /hub/prune/undo`
  lists what can be restored). See `services/homehub/prune.py`.

## TVs and streamers

Every TV remote takes one standard key set in `remote.send_command`
(`media_common.STANDARD_REMOTE_KEYS`): `up down left right select back home
menu info guide exit volume_up volume_down mute channel_up channel_down power
power_on power_off input play pause play_pause stop next previous fast_forward
rewind 0-9`, beside its native names (`KEY_*`, `KEYCODE_*`, ECP names, LG
button names). Each remote publishes the keys it really has in
`command_list`, so a remote control draws only buttons that work.

| Integration | Native (protocol / HA) | homehub | Status |
|---|---|---|---|
| androidtv_remote (Android TV, Google TV, Chromecast with Google TV) | Power, every Android key, app launch, volume steps, current app; HA: media_player + remote with activities (apps) | media_player (on/off, app, volume, mute, `source_list` of apps, `select_source`), remote with every standard key incl. input (TV_INPUT) and digits, `activity_list`; both registered at setup when missing | **fixed** (keys, sources, missing entities) |
| roku | ECP keys, apps, TV inputs and tuner as apps, device info; HA: media_player, remote, diagnostics | media_player with apps and inputs as sources, remote with the standard keys it has plus ECP names, remote `activity_list` = sources; binary sensors and sensors as HA | **fixed** (activities, key names). n/a: generic input, digits and stop keys (inputs and channels are sources) |
| samsungtv (websocket 2016+, encrypted, legacy) | Keys, power off, Wake-on-LAN on, UPnP volume, app list and launch (websocket); HA: media_player + remote | media_player (TV/HDMI plus a websocket TV's installed apps as sources), remote with every standard key (input = KEY_SOURCE, digits) | **fixed** (keys, apps as sources) |
| webostv (LG) | Pointer-socket buttons, apps and inputs, sound output, power off, Wake-on-LAN; HA: media_player and `webostv.button/command`, no remote entity | media_player as HA, plus a remote entity (registered at setup) taking the standard keys and LG buttons; `webostv.*` services kept | **fixed** (remote). n/a: an input button (inputs are sources) |
| cast | Cast media, app on screen, volume; HA: media_player | as HA | ok. n/a: key presses (Cast has none; a Google TV's keys come from its Android TV Remote entry) |
| dlna_dmr | UPnP AVTransport and RenderingControl | media_player (play URL, volume, mute) | ok. n/a: keys |
| music_assistant | Every player MA knows: play, queue, group, announce | media_player per player, `music_assistant.*` services; players MA dropped are reported as gone | **fixed** (gone detection). Duplicate `_2`/`_3` entities are MA's own players for the same TV over other protocols (AirPlay, the HA-player provider, Cast); remove them in MA, or let prune list the ones MA no longer has |

## Robot vacuum

| Integration | Native / HA | homehub | Status |
|---|---|---|---|
| roborock | Start, pause, stop, dock, locate, spot, fan speeds, mop intensity and route, room (segment) cleaning, maps, consumables, totals, errors, do-not-disturb, volume; HA: vacuum, map images, current room, sensors, selects, switches, times | vacuum (`fan_speed_list`, `segments` `[{"id","name"}]`, `send_command app_segment_clean` checked against the rooms), consumable `*_time_left`, totals, status and error sensors, mop selects, DND switch and times, volume | ok |
| roborock maps | HA's image entity, refreshed while cleaning | image entities serve the current map PNG; drawn once the vacuum answers and every minute while it cleans; an image whose id names no map shows the current one | **fixed** (the images were always "unknown") |
| roborock rooms | Room mapping from the vacuum, names from the app | `segments` from the room mapping, else from the parsed map's rooms; `sensor.*_current_room` from the map | **fixed** |

Other saved maps are shown only when the library already holds their picture:
drawing one means switching the vacuum to it, which the hub never does
unasked (`select.*_selected_map` switches it).

## Plugs, switches and energy

| Integration | Native / HA | homehub | Status |
|---|---|---|---|
| matter plugs (On/Off Plug-in Unit) | OnOff, ElectricalPowerMeasurement (W, V, A), ElectricalEnergyMeasurement | switch, power/voltage/current sensors on the plug's device | ok (no Matter plug in use today; the Matter devices in use are bulbs) |
| esphome plugs (e.g. Kauf) | Whatever the firmware defines: relay, power, voltage, current, energy | every node entity, now including ones added to the firmware later | **fixed** (new firmware entities) |
| tasmota | Relays, ENERGY (power, apparent/reactive power, factor, voltage, current, today/yesterday/total) | the same over HTTP `cm?cmnd=` | ok (HA used MQTT) |
| wiz sockets and bulbs | Light, effects, power metering on some | light, effect speed, power sensor; an entry with no entities gets them | **fixed** (empty entry) |
| emporia_vue | Mains and per-circuit power and energy | per-circuit power (W/kW) and energy (kWh, daily and monthly) | ok |
| srp_energy | Utility usage | energy sensor | ok |

## Covers, doors, gates

| Integration | Native / HA | homehub | Status |
|---|---|---|---|
| esphome covers | HA shows an ESPHome `cover` natively | cover with position, tilt, stop | ok, and a cover added to a node's firmware now appears on its own |
| relay + contact sensor (no cover in the firmware) | HA: a template cover | `switch_as_x` cover with a door sensor, momentary (push button) mode and device class `garage`; Devices > Add > Switch as another device | **fixed** |

A garage opener node that exposes only a pulse relay (`switch`) and a door
contact (`binary_sensor`) has no cover in HA either. Two ways to get one:
flash a template cover into the node (`cover: - platform: template`,
`device_class: garage`, state from the contact, open/close pressing the relay
only when the door is the other way), which the hub now registers by itself;
or, without flashing, add "Switch as another device" with the relay, kind
"garage", the door sensor and "Momentary".

## Lights

| Integration | Native / HA | homehub | Status |
|---|---|---|---|
| wiz | Brightness, colour, white temperature, effects, effect speed | as HA | ok |
| matter bulbs | OnOff, Level, Color, startup behaviour, transitions, identify | light, power-on level/behaviour numbers and selects, identify buttons, firmware update | ok |
| esphome lights | Brightness, colour modes, effects, transitions | as HA | ok |
| switch_as_x | A switch shown as a light, fan, lock, siren, valve or cover | as HA | ok |

## Climate

| Integration | Native / HA | homehub | Status |
|---|---|---|---|
| homekit_controller thermostats | Every HAP characteristic: HVAC modes, target temperature or range, target humidity, fan mode, display units, vendor modes (home/sleep/away as a select), identify | climate with `hvac_modes`, `fan_modes`, temperature and humidity ranges and feature flags from the accessory, plus sensors, selects and buttons as HA | ok |

## Cameras, sirens, lights on cameras

| Integration | Native / HA | homehub | Status |
|---|---|---|---|
| reolink (through a Home Hub) | Snapshots, streams, motion and AI detection, sirens, floodlights, recordings, battery, firmware; PTZ on PTZ models | cameras (snapshot, live), motion and person/vehicle/animal sensors, sirens (duration, volume), lights, numbers, selects, switches, recordings, firmware updates | ok. PTZ is not implemented; none of the cameras in use is a PTZ model |
| mjpeg | Snapshot and MJPEG stream | as HA, plus hub motion recording | ok |
| esphome cameras | Snapshot and stream over the API | as HA, plus hub motion recording | ok |

## Sensors, weather, phones

| Integration | Status |
|---|---|
| BLE sensors (thermopro and the other parsers) | ok; a sensor out of range shows unavailable |
| nws, open_meteo | ok (`weather.get_forecasts`); a failed first fetch is retried |
| mobile_app | ok (sensors, location, notifications) |

## Setup failures seen while writing this

None was a code bug; each was a device that does not answer:

- ESPHome nodes that refuse connections or are off the network keep
  `setup_retry` and are retried. A node that moved to a new address falls
  back to `<name>.local`; a node with a new MAC (a new board) is a new device
  to adopt from Devices > Discovered.
- A Tasmota device that does not answer on HTTP keeps `setup_retry`.
- open_meteo failed once with a DNS/connection error at start and loaded on
  the retry.
- An Android TV Remote entry with no entities (fixed above) still needs the TV
  on and reachable to show a state.

## What could not be verified without operating real devices

Every fix above is covered by unit tests against fake devices. Nothing was
switched, pressed or moved on a real device while writing it: the TV key
names on each model (LG PLAY/PAUSE/STOP/GUIDE/EXIT, Roku Info as menu/guide,
Android BACK as exit), a Samsung websocket TV answering `app_list`, a garage
opener's response to the pulse timing, and a real vacuum's map, room and
current-room data all need a check on the devices.
