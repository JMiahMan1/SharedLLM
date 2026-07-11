# services/execution/ha_client.py
"""
Asynchronous Home Assistant REST client using aiohttp.
"""
import logging
from contextlib import asynccontextmanager

import aiohttp

log = logging.getLogger("execution.ha_client")

import re

from services.execution.http_client import get_session, host_of

_TIMEOUT = aiohttp.ClientTimeout(total=45.0, connect=15.0)


@asynccontextmanager
async def _ha_session(ha_url: str, verify: bool = False):
    """Yield the pooled HA session WITHOUT closing it (reused across calls)."""
    yield await get_session(host_of(ha_url), verify=verify)


def authorize_action(user_context: dict, domain: str, action: str) -> bool:
    """
    Validates if a user is authorized to perform a specific action.
    Strictly enforces the 'Admin-Only' rule for sensitive environmental changes.
    """
    is_admin = user_context.get("is_admin", False)

    # SENSITIVE ACTIONS (Admins Only)
    sensitive_actions = {
        "lock": ["unlock", "open"],
        "cover": ["open"],
        "alarm_control_panel": ["alarm_disarm"],
        "climate": ["set_temperature"], # Some homes consider this sensitive
    }

    if domain in sensitive_actions:
        if action in sensitive_actions[domain] and not is_admin:
            log.warning(f"[Security] BLOCK: Non-admin user '{user_context.get('user')}' attempted '{action}' on '{domain}'")
            return False

    return True

def sanitize_entity_id(domain: str, llm_target: str) -> str:
    """
    Ensures the entity_id is well-formed for Home Assistant.
    Example: 'piano-lamp' -> 'light.piano_lamp'
    """
    if not llm_target: return ""

    # If already has a dot, assume it's domain.name and sanitize the name part
    if "." in llm_target:
        prefix, name = llm_target.split(".", 1)
        # Verify prefix matches domain or use domain if prefix is generic
        target_domain = prefix if prefix in ("light", "switch", "media_player", "climate", "cover", "sensor", "binary_sensor") else domain
    else:
        target_domain = domain
        name = llm_target

    # Strip non-alphanumeric, replace with underscore
    sanitized_name = re.sub(r'[^a-z0-9]+', '_', name.lower()).strip('_')
    return f"{target_domain}.{sanitized_name}"

async def call_service(
    ha_url: str,
    ha_token: str,
    domain: str,
    service: str,
    entity_id: str = "",
    service_data: dict | None = None,
    return_response: bool = False,
) -> dict:
    """Call a Home Assistant service and return the response JSON."""
    if not ha_url:
        log.error("[ha_client] ha_url is None or empty. Cannot call service.")
        return {"ok": False, "error": "Home Assistant URL not configured for this user."}

    headers = {"Authorization": f"Bearer {ha_token}", "Content-Type": "application/json"}
    url = f"{ha_url.rstrip('/')}/api/services/{domain}/{service}"
    if return_response:
        url += "?return_response"
    payload: dict = {}
    if entity_id:
        payload["entity_id"] = entity_id
    if service_data:
        payload.update(service_data)

    async with _ha_session(ha_url) as client:
        try:
            log.info(f"HA CALL: {domain}.{service} -> {entity_id or '(no target)'} | url={url} | payload={payload}")
            async with client.post(url, headers=headers, json=payload, timeout=_TIMEOUT) as resp:
                resp.raise_for_status()
                log.info(f"[ha_client] {domain}.{service} OK (HTTP {resp.status})")
                if return_response:
                    service_response = await resp.json()
                    return {"ok": True, "status_code": resp.status, "service_response": service_response}
                return {"ok": True, "status_code": resp.status}
        except aiohttp.ClientResponseError as e:
            log.error(f"[ha_client] HTTP error: {e}")
            detail = ""
            try:
                resp_detail = await client.get(url, headers=headers, json=payload, timeout=_TIMEOUT)
                await resp_detail.release()
                detail = str(e)
            except:
                detail = str(e)
            if e.status in (401, 403):
                return {"ok": False, "error": "Your Home Assistant token is invalid or expired. Please update it in Jarvis.", "status_code": e.status}
            return {"ok": False, "error": f"HA returned {e.status}: {detail}", "status_code": e.status}
        except aiohttp.ClientError as e:
            log.error(f"[ha_client] Request error: {e}")
            return {"ok": False, "error": f"Home Assistant is unreachable: {e}"}
        except Exception as e:
            log.error(f"[ha_client] Unexpected error: {e}")
            return {"ok": False, "error": str(e)}

async def get_state(ha_url: str, ha_token: str, entity_id: str) -> dict | None:
    """Retrieve the current state of an HA entity."""
    if not ha_url:
        log.error("[ha_client] ha_url is None or empty. Cannot get state.")
        return None

    headers = {"Authorization": f"Bearer {ha_token}"}
    url = f"{ha_url.rstrip('/')}/api/states/{entity_id}"

    async with _ha_session(ha_url) as client:
        try:
            log.debug(f"[ha_client] GET {url}")
            async with client.get(url, headers=headers, timeout=_TIMEOUT) as resp:
                if resp.status == 404:
                    return None
                resp.raise_for_status()
                return await resp.json()
        except Exception as e:
            log.error(f"[ha_client] get_state({entity_id}) failed: {type(e).__name__}: {e}")
            return None

async def get_all_states(ha_url: str, ha_token: str) -> list:
    """Retrieve all entity states from HA."""
    if not ha_url:
        log.error("[ha_client] ha_url is None or empty. Cannot get states.")
        return []

    headers = {"Authorization": f"Bearer {ha_token}"}
    url = f"{ha_url.rstrip('/')}/api/states"

    async with _ha_session(ha_url) as client:
        try:
            log.debug(f"[ha_client] GET {url}")
            async with client.get(url, headers=headers, timeout=_TIMEOUT) as resp:
                resp.raise_for_status()
                return await resp.json()
        except Exception as e:
            log.error(f"[ha_client] get_all_states failed: {type(e).__name__}: {e}")
            return []

async def get_config(ha_url: str, ha_token: str) -> dict:
    """Retrieve the core configuration from HA."""
    if not ha_url:
        return {}
    headers = {"Authorization": f"Bearer {ha_token}"}
    url = f"{ha_url.rstrip('/')}/api/config"
    async with _ha_session(ha_url) as client:
        try:
            async with client.get(url, headers=headers, timeout=_TIMEOUT) as resp:
                resp.raise_for_status()
                return await resp.json()
        except Exception as e:
            log.error(f"[ha_client] get_config failed: {e}")
            return {}

async def get_config_entries(ha_url: str, ha_token: str, domain: str = "") -> list:
    """Retrieve config entries from HA, optionally filtered by domain."""
    if not ha_url:
        return []
    headers = {"Authorization": f"Bearer {ha_token}"}
    url = f"{ha_url.rstrip('/')}/api/config/config_entries/entry"
    if domain:
        url += f"?domain={domain}"
    async with _ha_session(ha_url) as client:
        try:
            async with client.get(url, headers=headers, timeout=_TIMEOUT) as resp:
                resp.raise_for_status()
                return await resp.json()
        except Exception as e:
            log.error(f"[ha_client] get_config_entries failed: {e}")
            return []

async def find_mass_config_entry(ha_url: str, ha_token: str) -> str:
    """Find the Music Assistant config entry ID from HA."""
    entries = await get_config_entries(ha_url, ha_token, "music_assistant")
    for entry in entries:
        if entry.get("domain") == "music_assistant":
            entry_id = entry.get("entry_id", "")
            log.info(f"[ha_client] Found MA config entry: {entry_id}")
            return entry_id
    return ""

async def get_states(ha_url: str, ha_token: str) -> list:
    """Retrieve all states from HA."""
    if not ha_url:
        log.error("[ha_client] ha_url is None or empty. Cannot get states.")
        return []

    headers = {"Authorization": f"Bearer {ha_token}"}
    url = f"{ha_url.rstrip('/')}/api/states"

    async with _ha_session(ha_url) as client:
        try:
            log.info(f"[ha_client] GET {url}")
            async with client.get(url, headers=headers, timeout=_TIMEOUT) as resp:
                resp.raise_for_status()
                return await resp.json()
        except Exception as e:
            log.error(f"[ha_client] get_states failed: {type(e).__name__}: {e}")
            return []  # Ensure empty list on error

async def get_history(ha_url: str, ha_token: str, entity_id: str, days: int = 1) -> list:
    """Retrieve history for a specific entity from HA."""
    if not ha_url:
        log.error("[ha_client] ha_url is None or empty. Cannot get history.")
        return []

    import datetime
    start_time = (datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=days)).isoformat()
    headers = {"Authorization": f"Bearer {ha_token}"}
    url = f"{ha_url.rstrip('/')}/api/history/period/{start_time}"
    params = {"filter_entity_id": entity_id, "no_attributes": ""}

    async with _ha_session(ha_url) as client:
        try:
            log.info(f"[ha_client] GET {url}")
            async with client.get(url, headers=headers, params=params, timeout=_TIMEOUT) as resp:
                resp.raise_for_status()
                data = await resp.json()
                # HA returns a list of lists (one per entity)
                return data[0] if data else []
        except Exception as e:
            log.error(f"[ha_client] get_history({entity_id}) failed: {e}")
            return []

async def get_logbook(ha_url: str, ha_token: str, entity_id: str, days: int = 1) -> list:
    """Retrieve logbook entries for a specific entity from HA."""
    if not ha_url:
        log.error("[ha_client] ha_url is None or empty. Cannot get logbook.")
        return []

    import datetime
    start_time = (datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=days)).isoformat()
    headers = {"Authorization": f"Bearer {ha_token}"}
    url = f"{ha_url.rstrip('/')}/api/logbook/{start_time}"
    params = {"entity": entity_id}

    async with _ha_session(ha_url) as client:
        try:
            log.info(f"[ha_client] GET {url} | entity={entity_id}")
            async with client.get(url, headers=headers, params=params, timeout=_TIMEOUT) as resp:
                resp.raise_for_status()
                return await resp.json()
        except Exception as e:
            log.error(f"[ha_client] get_logbook({entity_id}) failed: {e}")
            return []

async def get_areas(ha_url: str, ha_token: str) -> dict:
    """Retrieve mapping of entity_id to area_name using HA Template API."""
    if not ha_url:
        return {}

    headers = {"Authorization": f"Bearer {ha_token}", "Content-Type": "application/json"}
    url = f"{ha_url.rstrip('/')}/api/template"

    # Standard Jinja2 template to list all entity IDs and their area names
    template = """
    [
      {%- for state in states %}
      {
        "eid": "{{ state.entity_id }}",
        "a": "{{ area_name(state.entity_id) or '' }}"
      }{{ "," if not loop.last }}
      {%- endfor %}
    ]
    """

    async with _ha_session(ha_url) as client:
        try:
            async with client.post(url, headers=headers, json={"template": template}, timeout=_TIMEOUT) as resp:
                resp.raise_for_status()
                raw_data = await resp.json()
                # Convert list of dicts to a single mapping dict
                if isinstance(raw_data, list):
                    return {item["eid"]: item["a"] for item in raw_data if item.get("a")}
                return {}
        except Exception as e:
            log.error(f"[ha_client] get_areas failed: {e}")
            return {}
    return {}  # Fallback

async def resolve_entity_by_name(ha_url: str, ha_token: str, device_name: str, domain: str = "media_player", media_type: str | None = None) -> str | None:
    """
    Resolve a human-readable device name to an HA entity_id.
    Searches all states for entities matching the device_name in their friendly_name or entity_id.
    Prefers exact matches over partial matches.
    
    When media_type is provided:
    - "music": prefers entities with Music Assistant Queue (active_queue attribute)
    - "video": prefers entities with Cast capability (Default Media Receiver) or Android TV (device_class=tv)
    """
    if not ha_url or not device_name:
        return None

    states = await get_states(ha_url, ha_token)
    if not states:
        return None

    search = device_name.lower().strip()
    candidates = []

    for state in states:
        entity_id = state.get("entity_id", "")
        if not entity_id.startswith(f"{domain}."):
            continue

        friendly_name = state.get("attributes", {}).get("friendly_name", "").lower()
        eid_base = entity_id.lower().replace(f"{domain}.", "")

        # Score: exact match gets highest priority
        score = 0

        # Exact friendly name match (highest priority)
        if friendly_name == search:
            score += 100
        # Exact entity_id base match (e.g., "office_tv" matches "media_player.office_tv")
        elif eid_base == search.replace(" ", "_"):
            score += 90
        # Starts with search (e.g., "office tv" matches "office tv chrome")
        elif friendly_name.startswith(search):
            score += 50
        elif eid_base.startswith(search.replace(" ", "_")):
            score += 40
        # Contains search (fallback)
        elif search in friendly_name:
            score += 10
        elif search.replace(" ", "_") in eid_base:
            score += 5

        # Bonus for word-level matches
        for word in search.split():
            if word in friendly_name:
                score += 3
            if word in eid_base:
                score += 2

        # Bonus for device_class match (prefer actual TVs over speakers when searching for TV)
        attrs = state.get("attributes", {})
        device_class = attrs.get("device_class", "")
        if device_class == "tv":
            score += 200
        elif device_class == "speaker":
            score += 50

        # Context-aware bonuses based on media_type
        if media_type == "music":
            # Prefer Music Assistant Queue entities for music playback
            if attrs.get("active_queue"):
                score += 500
            if attrs.get("mass_player_type"):
                score += 200
        elif media_type == "video":
            # Prefer Cast-capable or Android TV entities for video
            if attrs.get("app_name") == "Default Media Receiver":
                score += 500
            if attrs.get("app_name") == "com.google.android.apps.mediashell":
                score += 400
            if device_class == "tv":
                score += 300

        # Penalty for numeric suffixes (e.g., "office_tv_3" when searching "office tv")
        if search.replace(" ", "_") in eid_base and eid_base != search.replace(" ", "_"):
            if any(c.isdigit() for c in eid_base.split("_")[-1:]):
                score -= 5

        if score > 0:
            candidates.append((score, entity_id))

    if not candidates:
        return None

    # Return highest scoring candidate
    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates[0][1]
