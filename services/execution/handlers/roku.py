# services/execution/handlers/roku.py
"""
Roku media playback and transport commands via Home Assistant's roku integration.

Roku uses a two-part approach for music:
  1. ECP launch of Media Assistant (782875) for the rich music UI
  2. Music Assistant service call on the MA player sibling for actual audio

For transport:
  - roku.launch: Launch an app by app_id
  - roku.press: Send a key press (HOME, BACK, PLAY, etc.)
  - remote.send_command: Alternative transport via remote entity
"""
import logging
import asyncio
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
try:
    import ha_client
    from schemas import ExecutionResult
except ImportError:
    import ha_client
    from schemas import ExecutionResult

log = logging.getLogger("execution.roku")

# Roku key mappings
ROKU_KEYS = {
    "home": "Home", "back": "Back", "enter": "Select", "select": "Select",
    "up": "Up", "down": "Down", "left": "Left", "right": "Right",
    "play": "Play", "pause": "Play", "stop": "Stop",
    "fast_forward": "Fwd", "rewind": "Rev",
    "info": "Info", "backspace": "Backspace",
    "volume_up": "VolumeUp", "volume_down": "VolumeDown", "mute": "VolumeMute",
    "power_off": "PowerOff", "power_on": "PowerOn",
    "input_av1": "InputAV1", "input_hdmi1": "InputHDMI1",
    "input_hdmi2": "InputHDMI2", "input_hdmi3": "InputHDMI3",
    "input_hdmi4": "InputHDMI4", "input_tuner": "InputTuner",
}

# Common Roku app IDs
ROKU_APPS = {
    "netflix": "12", "youtube": "837", "hulu": "2285",
    "disney_plus": "291097", "prime_video": "13", "spotify": "22297",
    "plex": "13535", "tubi": "26079", "peacock": "427192",
    "paramount_plus": "428927", "hbo_max": "301921", "apple_tv": "472192",
    "media_assistant": "782875",
}

MEDIA_ASSISTANT_CHANNEL_ID = "782875"


async def is_roku_device(ha_url: str, ha_token: str, entity_id: str) -> bool:
    """Check if a media_player entity is a Roku device."""
    state = await ha_client.get_state(ha_url, ha_token, entity_id)
    if not state:
        return False
    attrs = state.get("attributes", {})
    app_id = (attrs.get("app_id") or "").lower()
    entity_id_lower = entity_id.lower()
    roku_indicators = ("roku.", "com.roku.", "roku media player")
    if any(ind in app_id for ind in roku_indicators):
        return True
    if "roku" in entity_id_lower:
        return True
    source_list = [s.lower() for s in (attrs.get("source_list") or [])]
    roku_sources = {"home", "roku media player", "the roku channel"}
    if roku_sources & set(source_list):
        return True
    return False


async def get_roku_ip(ha_url: str, ha_token: str, entity_id: str) -> str | None:
    """Discover Roku IP via HA device registry, SSDP, or local network scan."""
    import httpx
    try:
        headers = {"Authorization": f"Bearer {ha_token}"}
        async with httpx.AsyncClient(verify=False, timeout=10) as client:
            resp = await client.get(f"{ha_url}/api/devices", headers=headers)
            if resp.status_code == 200:
                devices = resp.json()
                for dev in devices:
                    dev_name = (dev.get("name") or "").lower()
                    entity_name = entity_id.replace("media_player.", "").replace("_", " ").lower()
                    if any(part in dev_name for part in entity_name.split() if len(part) > 2):
                        for conn in dev.get("connections", []):
                            if conn and conn[0] == "network":
                                return conn[1]
    except Exception as e:
        log.warning(f"[roku] Device registry IP lookup failed: {e}")
    
    try:
        import socket
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(3)
        sock.bind(("", 0))
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        ssdp_request = (
            'M-SEARCH * HTTP/1.1\r\n'
            'HOST: 239.255.255.250:1900\r\n'
            'MAN: "ssdp:discover"\r\n'
            'MX: 2\r\n'
            'ST: roku:ecp\r\n'
            '\r\n'
        )
        sock.sendto(ssdp_request.encode(), ("239.255.255.250", 1900))
        while True:
            try:
                data, addr = sock.recvfrom(1024)
                if b"Roku" in data or b"roku" in data:
                    sock.close()
                    return addr[0]
            except socket.timeout:
                break
        sock.close()
    except Exception as e:
        log.warning(f"[roku] SSDP discovery failed: {e}")
    
    try:
        import ipaddress
        async with httpx.AsyncClient(verify=False) as client:
            candidates = [str(ip) for ip in ipaddress.IPv4Network("192.168.2.0/24")
                          if not str(ip).endswith(".0") and not str(ip).endswith(".255")]
            batch_size = 30
            for i in range(0, len(candidates), batch_size):
                batch = candidates[i:i + batch_size]
                tasks = [client.get(f"http://{ip}:8060/query/device-info", timeout=1) for ip in batch]
                results = await asyncio.gather(*tasks, return_exceptions=True)
                for ip, resp in zip(batch, results):
                    if not isinstance(resp, Exception) and resp.status_code == 200 and b"roku" in resp.content.lower():
                        log.info(f"[roku] Found Roku via network scan: {ip}")
                        return ip
    except Exception as e:
        log.warning(f"[roku] Network scan failed: {e}")
    
    return None


async def _probe_roku_ecp(client, ip: str) -> str | None:
    """Probe a single IP for Roku ECP response."""
    try:
        resp = await client.get(f"http://{ip}:8060/query/device-info", timeout=2)
        if resp.status_code == 200 and b"roku" in resp.content.lower():
            return ip
    except Exception:
        pass
    return None


async def find_ma_player_sibling(ha_url: str, ha_token: str, roku_entity: str) -> str | None:
    """Find the Music Assistant player entity that is a sibling of the Roku entity."""
    all_states = await ha_client.get_states(ha_url, ha_token)
    if not all_states:
        return None
    
    roku_friendly = ""
    for state in all_states:
        if state.get("entity_id") == roku_entity:
            roku_friendly = state.get("attributes", {}).get("friendly_name", "").lower()
            break
    
    if not roku_friendly:
        return None
    
    for state in all_states:
        eid = state.get("entity_id", "")
        if not eid.startswith("media_player.") or eid == roku_entity:
            continue
        attrs = state.get("attributes", {})
        friendly = attrs.get("friendly_name", "").lower()
        source = attrs.get("source", "").lower()
        integration = attrs.get("integration", "")
        
        is_ma = ("music_assistant" in str(integration).lower() or
                 "active_queue" in attrs or
                 "mass_player_type" in attrs or
                 "music_assistant" in source)
        
        if is_ma and (roku_friendly in friendly or friendly in roku_friendly):
            log.info(f"[roku] Found MA player sibling: {eid}")
            return eid
    
    log.warning(f"[roku] No MA player sibling found for {roku_entity}")
    return None


async def roku_play_music(ha_url: str, ha_token: str, roku_entity: str, query: str,
                          mass_config_entry_id: str = "") -> ExecutionResult:
    """
    Play music on Roku using the two-part approach:
    1. Launch Media Assistant (782875) via ECP for the UI
    2. Call music_assistant/play_media on the MA player sibling for audio
    """
    log.info(f"[roku.music] Playing '{query}' on {roku_entity}")
    
    roku_ip = await get_roku_ip(ha_url, ha_token, roku_entity)
    if not roku_ip:
        log.warning("[roku.music] Could not find Roku IP, falling back to MA-only")
        ma_entity = await find_ma_player_sibling(ha_url, ha_token, roku_entity)
        if ma_entity:
            result = await ha_client.call_service(
                ha_url, ha_token, "music_assistant", "play_media", ma_entity,
                {"media_id": query, "media_type": "track", "enqueue": "play"},
            )
            if result.get("ok"):
                return ExecutionResult(status="SUCCESS", message=f"Playing '{query}' on {roku_entity} (MA only).", service="roku_music")
    
    ma_entity = await find_ma_player_sibling(ha_url, ha_token, roku_entity)
    if not ma_entity:
        return ExecutionResult(status="FAILURE", message=f"Could not find Music Assistant player for {roku_entity}.", service="roku_music")
    
    import httpx
    params = {"t": "a", "autoplay": "true"}
    
    search_result = await ha_client.call_service(
        ha_url, ha_token, "music_assistant", "search", entity_id="",
        service_data={
            "config_entry_id": mass_config_entry_id,
            "name": query,
            "media_type": ["track", "artist", "album", "playlist"],
            "limit": 1,
        },
        return_response=True,
    )
    
    song_name = query
    artist_name = ""
    ma_media_id = query
    ma_media_type = "track"
    
    if search_result.get("ok") and search_result.get("service_response"):
        raw = search_result["service_response"]
        resp = raw.get("service_response", raw)
        for category in ["tracks", "albums", "artists", "playlists"]:
            items = resp.get(category, [])
            if items:
                item = items[0]
                song_name = item.get("name", item.get("title", query))
                artist_name = item.get("artist", {}).get("name", "") if isinstance(item.get("artist"), dict) else ""
                ma_media_id = item.get("uri", query)
                ma_media_type = category.rstrip("s")
                params["songName"] = song_name
                if artist_name:
                    params["artistName"] = artist_name
                if item.get("image", {}).get("path"):
                    params["albumArt"] = item["image"]["path"]
                log.info(f"[roku.music] MA search match: {song_name} by {artist_name}")
                break
    else:
        params["songName"] = song_name
    
    ecp_url = f"http://{roku_ip}:8060/launch/{MEDIA_ASSISTANT_CHANNEL_ID}"
    try:
        log.info(f"[roku.music] Launching Media Assistant via ECP: {ecp_url}")
        async with httpx.AsyncClient(verify=False, timeout=10) as client:
            resp = await client.post(ecp_url, params=params)
            log.info(f"[roku.music] ECP response: {resp.status_code}")
            if resp.status_code in (200, 204):
                await asyncio.sleep(3)
            else:
                log.warning(f"[roku.music] ECP launch returned {resp.status_code}")
    except Exception as e:
        log.warning(f"[roku.music] ECP launch failed: {e}")
    
    log.info(f"[roku.music] Delegating audio to MA: {ma_entity} media_id={ma_media_id} type={ma_media_type}")
    result = await ha_client.call_service(
        ha_url, ha_token, "music_assistant", "play_media", ma_entity,
        {"media_id": ma_media_id, "media_type": ma_media_type, "enqueue": "play"},
    )
    
    if result.get("ok"):
        return ExecutionResult(status="SUCCESS", message=f"Playing '{song_name}' on {roku_entity}.", service="roku_music")
    
    return ExecutionResult(status="FAILURE", message=f"Failed to play music on {roku_entity}: {result.get('error')}", service="roku_music", detail=result)


async def roku_press(ha_url: str, ha_token: str, entity_id: str, key: str) -> ExecutionResult:
    """Send a key press to a Roku device."""
    roku_key = ROKU_KEYS.get(key.lower(), key)
    log.info(f"[roku] Pressing '{roku_key}' on {entity_id}")
    result = await ha_client.call_service(
        ha_url, ha_token, "roku", "press", entity_id,
        {"key": roku_key}
    )
    if result.get("ok"):
        return ExecutionResult(status="SUCCESS", message=f"Sent '{key}' to {entity_id} (Roku).", service="roku_transport")
    return ExecutionResult(status="FAILURE", message=f"Failed to send '{key}' to Roku: {result.get('error')}", service="roku_transport", detail=result)


async def roku_launch(ha_url: str, ha_token: str, entity_id: str, app: str) -> ExecutionResult:
    """Launch an app on a Roku device."""
    app_id = ROKU_APPS.get(app.lower(), app)
    log.info(f"[roku] Launching app '{app_id}' on {entity_id}")
    result = await ha_client.call_service(
        ha_url, ha_token, "roku", "launch", entity_id,
        {"app_id": app_id}
    )
    if result.get("ok"):
        return ExecutionResult(status="SUCCESS", message=f"Launched '{app}' on {entity_id} (Roku).", service="roku_transport")
    return ExecutionResult(status="FAILURE", message=f"Failed to launch '{app}' on Roku: {result.get('error')}", service="roku_transport", detail=result)


async def roku_home(ha_url: str, ha_token: str, entity_id: str) -> ExecutionResult:
    """Return Roku to home screen."""
    return await roku_press(ha_url, ha_token, entity_id, "home")


async def roku_back(ha_url: str, ha_token: str, entity_id: str) -> ExecutionResult:
    """Send back command on Roku."""
    return await roku_press(ha_url, ha_token, entity_id, "back")


async def roku_power_off(ha_url: str, ha_token: str, entity_id: str) -> ExecutionResult:
    """Power off Roku device."""
    return await roku_press(ha_url, ha_token, entity_id, "power_off")


async def roku_play_pause(ha_url: str, ha_token: str, entity_id: str, command: str) -> ExecutionResult:
    """Play or pause on Roku (same key toggles)."""
    return await roku_press(ha_url, ha_token, entity_id, "play")


async def roku_stop(ha_url: str, ha_token: str, entity_id: str) -> ExecutionResult:
    """Stop playback on Roku."""
    return await roku_press(ha_url, ha_token, entity_id, "stop")
