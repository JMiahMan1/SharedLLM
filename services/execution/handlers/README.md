# Execution Handlers

Platform-specific media transport and announcement handlers. Each file is
isolated to prevent cross-contamination between device types.

## Handler Files

| File | Platform | Purpose |
| :--- | :--- | :--- |
| `roku.py` | Roku TVs/players | ECP launch, MA sibling delegation, transport commands |
| `android_tv.py` | Android TV / Google TV | ADB commands, media_player services |
| `webos.py` | LG WebOS TVs | media_player services, power management |
| `samsung.py` | Samsung Tizen TVs | media_player services, KEY_POWER fallbacks |
| `media.py` | All platforms | Unified routing — detects platform and delegates |
| `video.py` | All platforms | yt-dlp URL resolution and local streaming |

## Device Discovery & Profiling

Three new modules provide network-level device management:

### `device_registry.py` — Persistent IP/MAC Store
JSON file at `/data/device_registry.json` (volume-mounted as `execution_data`).
Stores per-entity: IP, MAC, hostname, integration, friendly_name, discovery method,
last_verified timestamp, and stale flags.

| Function | Purpose |
| :--- | :--- |
| `get_device(entity_id)` | Get stored device info |
| `set_device(entity_id, ip, mac, ...)` | Store/update device info |
| `invalidate_device(entity_id, reason)` | Mark IP as stale on connection failure |
| `clear_stale(entity_id)` | Remove stale flag after re-discovery |
| `find_by_ip(ip)` / `find_by_mac(mac)` | Reverse lookup |
| `needs_rediscovery(entity_id)` | Check if device needs re-scan |
| `list_devices()` | Return all registered devices |
| `remove_device(entity_id)` | Delete a device record |

### `device_discovery.py` — Multi-Strategy IP Discovery
Ordered pipeline (stops on first success):

1. **Persistent registry cache** — instant if previously discovered
2. **HA device registry** — config entries + device registry connections for MAC
3. **HA entity attributes** — some integrations expose `ip_address`/`mac_address`
4. **ARP table scan** — matches hostname to `arp -a` output
5. **mDNS resolution** — resolves `.local` hostnames derived from friendly_name
6. **SSDP broadcast** — Roku (`roku:ecp`), DLNA, Cast discovery
7. **Batched network scan** — probes known ports in groups of 30 (1s timeout)

| Function | Purpose |
| :--- | :--- |
| `discover_device(entity_id, ha_url, ha_token, device_type)` | Full pipeline |
| `bulk_scan(ha_url, ha_token, subnet)` | Scan all media_player entities |

### `device_profiler.py` — Capability Mapping
Generates complete device profiles pairing HA entity info with network data
and control method documentation.

| Function | Purpose |
| :--- | :--- |
| `profile_device(entity_id, ha_url, ha_token)` | Full profile for one device |
| `profile_all_media_devices(ha_url, ha_token)` | Profile all media_player entities |
| `build_capability_map(ha_url, ha_token)` | Friendly-name → capabilities dict |
| `resolve_by_friendly_name(cap_map, query)` | Fuzzy match "gracies tv" → entity_id + caps |

### Supported Device Types & Control Methods

See `device_profiler.py` module docstring for the full control method matrix.
Summary:

| Type | Power On | Power Off | Play Media | Transport | WOL |
|------|----------|-----------|------------|-----------|-----|
| Roku | HA turn_on, ECP home | HA turn_off, remote PowerOff | ECP launch 782875, MA delegation | ECP keypress, HA remote | ❌ |
| WebOS | HA turn_on, WOL | HA turn_off | HA play_media | HA transport, WebSocket | ✅ |
| Samsung | HA turn_on, WOL | HA turn_off, KEY_POWER | HA play_media | HA send_key, WebSocket | ✅ |
| Android TV | HA turn_on, ADB wake | HA turn_off, ADB sleep | HA play_media | ADB command, HA transport | ❌ |
| Chromecast | HA turn_on, auto-wake | HA turn_off | HA play_media, DIAL | HA transport | ❌ |
| ESPHome | HTTP API, MQTT | HTTP API, MQTT | HTTP stream | HTTP API, MQTT | ❌ |
| MQTT | MQTT publish | MQTT publish | MQTT publish | MQTT publish | ❌ |
| DLNA | HA turn_on | HA turn_off | HA play_media, DLNA URI | HA transport, DLNA SOAP | ❌ |

## Routing (`media.py`)

`media.py` detects the platform and delegates:

```python
is_roku = await roku_handler.is_roku_device(ctx.ha_url, ctx.ha_token, entity_id)
if is_roku:
    return await roku_handler.roku_play_music(...)
# ... other platforms handled separately
```

This keeps each platform's logic isolated and maintainable.

## HTTP API Endpoints

| Method | Path | Purpose |
| :--- | :--- | :--- |
| GET | `/discovery/devices` | List all registered devices |
| GET | `/discovery/devices/{entity_id}` | Get device info for entity |
| POST | `/discovery/devices/{entity_id}/refresh` | Trigger re-discovery |
| DELETE | `/discovery/devices/{entity_id}` | Remove device from registry |
| POST | `/discovery/scan` | Bulk network scan |
| GET | `/discovery/profile/{entity_id}` | Full device profile |
| GET | `/discovery/profile` | Profile all media devices |
| GET | `/discovery/control_methods` | Document all control methods |
