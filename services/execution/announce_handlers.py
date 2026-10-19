"""Unified TV & Device Communication Layer for Announcements, Alarms, and 1-Way / 2-Way Intercoms.

Supports multi-generation Smart TVs and media players via their native integrations:
- Roku TVs / Streaming Players (Roku OS ECP port 8060, Media Assistant app 782875, Music Assistant sibling)
- LG webOS TVs (webOS toast notifications via notify.lg_webos_* / webostv.command + audio via play_media / AirPlay MA sibling)
- Samsung TVs (Tizen 2016+ via Music Assistant / DLNA / WebSocket, and Legacy Orsay 2011-2015 via DLNA / room Cast companion)
- Android TV / Google TV (Wake via androidtv_remote / ADB + audio routing via Cast sibling / Music Assistant sibling)
- Chromecast / Google Cast (Direct HTTP WAV/MP3 streaming via cast integration)
- Sony Bravia, DLNA DMR, Music Assistant, and ESPHome voice satellites.
"""
import asyncio
import contextlib
import logging
import re
from typing import Any

from services.common.entity_ids import usable_entity_id

log = logging.getLogger("execution.announce")

# Supported feature flags (from HA media_player)
SUPPORT_SELECT_SOURCE = 1
SUPPORT_PLAY_MEDIA = 16384
SUPPORT_TURN_ON = 128
SUPPORT_TURN_OFF = 256
SUPPORT_VOLUME_SET = 4
SUPPORT_BROWSE_MEDIA = 131072
SUPPORT_MEDIA_ANNOUNCE = 1 << 20

# Exact supported_features bitmask for HA androidtv_remote media_player entities
ANDROIDTV_REMOTE_FEATURES = 153529
# Exact supported_features bitmask for HA cast media_player entities
CAST_FEATURES = 152461
# Exact supported_features bitmask for HA samsungtv / webostv media_player entities
TV_STANDARD_FEATURES = 24381

# Cast receiver app IDs
CAST_APP_IDS = {"cc1ad845", "9ac10326", "4475d545", "e8c61a77", "53c9a77e"}

# Android TV package prefixes
ANDROID_PACKAGE_PREFIXES = ("com.google.android.", "com.google.tv.", "com.android.", "com.sonymobile.android.")

# Android TV app_id indicators
ANDROID_INDICATORS = ("mediashell", "backdrop", "tvlauncher", "android.tv", "atv", "chromecast")

# Roku-specific source names
ROKU_SOURCES = {"home", "roku media player", "the roku channel", "roku tv intro"}

# Samsung Tizen-specific source names
SAMSUNG_SOURCES = {"samsung gamebreaks", "samsung promotion", "smartthings", "samsung tv plus", "universal guide"}

# LG webOS-specific source names
WEBOS_SOURCES = {"lg content store", "lg gaming portal", "home dashboard", "tv scheduler", "multi-view"}

# Common streaming apps (present on many platforms, but Roku has them as sources)
STREAMING_APPS = {
    "netflix", "hulu", "disney plus", "prime video", "youtube",
    "youtube tv", "peacock tv", "paramount plus", "tubi", "fandango at home",
}

# Known manufacturer/model patterns in entity_id or attributes
MANUFACTURER_PATTERNS = {
    "lg": "webos",
    "webos": "webos",
    "samsung": "samsung",
    "tizen": "samsung",
    "sony": "bravia",
    "bravia": "bravia",
    "roku": "roku",
    "hisense": "android_tv",
    "philips": "android_tv",
    "sharp": "roku",
    "tcl": "roku",
    "vizio": "generic_tv",
    "panasonic": "generic_tv",
    "toshiba": "generic_tv",
}

TV_PLATFORMS = {"roku", "webos", "samsung", "android_tv", "cast", "bravia", "generic_tv"}


def _get_ha_client():
    try:
        from services.execution import ha_client
        return ha_client
    except ImportError:
        import ha_client  # type: ignore[no-redef]
        return ha_client


def _get_device_discovery():
    try:
        from services.execution import device_discovery
        return device_discovery
    except ImportError:
        import device_discovery  # type: ignore[no-redef]
        return device_discovery


def _get_device_registry():
    try:
        from services.execution import device_registry
        return device_registry
    except ImportError:
        try:
            import device_registry  # type: ignore[no-redef]
            return device_registry
        except ImportError:
            return None


def _is_ma_player(attrs: dict, entity_id: str = "") -> bool:
    """Detect Music Assistant player even when state is 'off' and app_id is None."""
    if not isinstance(attrs, dict):
        return False
    try:
        from services.shared.ma_player import is_music_assistant_player
        return is_music_assistant_player(attrs, entity_id)
    except ImportError:
        return bool(
            (attrs.get("app_id") or "").lower() == "music_assistant"
            or attrs.get("mass_player_type")
            or attrs.get("active_queue") is not None
        )


def _room_slug_from_entity(entity_id: str, friendly_name: str = "") -> str:
    """Extract normalized room prefix (e.g. 'office', 'living_room', 'master_bedroom', 'loft', 'gracies', 'rv')."""
    raw = entity_id.split(".")[-1].lower()
    for suffix in (
        "_assist_satellite", "_s3_box_media_player", "_jarvis_smart_speaker",
        "_smart_speaker", "_tv_chrome_2", "_tv_chrome", "_tv_remote",
        "_tv_2", "_tv_3", "_tv", "_samsung", "_speaker_2", "_speaker",
        "_airplay", "_cast", "_dlna", "_q900_2", "_q900",
    ):
        if raw.endswith(suffix):
            raw = raw[: -len(suffix)]
            break
    raw = raw.removeprefix("jarvis_").removeprefix("tv_")
    # Collapse duplicate room words like 'office_office' -> 'office'
    parts = [p for p in raw.split("_") if p and p not in ("jarvis", "media", "player")]
    if len(parts) >= 2 and parts[0] == parts[1]:
        parts = parts[1:]
    return "_".join(parts)


def detect_tv_type(
    entity_id: str,
    state: str,
    attributes: dict,
    loaded_components: set[str] | None = None,
) -> str:
    """Detect TV platform type using multiple context clues in priority order.

    Handles entities even when `state == 'off'` and `app_id` is None.
    """
    eid = entity_id.lower()
    attrs = attributes or {}
    app_id = (attrs.get("app_id") or "").lower()
    device_class = (attrs.get("device_class") or "").lower()
    supported_features = int(attrs.get("supported_features") or 0)
    source_list = [str(s).lower().strip() for s in (attrs.get("source_list") or [])]
    source_set = set(source_list)
    active_queue = (attrs.get("active_queue") or "").lower()
    friendly_lower = (attrs.get("friendly_name") or "").lower()

    # 1. Cast devices: entity contains 'chrome'/'cast', or app_id matches known Cast receivers
    if "chrome" in eid or "_cast" in eid or app_id in CAST_APP_IDS:
        return "cast"

    # 2. Roku: entity contains 'roku', source_list has Roku entries, or MA player wrapping Roku queue
    has_roku_sources = bool(ROKU_SOURCES & source_set)
    has_many_streaming = len(ROKU_SOURCES & source_set) >= 1 or len(STREAMING_APPS & source_set) >= 5
    is_ma_roku = ("roku" in active_queue) or (
        _is_ma_player(attrs, eid) and ("roku" in active_queue or "roku" in eid or "roku" in friendly_lower)
    )
    if "roku" in eid or "roku" in friendly_lower or has_roku_sources or has_many_streaming or is_ma_roku:
        return "roku"

    # 3. webOS (LG Electronics): entity/friendly_name/app_id/attributes/sources indicate LG webOS
    #    Note: webOS is LG's TV OS (never Samsung). HA webostv exposes `sound_output` and `com.webos.*` / `com.lge.*` app_ids.
    has_webos_sources = bool(WEBOS_SOURCES & source_set)
    is_webos_app = any(
        ind in app_id for ind in ("com.webos.", "com.lge.", "com.palm.", "webos.tv", "lg.webos")
    )
    integration_lower = (attrs.get("integration") or "").lower()
    manufacturer_lower = (attrs.get("manufacturer") or "").lower()
    has_lg_word = bool(re.search(r"\blg\b", friendly_lower)) or bool(re.search(r"(^|[._])lg([._]|$)", eid))
    if (
        has_lg_word
        or "webos" in eid
        or "web_os" in eid
        or "webostv" in eid
        or "webos" in friendly_lower
        or "web os" in friendly_lower
        or is_webos_app
        or "sound_output" in attrs
        or integration_lower == "webostv"
        or manufacturer_lower in ("lg", "lg electronics", "lge")
        or has_webos_sources
    ):
        return "webos"

    # 4. Samsung (Tizen & Legacy Orsay): entity/friendly_name/app_id/attributes/sources indicate Samsung
    #    or HA samsungtv signature (source_list == ['tv', 'hdmi'] with TV class/features and no LG sound_output)
    has_samsung_sources = bool(SAMSUNG_SOURCES & source_set)
    is_samsung_app = any(
        ind in app_id for ind in ("org.tizen.", "samsung.tv", "tizen.tv", "com.samsung.")
    )
    is_samsung_tv_sources = (
        source_list == ["tv", "hdmi"]
        and "sound_output" not in attrs
        and (device_class == "tv" or supported_features == TV_STANDARD_FEATURES)
    )
    if (
        "samsung" in eid
        or "tizen" in eid
        or "orsay" in eid
        or "samsung" in friendly_lower
        or "tizen" in friendly_lower
        or is_samsung_app
        or integration_lower == "samsungtv"
        or "samsung" in manufacturer_lower
        or has_samsung_sources
        or is_samsung_tv_sources
    ):
        return "samsung"

    # 5. Android TV: app_id is Android package/indicator, entity has 'android',
    #    or supported_features == 153529 (HA androidtv_remote signature even when off)
    is_android_app = any(app_id.startswith(pkg) for pkg in ANDROID_PACKAGE_PREFIXES)
    is_android_indicator = any(ind in app_id for ind in ANDROID_INDICATORS)
    is_androidtv_remote = supported_features == ANDROIDTV_REMOTE_FEATURES and (
        device_class == "tv" or "tv" in eid or "remote" in friendly_lower
    )
    if is_android_app or is_android_indicator or "android" in eid or is_androidtv_remote:
        return "android_tv"

    # 6. Sony Bravia: entity contains 'bravia' or 'sony'
    if "bravia" in eid or "sony" in eid:
        return "bravia"

    # 7. ESPHome: entity contains 'esphome' or known ESPHome voice satellite slugs
    if "esphome" in eid or "s3_box" in eid or "jarvis_smart_speaker" in eid or "atom_echo" in eid:
        return "esphome"

    # 8. DLNA: entity contains 'dlna'
    if "dlna" in eid:
        return "dlna"

    # 9. Cast by exact feature signature (when not an MA wrapper)
    if supported_features == CAST_FEATURES and not _is_ma_player(attrs, eid):
        return "cast"

    # 10. Music Assistant: app_id is 'music_assistant' or mass_player_type is set
    if _is_ma_player(attrs, eid):
        return "music_assistant"

    # 11. Generic TV: device_class=tv with TV inputs in source_list
    tv_inputs = {"live tv", "tv", "hdmi", "hdmi 1", "hdmi 2", "hdmi 3", "av", "component"}
    has_tv_inputs = bool(tv_inputs & source_set)
    if device_class == "tv" or has_tv_inputs:
        return "generic_tv"

    # 12. Speaker: device_class=speaker
    if device_class == "speaker":
        return "speaker"

    # 13. Use loaded components as additional signal (only when unambiguous)
    if loaded_components:
        if "cast.media_player" in loaded_components and supported_features & SUPPORT_PLAY_MEDIA:
            return "cast"
        has_samsung_comp = "samsungtv.media_player" in loaded_components
        has_webos_comp = "webostv.media_player" in loaded_components
        if has_samsung_comp and not has_webos_comp:
            return "samsung"
        if has_webos_comp and not has_samsung_comp:
            return "webos"
        if "androidtv_remote.media_player" in loaded_components:
            return "android_tv"
        if "roku" in loaded_components and supported_features & SUPPORT_BROWSE_MEDIA:
            return "roku"
        if "dlna_dmr.media_player" in loaded_components:
            return "dlna"

    return "unknown"


def resolve_tv_companions(
    entity_id: str,
    all_states: list[dict] | None = None,
    services: Any = None,
) -> dict[str, Any]:
    """Resolve sibling/companion entities for a TV or room media device.

    Returns dict with:
    - primary_entity: underlying hardware TV entity
    - ma_sibling: Music Assistant player entity for this TV
    - cast_sibling: Chromecast / Google Cast sibling entity for this TV or room
    - dlna_sibling: DLNA DMR sibling entity for this TV
    - remote_sibling: HA remote.* entity for waking / key commands
    - webos_notify_service: notify.<service_name> for LG webOS toast banners
    - room_assist_satellite: assist_satellite.* in the same room (for 2-way intercom)
    - room_esphome_speaker: ESPHome speaker media_player.* in the same room
    """
    states = all_states or []
    state_by_id = {s.get("entity_id", ""): s for s in states if s.get("entity_id")}
    target_state = state_by_id.get(entity_id, {})
    target_attrs = target_state.get("attributes", {}) if isinstance(target_state, dict) else {}
    room_slug = _room_slug_from_entity(entity_id, target_attrs.get("friendly_name", ""))

    primary_entity = entity_id
    active_q = target_attrs.get("active_queue") or ""
    if active_q.startswith("media_player.") and active_q in state_by_id:
        primary_entity = active_q

    ma_sibling: str | None = None
    cast_sibling: str | None = None
    dlna_sibling: str | None = None
    remote_sibling: str | None = None
    room_assist_satellite: str | None = None
    room_esphome_speaker: str | None = None

    if _is_ma_player(target_attrs, entity_id):
        ma_sibling = entity_id

    for s in states:
        eid = s.get("entity_id", "")
        if not eid:
            continue
        s_attrs = s.get("attributes", {}) or {}
        s_q = s_attrs.get("active_queue") or ""
        s_room = _room_slug_from_entity(eid, s_attrs.get("friendly_name", ""))
        s_features = int(s_attrs.get("supported_features") or 0)

        if eid.startswith("assist_satellite.") and room_slug and (room_slug in eid or s_room == room_slug):
            room_assist_satellite = eid
            continue

        if eid.startswith("remote.") and room_slug and (room_slug in eid or s_room == room_slug):
            if not remote_sibling or eid == f"remote.{entity_id.split('.')[-1]}":
                remote_sibling = eid
            continue

        if not eid.startswith("media_player."):
            continue

        # ESPHome speaker in same room
        if ("s3_box" in eid or "jarvis_smart_speaker" in eid or "esphome" in eid) and room_slug and s_room == room_slug:
            room_esphome_speaker = eid

        if eid == entity_id:
            continue

        # Music Assistant sibling matching
        if _is_ma_player(s_attrs, eid):
            if s_q in (entity_id, primary_entity):
                ma_sibling = eid
            elif not ma_sibling and room_slug and s_room == room_slug and "speaker" not in eid:
                ma_sibling = eid
            elif not ma_sibling and "webos" in eid and ("living_room" in entity_id or "webos" in entity_id):
                ma_sibling = eid

        # Cast sibling matching (e.g., media_player.office_tv_chrome or media_player.master_bedroom_tv)
        if not _is_ma_player(s_attrs, eid):
            if ("chrome" in eid or "_cast" in eid or s_features == CAST_FEATURES) and room_slug and s_room == room_slug:
                cast_sibling = eid
                # Also check if there is an MA wrapper for this cast sibling
                if not ma_sibling:
                    for s2 in states:
                        eid2 = s2.get("entity_id", "")
                        a2 = s2.get("attributes", {}) or {}
                        if _is_ma_player(a2, eid2) and a2.get("active_queue") == eid:
                            ma_sibling = eid2
                            break

        # DLNA sibling matching (e.g., media_player.tv_loft_samsung_q900)
        if ("dlna" in eid or "q900" in eid or (s_q == primary_entity and not _is_ma_player(s_attrs, eid))):
            if room_slug and (room_slug in eid or s_q == primary_entity):
                dlna_sibling = eid

    # Discover LG webOS notify service if available
    webos_notify_service = None
    if isinstance(services, list):
        for dom_entry in services:
            if isinstance(dom_entry, dict) and dom_entry.get("domain") == "notify":
                svcs = dom_entry.get("services") or {}
                for svc_name in svcs:
                    if "webos" in svc_name.lower() or "lg_" in svc_name.lower():
                        webos_notify_service = svc_name
                        break
    elif isinstance(services, dict):
        notify_svcs = services.get("notify") or {}
        for svc_name in notify_svcs:
            if "webos" in svc_name.lower() or "lg_" in svc_name.lower():
                webos_notify_service = svc_name
                break

    return {
        "primary_entity": primary_entity,
        "ma_sibling": ma_sibling,
        "cast_sibling": cast_sibling,
        "dlna_sibling": dlna_sibling,
        "remote_sibling": remote_sibling,
        "webos_notify_service": webos_notify_service,
        "room_assist_satellite": room_assist_satellite,
        "room_esphome_speaker": room_esphome_speaker,
        "room_slug": room_slug,
    }


async def search_device_type(
    entity_id: str,
    attributes: dict,
    loaded_components: set[str] | None = None,
) -> str | None:
    """Search the web to identify an unknown device type using available clues."""
    clues = []
    eid_parts = entity_id.lower().replace("media_player.", "").replace("_", " ").split()
    clues.extend(eid_parts)

    app_id = attributes.get("app_id", "")
    if app_id:
        clues.append(app_id)

    features = attributes.get("supported_features", 0)
    if features & SUPPORT_BROWSE_MEDIA:
        clues.append("browse media")
    if features & SUPPORT_SELECT_SOURCE:
        clues.append("source select")

    if loaded_components:
        related = [c for c in loaded_components if "media_player" in c or "tv" in c or "cast" in c or "roku" in c]
        clues.extend(related)

    if not clues:
        return None

    query = f"home assistant media_player {' '.join(clues[:5])} integration type"

    try:
        from websearch import web_search
        results = await web_search(query, num_results=3)
        combined = " ".join([r.get("snippet", "") + " " + r.get("title", "") for r in results]).lower()

        platform_keywords = {
            "cast": ["google cast", "chromecast", "cast integration"],
            "roku": ["roku integration", "roku media player"],
            "android_tv": ["android tv", "androidtv_remote", "adb"],
            "webos": ["lg webos", "webostv"],
            "samsung": ["samsung tv", "tizen", "smartthings"],
            "bravia": ["sony bravia"],
            "dlna": ["dlna", "dlna_dmr"],
        }

        for platform, keywords in platform_keywords.items():
            if any(kw in combined for kw in keywords):
                log.info(f"[announce.search] Web search suggests {platform} for {entity_id}")
                return platform
    except Exception as e:
        log.warning(f"[announce.search] Web search failed: {e}")

    return None


async def announce_cast(
    ha_url: str,
    ha_token: str,
    entity_id: str,
    media_url: str,
    volume: float,
    state: str = "unknown",
    attributes: dict | None = None,
    message: str = "",
) -> dict[str, Any]:
    """Cast devices support direct URL playback."""
    ha_client = _get_ha_client()
    log.info(f"[announce.cast] Playing URL on {entity_id}: {media_url[:60]}")
    if volume > 0:
        with contextlib.suppress(Exception):
            await ha_client.call_service(
                ha_url, ha_token, "media_player", "volume_set", entity_id, {"volume_level": volume}
            )
    return await ha_client.call_service(ha_url, ha_token, "media_player", "play_media", entity_id, {
        "media_content_id": media_url,
        "media_content_type": "url",
    })


async def announce_roku(
    ha_url: str,
    ha_token: str,
    entity_id: str,
    media_url: str,
    volume: float,
    state: str = "unknown",
    attributes: dict | None = None,
    message: str = "",
) -> dict[str, Any]:
    """Roku TV / player: wake display via ECP PowerOn + Home, launch Media Assistant (782875) with audio URL,
    and fall back to Music Assistant sibling or media_player.play_media.
    """
    device_discovery = _get_device_discovery()
    ha_client = _get_ha_client()

    log.info(f"[announce.roku] Waking display and preparing Media Assistant for {entity_id}...")

    roku_result = await device_discovery.discover_device(entity_id, ha_url, ha_token, device_type="roku")
    roku_ip = roku_result.get("ip") if roku_result else None

    if roku_ip:
        import aiohttp
        try:
            log.info(f"[announce.roku] Sending PowerOn + Home key via ECP to {roku_ip}")
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=5),
                connector=aiohttp.TCPConnector(ssl=False),
            ) as client:
                with contextlib.suppress(Exception):
                    await client.post(f"http://{roku_ip}:8060/keypress/PowerOn")
                resp = await client.post(f"http://{roku_ip}:8060/keypress/Home")
                log.info(f"[announce.roku] ECP Home key response: {resp.status}")
        except Exception as e:
            log.warning(f"[announce.roku] ECP Home key failed: {e}")

    await ha_client.call_service(ha_url, ha_token, "media_player", "turn_on", entity_id, {})
    await asyncio.sleep(2.5)

    # 2. Launch Media Assistant (app 782875) with audio URL via ECP
    if roku_ip:
        import aiohttp
        try:
            ecp_url = f"http://{roku_ip}:8060/launch/782875"
            params = {
                "t": "a",
                "u": media_url,
                "songName": message or "SharedLLM Announcement",
                "songFormat": "wav",
                "autoplay": "true",
            }
            log.info(f"[announce.roku] ECP launch Media Assistant: {ecp_url} params={params}")
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=15),
                connector=aiohttp.TCPConnector(ssl=False),
            ) as client:
                resp = await client.post(ecp_url, params=params)
                log.info(f"[announce.roku] ECP response: {resp.status}")
                if resp.status in (200, 204):
                    return {"ok": True, "method": "ecp_media_assistant_782875", "ip": roku_ip}
        except Exception as e:
            log.warning(f"[announce.roku] ECP launch failed: {e}")

    # 3. Fallback: check Music Assistant sibling (e.g., media_player.gracies_tv)
    with contextlib.suppress(Exception):
        all_states = await ha_client.get_all_states(ha_url, ha_token)
        companions = resolve_tv_companions(entity_id, all_states)
        ma_target = companions.get("ma_sibling")
        if ma_target:
            log.info(f"[announce.roku] Trying Music Assistant sibling {ma_target}")
            ma_res = await ha_client.call_service(
                ha_url, ha_token, "media_player", "play_media", ma_target,
                {"media_content_id": media_url, "media_content_type": "url"},
            )
            if ma_res.get("ok"):
                return ma_res

    # 4. Final fallback: direct media_player.play_media
    log.info("[announce.roku] Fallback: play_media with URL")
    return await ha_client.call_service(ha_url, ha_token, "media_player", "play_media", entity_id, {
        "media_content_id": media_url,
        "media_content_type": "url",
    })


async def announce_webos(
    ha_url: str,
    ha_token: str,
    entity_id: str,
    media_url: str,
    volume: float,
    state: str = "unknown",
    attributes: dict | None = None,
    message: str = "",
) -> dict[str, Any]:
    """LG webOS TV: display on-screen toast notification AND play audio via play_media / AirPlay MA sibling."""
    ha_client = _get_ha_client()
    toast_text = (message or "SharedLLM Alert").strip()
    log.info(f"[announce.webos] Announcing on {entity_id}: toast='{toast_text[:50]}', media={media_url[:60]}")

    if state in ("off", "standby"):
        with contextlib.suppress(Exception):
            await ha_client.call_service(ha_url, ha_token, "media_player", "turn_on", entity_id, {})
            await asyncio.sleep(2)

    if volume > 0:
        with contextlib.suppress(Exception):
            await ha_client.call_service(
                ha_url, ha_token, "media_player", "volume_set", entity_id, {"volume_level": volume}
            )

    toast_ok = False
    companions: dict[str, Any] = {}
    with contextlib.suppress(Exception):
        all_states = await ha_client.get_all_states(ha_url, ha_token)
        services = await ha_client.get_services(ha_url, ha_token)
        companions = resolve_tv_companions(entity_id, all_states, services=services)

    # 1a. Send onscreen toast via notify.<lg_webos_tv_*> service if registered
    notify_svc = companions.get("webos_notify_service")
    if notify_svc:
        with contextlib.suppress(Exception):
            res_toast = await ha_client.call_service(
                ha_url, ha_token, "notify", notify_svc, None, {"message": toast_text}
            )
            if res_toast.get("ok"):
                toast_ok = True

    # 1b. Also send toast via webostv.command (system.notifications/createToast)
    if not toast_ok:
        with contextlib.suppress(Exception):
            res_cmd = await ha_client.call_service(
                ha_url,
                ha_token,
                "webostv",
                "command",
                entity_id,
                {
                    "command": "system.notifications/createToast",
                    "payload": {"message": toast_text},
                },
            )
            if res_cmd.get("ok"):
                toast_ok = True

    # 2. Play audio stream on the TV via media_player.play_media
    play_res = await ha_client.call_service(ha_url, ha_token, "media_player", "play_media", entity_id, {
        "media_content_id": media_url,
        "media_content_type": "url",
    })
    if play_res.get("ok"):
        return play_res

    # 3. Fallback: play audio via Music Assistant / AirPlay sibling (e.g. media_player.lg_webos_tv_un6955zuf_airplay)
    ma_sibling = companions.get("ma_sibling")
    if ma_sibling and ma_sibling != entity_id:
        log.info(f"[announce.webos] Trying AirPlay/MA sibling {ma_sibling}")
        ma_res = await ha_client.call_service(ha_url, ha_token, "media_player", "play_media", ma_sibling, {
            "media_content_id": media_url,
            "media_content_type": "url",
        })
        if ma_res.get("ok"):
            return ma_res

    if toast_ok:
        return {"ok": True, "method": "webos_toast"}
    return play_res


async def announce_android_tv(
    ha_url: str,
    ha_token: str,
    entity_id: str,
    media_url: str,
    volume: float,
    state: str = "unknown",
    attributes: dict | None = None,
    message: str = "",
) -> dict[str, Any]:
    """Android TV: wake TV via androidtv_remote / media_player, then route audio via Cast sibling,
    Music Assistant sibling, direct play_media, or ADB intent.
    """
    ha_client = _get_ha_client()
    attrs = attributes or {}
    supported_features = int(attrs.get("supported_features") or 0)
    log.info(f"[announce.android_tv] Dispatching to Android TV {entity_id} (state={state}, features={supported_features})")

    companions: dict[str, Any] = {}
    with contextlib.suppress(Exception):
        all_states = await ha_client.get_all_states(ha_url, ha_token)
        companions = resolve_tv_companions(entity_id, all_states)

    # 1. Wake the TV if off
    if state in ("off", "standby", "unavailable"):
        with contextlib.suppress(Exception):
            await ha_client.call_service(ha_url, ha_token, "media_player", "turn_on", entity_id, {})
        remote_sibling = companions.get("remote_sibling")
        if remote_sibling:
            with contextlib.suppress(Exception):
                await ha_client.call_service(ha_url, ha_token, "remote", "turn_on", remote_sibling, {})
        await asyncio.sleep(1.5)

    # 2. If entity is androidtv_remote (features == 153529, which does not support play_media URLs),
    #    prefer its Cast sibling (e.g. media_player.office_tv_chrome) or MA sibling first!
    cast_sibling = companions.get("cast_sibling")
    ma_sibling = companions.get("ma_sibling")

    if cast_sibling:
        log.info(f"[announce.android_tv] Routing audio to Cast sibling {cast_sibling}")
        if volume > 0:
            with contextlib.suppress(Exception):
                await ha_client.call_service(
                    ha_url, ha_token, "media_player", "volume_set", cast_sibling, {"volume_level": volume}
                )
        cast_res = await ha_client.call_service(ha_url, ha_token, "media_player", "play_media", cast_sibling, {
            "media_content_id": media_url,
            "media_content_type": "url",
        })
        if cast_res.get("ok"):
            return cast_res

    if ma_sibling and ma_sibling != entity_id:
        log.info(f"[announce.android_tv] Routing audio to Music Assistant sibling {ma_sibling}")
        ma_res = await ha_client.call_service(ha_url, ha_token, "media_player", "play_media", ma_sibling, {
            "media_content_id": media_url,
            "media_content_type": "url",
        })
        if ma_res.get("ok"):
            return ma_res

    # 3. Try direct media_player.play_media on entity_id
    log.info(f"[announce.android_tv] Trying direct play_media on {entity_id}")
    result = await ha_client.call_service(ha_url, ha_token, "media_player", "play_media", entity_id, {
        "media_content_id": media_url,
        "media_content_type": "url",
    })
    if result.get("ok"):
        return result

    # 4. Fallback: ADB intent to launch media URL
    log.info("[announce.android_tv] Trying ADB command to launch media")
    await ha_client.call_service(ha_url, ha_token, "androidtv", "adb_command", entity_id, {"command": "HOME"})
    await asyncio.sleep(1)

    return await ha_client.call_service(ha_url, ha_token, "androidtv", "adb_command", entity_id, {
        "command": f"am start -d '{media_url}' -a android.intent.action.VIEW"
    })


async def announce_samsung(
    ha_url: str,
    ha_token: str,
    entity_id: str,
    media_url: str,
    volume: float,
    state: str = "unknown",
    attributes: dict | None = None,
    message: str = "",
) -> dict[str, Any]:
    """Samsung TV (Tizen & Legacy Orsay): wake, set volume, play audio via MA player, DLNA renderer,
    direct play_media, or room Cast companion (for legacy Orsay TVs with attached Chromecast/Android TV).
    """
    ha_client = _get_ha_client()
    log.info(f"[announce.samsung] Preparing Samsung TV {entity_id} (state={state}) for announcement")

    initial_state = state
    was_off = initial_state in ("off", "unavailable", "standby")

    # Resolve companions (MA player, DLNA renderer, or room Cast companion)
    companions: dict[str, Any] = {}
    try:
        all_states = await ha_client.get_all_states(ha_url, ha_token)
        companions = resolve_tv_companions(entity_id, all_states)
    except Exception as e:
        log.warning(f"[announce.samsung] Failed to resolve Samsung companions: {e}")

    ma_entity = companions.get("ma_sibling")
    dlna_entity = companions.get("dlna_sibling")
    cast_sibling = companions.get("cast_sibling")

    # Power on if needed
    if was_off:
        log.info(f"[announce.samsung] TV is {initial_state}, turning on...")
        await ha_client.call_service(ha_url, ha_token, "media_player", "turn_on", entity_id, {})
        # If a room Cast companion is available on a legacy Samsung TV, we don't need a full 15s wait
        await asyncio.sleep(4 if cast_sibling else 10)

    # Set volume for announcement
    if volume > 0:
        log.info(f"[announce.samsung] Setting volume to {volume}")
        await ha_client.call_service(
            ha_url, ha_token, "media_player", "volume_set", entity_id, {"volume_level": volume}
        )
        await asyncio.sleep(0.5)

    # Unmute if needed
    attrs = attributes or {}
    if attrs.get("is_volume_muted"):
        log.info("[announce.samsung] Unmuting TV")
        await ha_client.call_service(
            ha_url, ha_token, "media_player", "volume_mute", entity_id, {"is_volume_muted": False}
        )
        await asyncio.sleep(0.5)

    playback_ok = False
    result: dict[str, Any] = {"ok": False}

    # Strategy 1: Music Assistant sibling (works even when MA entity was 'off' and app_id was None)
    if ma_entity:
        log.info(f"[announce.samsung] Using MA player for playback: {ma_entity}")
        result = await ha_client.call_service(
            ha_url, ha_token, "music_assistant", "play_announcement", ma_entity,
            {"url": media_url, "use_pre_announce": False},
        )
        if not result.get("ok"):
            result = await ha_client.call_service(
                ha_url, ha_token, "music_assistant", "play_media", ma_entity,
                {"media_id": media_url},
            )
        if not result.get("ok"):
            result = await ha_client.call_service(
                ha_url, ha_token, "media_player", "play_media", ma_entity,
                {"media_content_id": media_url, "media_content_type": "url"},
            )
        if result.get("ok"):
            playback_ok = True

    # Strategy 2: DLNA DMR sibling (e.g. media_player.tv_loft_samsung_q900)
    if not playback_ok and dlna_entity:
        log.info(f"[announce.samsung] Trying DLNA renderer {dlna_entity}")
        result = await ha_client.call_service(ha_url, ha_token, "media_player", "play_media", dlna_entity, {
            "media_content_id": media_url,
            "media_content_type": "music",
        })
        if result.get("ok"):
            playback_ok = True

    # Strategy 3: Room Cast companion (e.g. Master Bedroom Legacy Samsung UN46EH5300 + Master Bedroom Cast TV)
    if not playback_ok and cast_sibling:
        log.info(f"[announce.samsung] Routing legacy Samsung audio to room Cast companion {cast_sibling}")
        result = await ha_client.call_service(ha_url, ha_token, "media_player", "play_media", cast_sibling, {
            "media_content_id": media_url,
            "media_content_type": "url",
        })
        if result.get("ok"):
            playback_ok = True

    # Strategy 4: Direct play_media on Samsung entity
    if not playback_ok:
        log.info(f"[announce.samsung] Trying direct media_player.play_media on {entity_id}")
        result = await ha_client.call_service(ha_url, ha_token, "media_player", "play_media", entity_id, {
            "media_content_id": media_url,
            "media_content_type": "url",
        })
        if result.get("ok"):
            playback_ok = True

    if was_off and playback_ok and not cast_sibling:
        log.info("[announce.samsung] Waiting for announcement to complete before restoring TV to off...")
        await asyncio.sleep(5)
        log.info(f"[announce.samsung] Restoring TV to off state (was {initial_state})")
        await ha_client.call_service(ha_url, ha_token, "media_player", "turn_off", entity_id, {})

    return result


async def announce_bravia(
    ha_url: str,
    ha_token: str,
    entity_id: str,
    media_url: str,
    volume: float,
    state: str = "unknown",
    attributes: dict | None = None,
    message: str = "",
) -> dict[str, Any]:
    """Sony Bravia TV."""
    ha_client = _get_ha_client()
    log.info(f"[announce.bravia] Trying play_media on {entity_id}")
    return await ha_client.call_service(ha_url, ha_token, "media_player", "play_media", entity_id, {
        "media_content_id": media_url,
        "media_content_type": "url",
    })


async def announce_music_assistant(
    ha_url: str,
    ha_token: str,
    entity_id: str,
    media_url: str,
    volume: float,
    state: str = "unknown",
    attributes: dict | None = None,
    message: str = "",
) -> dict[str, Any]:
    """Music Assistant integration."""
    ha_client = _get_ha_client()
    log.info(f"[announce.mass] Using media_player.play_media on {entity_id}")
    if volume > 0:
        with contextlib.suppress(Exception):
            await ha_client.call_service(
                ha_url, ha_token, "media_player", "volume_set", entity_id, {"volume_level": volume}
            )
    return await ha_client.call_service(ha_url, ha_token, "media_player", "play_media", entity_id, {
        "media_content_id": media_url,
        "media_content_type": "url",
    })


async def announce_esphome(
    ha_url: str,
    ha_token: str,
    entity_id: str,
    media_url: str,
    volume: float,
    state: str = "unknown",
    attributes: dict | None = None,
    message: str = "",
) -> dict[str, Any]:
    """ESPHome media player."""
    ha_client = _get_ha_client()
    log.info(f"[announce.esphome] Trying play_media on {entity_id}")
    if volume > 0:
        with contextlib.suppress(Exception):
            await ha_client.call_service(
                ha_url, ha_token, "media_player", "volume_set", entity_id, {"volume_level": volume}
            )
    return await ha_client.call_service(ha_url, ha_token, "media_player", "play_media", entity_id, {
        "media_content_id": media_url,
        "media_content_type": "url",
    })


async def announce_dlna(
    ha_url: str,
    ha_token: str,
    entity_id: str,
    media_url: str,
    volume: float,
    state: str = "unknown",
    attributes: dict | None = None,
    message: str = "",
) -> dict[str, Any]:
    """DLNA renderer."""
    ha_client = _get_ha_client()
    log.info(f"[announce.dlna] Trying play_media on {entity_id}")
    return await ha_client.call_service(ha_url, ha_token, "media_player", "play_media", entity_id, {
        "media_content_id": media_url,
        "media_content_type": "url",
    })


async def announce_generic_tv(
    ha_url: str,
    ha_token: str,
    entity_id: str,
    media_url: str,
    volume: float,
    state: str = "unknown",
    attributes: dict | None = None,
    message: str = "",
) -> dict[str, Any]:
    """Generic TV fallback: check room companions first, then direct play_media."""
    ha_client = _get_ha_client()
    log.info(f"[announce.generic_tv] Trying play_media on {entity_id}")
    res = await ha_client.call_service(ha_url, ha_token, "media_player", "play_media", entity_id, {
        "media_content_id": media_url,
        "media_content_type": "url",
    })
    if res.get("ok"):
        return res
    with contextlib.suppress(Exception):
        all_states = await ha_client.get_all_states(ha_url, ha_token)
        companions = resolve_tv_companions(entity_id, all_states)
        for key in ("cast_sibling", "ma_sibling", "dlna_sibling"):
            sib = companions.get(key)
            if sib and sib != entity_id:
                sib_res = await ha_client.call_service(
                    ha_url, ha_token, "media_player", "play_media", sib,
                    {"media_content_id": media_url, "media_content_type": "url"},
                )
                if sib_res.get("ok"):
                    return sib_res
    return res


async def announce_speaker(
    ha_url: str,
    ha_token: str,
    entity_id: str,
    media_url: str,
    volume: float,
    state: str = "unknown",
    attributes: dict | None = None,
    message: str = "",
) -> dict[str, Any]:
    """Generic speaker."""
    ha_client = _get_ha_client()
    log.info(f"[announce.speaker] Trying play_media on {entity_id}")
    if volume > 0:
        with contextlib.suppress(Exception):
            await ha_client.call_service(
                ha_url, ha_token, "media_player", "volume_set", entity_id, {"volume_level": volume}
            )
    return await ha_client.call_service(ha_url, ha_token, "media_player", "play_media", entity_id, {
        "media_content_id": media_url,
        "media_content_type": "url",
    })


async def announce_unknown(
    ha_url: str,
    ha_token: str,
    entity_id: str,
    media_url: str,
    volume: float,
    state: str = "unknown",
    attributes: dict | None = None,
    message: str = "",
) -> dict[str, Any]:
    """Unknown device: try generic play_media, log all attributes for later identification."""
    ha_client = _get_ha_client()
    log.info(f"[announce.unknown] Unknown device type for {entity_id}, trying generic play_media")
    log.info(
        f"[announce.unknown] Playing media on {entity_id}: {media_url[:60]} "
        f"(app_id={(attributes or {}).get('app_id', '?')})"
    )
    return await ha_client.call_service(ha_url, ha_token, "media_player", "play_media", entity_id, {
        "media_content_id": media_url,
        "media_content_type": "url",
    })


TV_HANDLER_MAP = {
    "cast": announce_cast,
    "roku": announce_roku,
    "webos": announce_webos,
    "android_tv": announce_android_tv,
    "samsung": announce_samsung,
    "bravia": announce_bravia,
    "music_assistant": announce_music_assistant,
    "esphome": announce_esphome,
    "dlna": announce_dlna,
    "generic_tv": announce_generic_tv,
    "speaker": announce_speaker,
    "unknown": announce_unknown,
}


async def dispatch_announce(
    ha_url: str,
    ha_token: str,
    entity_id: str,
    media_url: str,
    volume: float,
    state: str,
    attributes: dict,
    loaded_components: set[str] | None = None,
    message: str = "",
) -> dict[str, Any]:
    """Dispatch announcement/alarm/intercom audio to the appropriate TV or speaker handler."""
    tv_type = detect_tv_type(entity_id, state, attributes, loaded_components)

    # If unknown, try web search fallback
    if tv_type == "unknown":
        log.info(f"[announce] Unknown device type for {entity_id}, attempting web search...")
        search_result = await search_device_type(entity_id, attributes, loaded_components)
        if search_result:
            tv_type = search_result
            log.info(f"[announce] Web search identified type as: {tv_type}")
        else:
            log.warning(f"[announce] Could not identify device type for {entity_id}, using unknown handler")

    handler = TV_HANDLER_MAP.get(tv_type, announce_unknown)

    log.info(
        f"[announce] Detected type: {tv_type} for {entity_id} "
        f"(app_id={attributes.get('app_id', '?')}, device_class={attributes.get('device_class', '?')}, "
        f"features={attributes.get('supported_features', '?')})"
    )
    return await handler(ha_url, ha_token, entity_id, media_url, volume, state, attributes, message=message)


async def stop_tv_or_media_alert(
    ha_url: str,
    ha_token: str,
    entity_id: str,
    all_states: list[dict] | None = None,
) -> dict[str, Any]:
    """Stop an active alarm, timer, or intercom stream on a TV or HA media_player.

    Handles stopping across the primary entity AND any active companion entities
    (Roku ECP Home key to exit Media Assistant 782875, Cast sibling, Music Assistant sibling, DLNA sibling).
    """
    ha_client = _get_ha_client()
    device_discovery = _get_device_discovery()
    states = all_states
    if states is None:
        with contextlib.suppress(Exception):
            states = await ha_client.get_all_states(ha_url, ha_token)
    states = states or []

    state_obj = next((s for s in states if s.get("entity_id") == entity_id), {})
    attrs = state_obj.get("attributes", {}) if isinstance(state_obj, dict) else {}
    st_val = state_obj.get("state", "unknown") if isinstance(state_obj, dict) else "unknown"
    tv_type = detect_tv_type(entity_id, st_val, attrs)
    companions = resolve_tv_companions(entity_id, states)

    stopped_targets: list[str] = []

    # 1. Roku: send ECP Home keypress to exit Media Assistant 782875 immediately
    if tv_type == "roku":
        with contextlib.suppress(Exception):
            roku_info = await device_discovery.discover_device(entity_id, ha_url, ha_token, device_type="roku")
            roku_ip = roku_info.get("ip") if roku_info else None
            if roku_ip:
                import aiohttp
                async with aiohttp.ClientSession(
                    timeout=aiohttp.ClientTimeout(total=3),
                    connector=aiohttp.TCPConnector(ssl=False),
                ) as client:
                    await client.post(f"http://{roku_ip}:8060/keypress/Home")
                    stopped_targets.append(f"roku_ecp:{roku_ip}")

    # 2. Stop playback on primary entity and all relevant companions
    candidate_entities = [
        entity_id,
        companions.get("primary_entity"),
        companions.get("cast_sibling"),
        companions.get("ma_sibling"),
        companions.get("dlna_sibling"),
    ]
    seen: set[str] = set()
    for target in candidate_entities:
        if not target or target in seen or not str(target).startswith("media_player."):
            continue
        seen.add(target)
        with contextlib.suppress(Exception):
            res = await ha_client.call_service(ha_url, ha_token, "media_player", "media_stop", target, {})
            if res.get("ok"):
                stopped_targets.append(target)

    return {"ok": bool(stopped_targets), "entity_id": entity_id, "platform": tv_type, "stopped": stopped_targets}


_CORRUPT_ENTITY_RE = re.compile(r"^media_player\.[0-9_]+$")
_TARGET_NAME_NOISE_RE = re.compile(r"\b(remote|cast|airplay|web player)\b.*$", re.IGNORECASE)
_TARGET_STATE_PRIORITY = {
    "playing": 0,
    "idle": 1,
    "on": 2,
    "paused": 3,
    "buffering": 4,
    "off": 5,
    "standby": 6,
}


def _target_group_key(name: str) -> str:
    """Group the many HA wrappers for one physical room/device.

    ``Office TV``, ``Office TV Remote`` and ``Office TV Cast`` are three
    entities for the same television. One row is enough in a picker.
    """
    cleaned = _TARGET_NAME_NOISE_RE.sub("", name or "")
    cleaned = re.sub(r"[^a-z0-9]+", " ", cleaned.lower()).strip()
    return cleaned or (name or "").lower().strip()


def _target_priority(device: dict[str, Any]) -> tuple:
    """Lower is better when choosing one entity for a physical target."""
    state = str(device.get("state") or "").lower()
    features = device.get("supported_features") or 0
    try:
        features = int(features)
    except (TypeError, ValueError):
        features = 0
    return (
        0 if state not in ("unavailable", "unknown") else 1,
        0 if device.get("announce_capable") else 1,
        0 if (device.get("device_class") or "").lower() == "speaker" else 1,
        0 if device.get("kind") == "speaker" else 1,
        -features,
        len(device.get("id") or ""),
        str(device.get("id") or ""),
    )


def _dedupe_media_player_targets(devices: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep the best entity for each physical target, preserving first-seen order."""
    best: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for device in devices:
        key = _target_group_key(str(device.get("name") or ""))
        if key not in best:
            best[key] = device
            order.append(key)
        elif _target_priority(device) < _target_priority(best[key]):
            best[key] = device
    return [best[key] for key in order]


async def classify_communication_devices(
    ha_url: str,
    ha_token: str,
    all_states: list[dict] | None = None,
    esp_devices: list[dict] | None = None,
) -> list[dict[str, Any]]:
    """Classify all household TVs, ESPHome devices, and HA speakers with OS version & communication capabilities.

    Reusable across Timers/Alarms (`/execute/timers/devices`), Announcements, and Intercoms (`/execute/intercom/devices`).
    """
    ha_client = _get_ha_client()
    device_registry = _get_device_registry()

    states = all_states
    if states is None and ha_url and ha_token:
        with contextlib.suppress(Exception):
            states = await ha_client.get_all_states(ha_url, ha_token)
    states = states or []

    devices: list[dict[str, Any]] = []
    ha_devices: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    # 1. Include ESPHome devices (speakers + screen-only visual flash devices)
    for esp in esp_devices or []:
        eid = esp.get("entity_id") or esp.get("id") or ""
        if not eid or eid in seen_ids:
            continue
        seen_ids.add(eid)
        has_mic = bool(esp.get("has_microphone"))
        companions = resolve_tv_companions(eid, states)
        if companions.get("room_assist_satellite"):
            has_mic = True
        devices.append({
            **esp,
            "id": eid,
            "entity_id": eid,
            "kind": "esphome",
            "platform": "esphome",
            "os_version": esp.get("os_version") or "ESPHome",
            "has_microphone": has_mic,
            "intercom_mode": "twoway" if has_mic else "oneway",
            "assist_satellite_entity": companions.get("room_assist_satellite"),
            "communication_methods": esp.get("communication_methods") or ["esphome_api", "ha_play_media"],
        })

    # 2. Classify HA media_player entities (TVs & Speakers)
    for st in states:
        eid = st.get("entity_id", "")
        if not eid.startswith("media_player.") or eid in seen_ids:
            continue
        if not usable_entity_id(eid):
            continue
        # Skip corrupt numeric/browser entities like media_player.85_73
        if _CORRUPT_ENTITY_RE.match(eid):
            continue

        attrs = st.get("attributes") or {}
        state_val = st.get("state") or "unknown"
        friendly = (attrs.get("friendly_name") or eid).strip()
        # A serialized user record is not a device name, even when HA managed
        # to slugify enough of it into an otherwise valid entity id.
        if re.match(r"^[0-9.:_ -]+$", friendly) or friendly.startswith("{"):
            continue

        tv_type = detect_tv_type(eid, state_val, attrs)
        companions = resolve_tv_companions(eid, states)

        # Avoid duplicate Music Assistant / DLNA wrapper entries when the primary TV entity is already in HA
        primary_ent = companions.get("primary_entity")
        if primary_ent and primary_ent != eid and primary_ent in {s.get("entity_id") for s in states}:
            # Keep the wrapper when the primary is unavailable (for example
            # Gracie's Roku TV) or cannot announce. A TV entity often exposes
            # only power/volume, while its Cast or MA sibling is the one that
            # can speak a timer alert.
            primary_state = next((s for s in states if s.get("entity_id") == primary_ent), {})
            prim_state = primary_state.get("state", "unknown")
            prim_attrs = primary_state.get("attributes") or {}
            prim_features = int(prim_attrs.get("supported_features") or 0)
            if prim_state != "unavailable" and (prim_features & SUPPORT_MEDIA_ANNOUNCE):
                continue

        # Look up cached network metadata (IP, OS version, model) if available
        cached_dev = None
        if device_registry is not None:
            with contextlib.suppress(Exception):
                cached_dev = await device_registry.get_device(eid)

        meta = (cached_dev or {}).get("metadata") or {}
        if isinstance(meta, str):
            import json
            with contextlib.suppress(Exception):
                meta = json.loads(meta)
        if not isinstance(meta, dict):
            meta = {}

        device_class = str(attrs.get("device_class") or "").lower()
        try:
            supported_features = int(attrs.get("supported_features") or 0)
        except (TypeError, ValueError):
            supported_features = 0
        # MEDIA_ANNOUNCE is the feature an alarm/TTS target actually needs.
        # TV remotes and cast wrappers in this house carry the low-bit feature
        # sets but not this bit, so they stop appearing as speakers.
        announce_capable = bool(supported_features & SUPPORT_MEDIA_ANNOUNCE)

        is_tv = (
            tv_type in TV_PLATFORMS
            or device_class == "tv"
            or (attrs.get("icon") or "") == "mdi:television"
            or "tv" in eid.lower()
            or "tv" in friendly.lower()
        )
        is_esphome = tv_type == "esphome"
        kind = "esphome" if is_esphome else ("tv" if is_tv else "speaker")

        os_version = meta.get("os_version") or ""
        if not os_version:
            if tv_type == "roku":
                os_version = "Roku OS (ECP + Media Assistant)"
            elif tv_type == "webos":
                os_version = "LG webOS"
            elif tv_type == "samsung":
                os_version = "Samsung Orsay (Legacy)" if "master_bedroom" in eid else "Samsung Tizen"
            elif tv_type == "android_tv":
                os_version = "Android TV"
            elif tv_type == "cast":
                os_version = "Google Cast"
            elif tv_type == "esphome":
                os_version = "ESPHome"
            else:
                os_version = tv_type.replace("_", " ").title()

        comm_methods = meta.get("communication_methods") or []
        if not comm_methods:
            if tv_type == "roku":
                comm_methods = ["ecp_media_assistant_782875", "ecp_keypress", "music_assistant", "ha_play_media"]
            elif tv_type == "webos":
                comm_methods = ["ha_notify_toast", "webostv_command_toast", "ha_play_media", "music_assistant"]
            elif tv_type == "android_tv":
                comm_methods = ["androidtv_remote", "cast_sibling", "music_assistant", "ha_play_media", "adb"]
            elif tv_type == "samsung":
                comm_methods = ["music_assistant", "dlna_dmr", "ha_play_media", "room_cast_companion"]
            elif tv_type == "cast":
                comm_methods = ["cast_direct", "music_assistant", "ha_play_media"]
            else:
                comm_methods = ["ha_play_media"]

        assist_sat = companions.get("room_assist_satellite")
        has_mic = bool(assist_sat) or is_esphome or bool(meta.get("supports_ecs_microphone"))

        seen_ids.add(eid)
        ha_devices.append({
            "id": eid,
            "entity_id": eid,
            "name": friendly,
            "kind": kind,
            "platform": tv_type,
            "os_version": os_version,
            "model": meta.get("model") or "",
            "ip": (cached_dev or {}).get("ip") or "",
            "state": state_val,
            "device_class": device_class,
            "supported_features": supported_features,
            "announce_capable": announce_capable,
            "has_speaker": True,
            "has_screen": is_tv,
            "has_microphone": has_mic,
            "intercom_mode": "twoway" if assist_sat or is_esphome else "oneway",
            "assist_satellite_entity": assist_sat,
            "companions": {
                k: v for k, v in companions.items()
                if v and k in ("ma_sibling", "cast_sibling", "dlna_sibling", "remote_sibling", "webos_notify_service")
            },
            "communication_methods": comm_methods,
        })

    devices.extend(_dedupe_media_player_targets(ha_devices))
    return devices
