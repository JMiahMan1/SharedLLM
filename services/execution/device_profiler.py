# services/execution/device_profiler.py
"""
Device Profiler — Network scan + HA pairing + control capability mapping.

Scans the local network, pairs discovered devices with Home Assistant entities,
and generates a profile for each device detailing:
- Network info (IP, MAC, hostname, open ports)
- HA entity info (entity_id, friendly_name, integration, device_class, area)
- Available control methods (WOL, ECP, ADB, HTTP API, etc.)
- Recommended actions for power on/off, media playback, transport

## Supported Device Types & Control Methods

### Roku (roku)
| Method | Port | Protocol | Power On | Power Off | Play Media | Transport |
|--------|------|----------|----------|-----------|------------|-----------|
| ECP | 8060 | HTTP POST | ✅ turn_on | ❌ (use Home key) | ✅ launch/782875 | ✅ keypress/Play |
| HA roku domain | - | REST API | ✅ media_player.turn_on | ❌ | ✅ roku.launch | ✅ roku.press |
| HA remote domain | - | REST API | ❌ | ✅ remote.send_command PowerOff | ❌ | ✅ remote.send_command |

### WebOS (webostv)
| Method | Port | Protocol | Power On | Power Off | Play Media | Transport |
|--------|------|----------|----------|-----------|------------|-----------|
| WOL | - | Magic Packet | ✅ (needs MAC) | ❌ | ❌ | ❌ |
| HA webostv domain | - | REST API | ✅ media_player.turn_on | ✅ media_player.turn_off | ✅ media_player.play_media | ✅ media_player.media_play/pause |
| Direct HTTP | 3000 | HTTP/WS | ❌ | ❌ | ❌ | ✅ (WebSocket commands) |

### Samsung Tizen (samsungtv)
| Method | Port | Protocol | Power On | Power Off | Play Media | Transport |
|--------|------|----------|----------|-----------|------------|-----------|
| WOL | - | Magic Packet | ✅ (needs MAC) | ❌ | ❌ | ❌ |
| HA samsungtv domain | - | REST API | ✅ media_player.turn_on | ✅ media_player.turn_off | ✅ media_player.play_media | ✅ samsungtv.send_key |
| Direct HTTP | 8001/8002 | HTTP/WS | ❌ | ❌ | ❌ | ✅ (WebSocket remote control) |

### Android TV (androidtv)
| Method | Port | Protocol | Power On | Power Off | Play Media | Transport |
|--------|------|----------|----------|-----------|------------|-----------|
| ADB | 5555 | TCP | ✅ (via HA) | ✅ (via HA) | ✅ media_player.play_media | ✅ androidtv.adb_command |
| HA androidtv domain | - | REST API | ✅ media_player.turn_on | ✅ media_player.turn_off | ✅ media_player.play_media | ✅ androidtv.adb_command |

### Chromecast (cast)
| Method | Port | Protocol | Power On | Power Off | Play Media | Transport |
|--------|------|----------|----------|-----------|------------|-----------|
| DIAL | 8009 | HTTP/REST | ✅ (auto-wake) | ❌ | ✅ media_player.play_media | ✅ media_player.media_play/pause |
| HA cast domain | - | REST API | ✅ media_player.turn_on | ❌ | ✅ media_player.play_media | ✅ media_player.media_play/pause |

### ESPHome (esphome)
| Method | Port | Protocol | Power On | Power Off | Control | Notes |
|--------|------|----------|----------|-----------|---------|-------|
| HTTP API | 80 | HTTP GET | ✅ (via relay) | ✅ (via relay) | ✅ POST /switch | Depends on firmware config |
| ESP32-CAM | 8080 | HTTP GET | ❌ | ❌ | ✅ GET /stream | Video stream only |

### MQTT Devices (mqtt)
| Method | Port | Protocol | Power On | Power Off | Control | Notes |
|--------|------|----------|----------|-----------|---------|-------|
| MQTT Broker | 1883 | MQTT | ✅ (via topic) | ✅ (via topic) | ✅ publish/subscribe | Depends on device firmware |
| MQTT TLS | 8883 | MQTT/TLS | ✅ (via topic) | ✅ (via topic) | ✅ publish/subscribe | Encrypted variant |

### DLNA (dlna_dmr)
| Method | Port | Protocol | Power On | Power Off | Play Media | Transport |
|--------|------|----------|----------|-----------|------------|-----------|
| DLNA/UPnP | 9197/8200 | SOAP/HTTP | ❌ | ❌ | ✅ SetAVTransportURI | ✅ Play/Pause/Stop |
| HA dlna_dmr domain | - | REST API | ❌ | ❌ | ✅ media_player.play_media | ✅ media_player.media_play/pause |
"""
import logging
import socket
import asyncio

import device_discovery
import ha_client

log = logging.getLogger("execution.profiler")

# Control method definitions per device type
CONTROL_METHODS = {
    "roku": {
        "power_on": ["ha_turn_on", "ecp_home_key"],
        "power_off": ["ha_turn_off", "remote_power_off", "remote_send_home"],
        "play_media": ["ecp_launch", "ma_delegation"],
        "transport": ["ecp_keypress", "ha_remote_send"],
        "ports": [8060],
        "wol_supported": False,
        "description": "Roku TV/streaming player. Uses ECP (External Control Protocol) on port 8060.",
    },
    "webostv": {
        "power_on": ["ha_turn_on", "wol"],
        "power_off": ["ha_turn_off"],
        "play_media": ["ha_play_media"],
        "transport": ["ha_transport", "websocket"],
        "ports": [3000, 7676],
        "wol_supported": True,
        "description": "LG WebOS TV. Requires MAC for WOL. WebSocket API on port 3000.",
    },
    "samsungtv": {
        "power_on": ["ha_turn_on", "wol"],
        "power_off": ["ha_turn_off", "send_key_power"],
        "play_media": ["ha_play_media"],
        "transport": ["ha_send_key", "websocket"],
        "ports": [8001, 8002],
        "wol_supported": True,
        "description": "Samsung Tizen TV. Requires MAC for WOL. WebSocket API on port 8001/8002.",
    },
    "androidtv": {
        "power_on": ["ha_turn_on", "adb_wake"],
        "power_off": ["ha_turn_off", "adb_sleep"],
        "play_media": ["ha_play_media"],
        "transport": ["adb_command", "ha_transport"],
        "ports": [5555],
        "wol_supported": False,
        "description": "Android TV / Google TV. ADB on port 5555 for deep control.",
    },
    "cast": {
        "power_on": ["ha_turn_on", "auto_wake"],
        "power_off": ["ha_turn_off"],
        "play_media": ["ha_play_media", "dial_launch"],
        "transport": ["ha_transport"],
        "ports": [8009],
        "wol_supported": False,
        "description": "Chromecast / Cast-enabled device. DIAL protocol on port 8009.",
    },
    "esphome": {
        "power_on": ["http_api", "mqtt_topic"],
        "power_off": ["http_api", "mqtt_topic"],
        "play_media": ["http_stream"],
        "transport": ["http_api", "mqtt_topic"],
        "ports": [80, 8080],
        "wol_supported": False,
        "description": "ESPHome device. HTTP API on port 80, ESP32-CAM stream on 8080.",
    },
    "mqtt": {
        "power_on": ["mqtt_publish"],
        "power_off": ["mqtt_publish"],
        "play_media": ["mqtt_publish"],
        "transport": ["mqtt_publish"],
        "ports": [1883, 8883],
        "wol_supported": False,
        "description": "MQTT device. Control via publish/subscribe on broker.",
    },
    "dlna": {
        "power_on": ["ha_turn_on"],
        "power_off": ["ha_turn_off"],
        "play_media": ["ha_play_media", "dlna_set_uri"],
        "transport": ["ha_transport", "dlna_transport"],
        "ports": [9197, 8200],
        "wol_supported": False,
        "description": "DLNA Digital Media Renderer. UPnP/SOAP on port 9197/8200.",
    },
    "unknown": {
        "power_on": ["ha_turn_on"],
        "power_off": ["ha_turn_off"],
        "play_media": ["ha_play_media"],
        "transport": ["ha_transport"],
        "ports": [],
        "wol_supported": False,
        "description": "Unknown device type. Use HA service calls as fallback.",
    },
}


def _detect_device_type(entity_id: str, integration: str, device_class: str,
                        open_ports: list[int], metadata: dict) -> str:
    """Detect device type from entity info and open ports."""
    entity_lower = entity_id.lower()
    integration_lower = integration.lower()

    # Check integration first (most reliable)
    integration_map = {
        "roku": "roku",
        "webostv": "webostv",
        "samsungtv": "samsungtv",
        "androidtv": "androidtv",
        "cast": "cast",
        "esphome": "esphome",
        "mqtt": "mqtt",
        "dlna_dmr": "dlna",
        "music_assistant": "cast",  # MASS players often use Cast
    }
    if integration_lower in integration_map:
        return integration_map[integration_lower]

    # Fallback: entity_id patterns
    if "roku" in entity_lower:
        return "roku"
    if "webos" in entity_lower:
        return "webostv"
    if "samsung" in entity_lower:
        return "samsungtv"
    if "android" in entity_lower or "shield" in entity_lower:
        return "androidtv"
    if "cast" in entity_lower or "chrome" in entity_lower:
        return "cast"
    if "esphome" in entity_lower:
        return "esphome"
    if "dlna" in entity_lower:
        return "dlna"

    # Fallback: open ports
    if 8060 in open_ports:
        return "roku"
    if 3000 in open_ports or 7676 in open_ports:
        return "webostv"
    if 8001 in open_ports or 8002 in open_ports:
        return "samsungtv"
    if 5555 in open_ports:
        return "androidtv"
    if 8009 in open_ports:
        return "cast"
    if 80 in open_ports:
        return "esphome"
    if 1883 in open_ports or 8883 in open_ports:
        return "mqtt"
    if 9197 in open_ports or 8200 in open_ports:
        return "dlna"

    return "unknown"


def _check_ports(ip: str, ports: list[int]) -> list[int]:
    """Check which ports are open on a given IP."""
    open_ports = []
    for port in ports:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(0.5)
            result = sock.connect_ex((ip, port))
            if result == 0:
                open_ports.append(port)
            sock.close()
        except Exception:
            pass
    return open_ports


async def profile_device(
    entity_id: str,
    ha_url: str,
    ha_token: str,
    subnet: str = None,
) -> dict:
    """
    Generate a complete device profile.

    Returns:
        {
            "entity_id": "media_player.xxx",
            "network": {"ip": "...", "mac": "...", "hostname": "...", "open_ports": [...]},
            "ha": {"friendly_name": "...", "integration": "...", "device_class": "...", "area": "...", "state": "..."},
            "device_type": "roku",
            "control": {
                "power_on": ["ha_turn_on", "ecp_home_key"],
                "power_off": [...],
                "play_media": [...],
                "transport": [...],
                "wol_supported": True/False,
                "wol_mac": "..." or None,
            },
            "description": "...",
            "recommendations": ["Use ECP for media playback", "WOL available for power-on"],
        }
    """
    # Resolve subnet
    if not subnet:
        import device_discovery
        subnet = device_discovery.DEFAULT_SUBNET

    # 1. Get HA entity info
    state = await ha_client.get_state(ha_url, ha_token, entity_id)
    ha_info = {}
    if state:
        attrs = state.get("attributes", {})
        ha_info = {
            "friendly_name": attrs.get("friendly_name", ""),
            "integration": attrs.get("integration", ""),
            "device_class": attrs.get("device_class", ""),
            "state": state.get("state", "unknown"),
            "supported_features": attrs.get("supported_features", 0),
            "source_list": attrs.get("source_list", []),
        }

    # 2. Discover network info
    net_info = await device_discovery.discover_device(
        entity_id, ha_url, ha_token, use_cache=True
    )
    network = {
        "ip": net_info.get("ip") if net_info else None,
        "mac": net_info.get("mac") if net_info else None,
        "hostname": net_info.get("hostname") if net_info else None,
        "open_ports": [],
        "last_verified": net_info.get("last_verified") if net_info else None,
        "discovery_method": net_info.get("discovery_method") if net_info else None,
    }

    # 3. Scan open ports if IP found
    if network["ip"]:
        all_ports = set()
        for methods in CONTROL_METHODS.values():
            all_ports.update(methods.get("ports", []))
        network["open_ports"] = _check_ports(network["ip"], sorted(all_ports))

    # 4. Detect device type
    device_type = _detect_device_type(
        entity_id,
        ha_info.get("integration", ""),
        ha_info.get("device_class", ""),
        network["open_ports"],
        net_info.get("metadata", {}) if net_info else {},
    )

    # 5. Build control profile
    ctrl = CONTROL_METHODS.get(device_type, CONTROL_METHODS["unknown"]).copy()
    control = {
        "power_on": ctrl["power_on"],
        "power_off": ctrl["power_off"],
        "play_media": ctrl["play_media"],
        "transport": ctrl["transport"],
        "wol_supported": ctrl["wol_supported"],
        "wol_mac": network.get("mac") if ctrl["wol_supported"] else None,
        "ports": ctrl["ports"],
    }

    # 6. Generate recommendations
    recommendations = []
    if control["wol_supported"] and control["wol_mac"]:
        recommendations.append(f"WOL available for power-on (MAC: {control['wol_mac']})")
    elif control["wol_supported"] and not control["wol_mac"]:
        recommendations.append("WOL supported but MAC not discovered — check HA device registry")
    if device_type == "roku":
        recommendations.append("Use ECP + MA sibling delegation for music playback")
        recommendations.append("Use media_player.play_media for announcements (audio/wav)")
    if device_type == "webostv":
        recommendations.append("Use media_player.play_media for announcements")
        recommendations.append("Power-on via media_player.turn_on or WOL")
    if device_type == "samsungtv":
        recommendations.append("Use media_player.play_media for announcements")
        recommendations.append("Power-on via media_player.turn_on (15s boot wait)")
    if device_type == "androidtv":
        recommendations.append("Use androidtv.adb_command for transport controls")
    if device_type == "cast":
        recommendations.append("Use media_player.play_media for all media types")
    if device_type == "esphome":
        recommendations.append("Use HTTP API on port 80 for control")
        if 8080 in network.get("open_ports", []):
            recommendations.append("ESP32-CAM stream available on port 8080")
    if device_type == "mqtt":
        recommendations.append("Use MQTT publish/subscribe for control")
    if not network["ip"]:
        recommendations.append("Device IP not discovered — run network scan or check HA registry")

    return {
        "entity_id": entity_id,
        "network": network,
        "ha": ha_info,
        "device_type": device_type,
        "control": control,
        "description": CONTROL_METHODS.get(device_type, CONTROL_METHODS["unknown"])["description"],
        "recommendations": recommendations,
    }


async def profile_all_media_devices(
    ha_url: str, ha_token: str, subnet: str = None
) -> list[dict]:
    """Profile all media_player entities concurrently."""
    if not subnet:
        import device_discovery
        subnet = device_discovery.DEFAULT_SUBNET
    all_states = await ha_client.get_states(ha_url, ha_token)
    if not all_states:
        return []

    media_entities = [
        s["entity_id"] for s in all_states
        if s["entity_id"].startswith("media_player.")
    ]

    async def _profile_one(entity_id: str):
        try:
            return await profile_device(entity_id, ha_url, ha_token, subnet)
        except Exception as e:
            log.warning(f"[profiler] Failed to profile {entity_id}: {e}")
            return {"entity_id": entity_id, "error": str(e)}

    results = await asyncio.gather(*[_profile_one(e) for e in media_entities], return_exceptions=True)
    return [r for r in results if not isinstance(r, Exception)]


async def build_capability_map(
    ha_url: str, ha_token: str, subnet: str = None
) -> dict:
    """
    Build a friendly-name -> capability lookup for all media devices.

    Returns:
        {
            "gracies tv": {
                "entity_id": "media_player.28_tcl_roku_tv",
                "device_type": "roku",
                "ip": "192.168.2.166",
                "mac": "cc:b0:da:c6:8f:21",
                "power_on": ["ha_turn_on", "ecp_home_key"],
                "power_off": ["ha_turn_off", "remote_power_off"],
                "play_media": ["ecp_launch", "ma_delegation"],
                "transport": ["ecp_keypress", "ha_remote_send"],
                "wol_supported": False,
                "recommendations": [...]
            },
            "office tv": {...},
            ...
        }
    """
    if not subnet:
        import device_discovery
        subnet = device_discovery.DEFAULT_SUBNET
    profiles = await profile_all_media_devices(ha_url, ha_token, subnet)
    cap_map = {}
    for p in profiles:
        if "error" in p:
            continue
        ha = p.get("ha", {})
        friendly = ha.get("friendly_name", "").lower().strip()
        entity_id = p.get("entity_id", "")
        if not friendly:
            continue

        cap_map[friendly] = {
            "entity_id": entity_id,
            "device_type": p.get("device_type", "unknown"),
            "ip": p.get("network", {}).get("ip"),
            "mac": p.get("network", {}).get("mac"),
            "hostname": p.get("network", {}).get("hostname"),
            "open_ports": p.get("network", {}).get("open_ports", []),
            "power_on": p.get("control", {}).get("power_on", []),
            "power_off": p.get("control", {}).get("power_off", []),
            "play_media": p.get("control", {}).get("play_media", []),
            "transport": p.get("control", {}).get("transport", []),
            "wol_supported": p.get("control", {}).get("wol_supported", False),
            "description": p.get("description", ""),
            "recommendations": p.get("recommendations", []),
        }

    return cap_map


def resolve_by_friendly_name(
    cap_map: dict, query: str
) -> tuple[str | None, dict | None]:
    """
    Resolve a fuzzy friendly name query to (entity_id, capabilities).

    Handles partial matches like "gracies", "office tv", "master bedroom".
    Returns (entity_id, cap_dict) or (None, None).
    """
    query_lower = query.lower().strip()

    # Exact match first
    if query_lower in cap_map:
        return cap_map[query_lower]["entity_id"], cap_map[query_lower]

    # Partial match — find best overlap
    best_score = 0
    best_key = None
    for key in cap_map:
        words = query_lower.split()
        key_words = key.split()
        matches = sum(1 for w in words if any(w in kw or kw in w for kw in key_words))
        score = matches / max(len(words), len(key_words))
        if score > best_score:
            best_score = score
            best_key = key

    if best_score >= 0.5 and best_key:
        return cap_map[best_key]["entity_id"], cap_map[best_key]

    return None, None
