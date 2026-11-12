# Pairing and managing devices

What HA's "Discovered" panel, config flows and "Devices & services" did, for the
hub's admins. Code: `flows.py` (no pairing step), `pairing.py` (pairing),
`entries.py` (management), `drivers/homekit_services.py` (HomeKit mapping).
UI: Devices > New devices and Devices > Integrations, through the gateway
(`services/gateway/discovery_routes.py`, admins only).

## Adding

A discovered device's Add, or "Add by address" for one that does not announce
itself (`POST /hub/flows/{domain}`), runs the integration's flow. The answer is
one of:

- `adopted`: the config entry, devices and entities were created (HA's data
  keys and unique_id formats) and the driver started.
- `needs_input` with `needs`: fields to fill in (a key, a code, a login). With
  a `session` when a pairing is open on the device (the TV is showing a code);
  send it back as `input.session` with the code.
- `pending` with `session`, `step` and `poll_ms`: the device waits for a person
  (Allow on the TV). Ask again with `input.session` after `poll_ms`.

Sessions live in memory for 5 minutes and are bound to the device they
started on (410 when expired).

| Integration | What the admin does | Notes |
|---|---|---|
| HomeKit (IP) | the 8-digit setup code | aiohomekit; the accessory must be unpaired (in pairing mode, `sf=1`). Bridges get one device per accessory. Mapped by HAP service type: lights, switches, outlets, valves, fans, garage doors, window coverings, locks, temperature/humidity/light/battery/CO2 sensors, motion/contact/occupancy/leak/smoke/CO sensors, security systems, stateless buttons and doorbells (event entities), thermostats |
| Android TV / Google TV | the code the TV shows | androidtvremote2, with the client certificate the TVs already trust (a new one when there is none) |
| Samsung TV | Allow on the TV (2016+, pre-2014), or the PIN it shows (2014-2015) | samsungtvws: websocket token, legacy, or encrypted (`token` + `session_id`) |
| LG webOS TV | Accept on the TV | aiowebostv; the key is stored as `client_secret` |
| DLNA renderer | nothing (or the description URL by address) | |
| Reolink camera, NVR or Home Hub | its login | reolink-aio; entities from what each channel reports it supports; cameras behind a hub or NVR become devices under it |
| ESPHome Device Builder | its URL (and login) | see `ESPHOME_BUILDER.md` |

HomeKit over Bluetooth is not done: the server has an adapter, but it needs
`bleak` and the host's D-Bus inside the container.

## Managing

| Route (admin) | What |
|---|---|
| `GET /hub/entries` | Every entry with its devices, entities, state and the fields it can be reconfigured with (secrets only as "set") |
| `PATCH /hub/entries/{id}` | `{"title"?, "data"?: {...}}`: a new address or login; the entry reloads |
| `DELETE /hub/entries/{id}[?force=1]` | Unpairs (HomeKit removes our controller pairing from the accessory), then removes the entry, its entities and devices. If the accessory cannot be reached the answer is 502; `force` removes it anyway (it then needs a reset to pair again) |
| `PATCH /hub/devices/{id}` | `{"name_by_user"?, "area_id"?}` |
| `DELETE /hub/devices/{id}[?force=1]` | One device. The entry's only device takes the entry with it; a Matter device is removed from the fabric (`remove_node`) |
| `GET /hub/flows` | What can be added by address, with its fields |
| `GET /hub/esphome-builder` | The builder link, when its integration is set up |

Removing never touches automations; ones that used the entities stop finding them.
