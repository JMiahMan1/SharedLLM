# services/execution/ha_client.py
"""
Asynchronous Home Assistant REST client using aiohttp.
"""
import logging
import time
from contextlib import asynccontextmanager

import aiohttp

log = logging.getLogger("execution.ha_client")

import re

from services.execution import credential_alerts
from services.execution.http_client import get_session, host_of

_TIMEOUT = aiohttp.ClientTimeout(total=45.0, connect=15.0)


@asynccontextmanager
async def _ha_session(ha_url: str, verify: bool = True):
    """Yield the pooled HA session WITHOUT closing it (reused across calls)."""
    yield await get_session(host_of(ha_url), verify=verify)


def authorize_action(user_context: dict, domain: str, action: str) -> bool:
    """
    Validates if a user is authorized to perform a specific action.

    Only *physical security* actions live here, because they stay admin-only no
    matter who the entity is assigned to: opening a garage or disarming an alarm
    is not a household-permission decision. Everything else is gated per entity
    by `entity_access.verify_entity_access`, which honours entity protection --
    that is why `climate` is deliberately absent from this table. A setpoint is
    only as sensitive as the thermostat it belongs to.
    """
    is_admin = user_context.get("is_admin", False)
    # A voice can be imitated or replayed, so recognising one never opens the
    # house, whoever it sounds like.
    by_voice = user_context.get("identified_by") == "voice"

    # SENSITIVE ACTIONS (Admins Only)
    sensitive_actions = {
        "lock": ["unlock", "open"],
        "cover": ["open"],
        "alarm_control_panel": ["alarm_disarm"],
    }

    if domain in sensitive_actions and action in sensitive_actions[domain] and (by_voice or not is_admin):
        reason = "recognised by voice" if by_voice else "non-admin"
        log.warning(f"[Security] BLOCK: {reason} user '{user_context.get('user')}' attempted '{action}' on '{domain}'")
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
            except Exception:
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

async def get_state(ha_url: str, ha_token: str, entity_id: str, account: str = "") -> dict | None:
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
                payload = await resp.json()
                await credential_alerts.auth_ok("home_assistant", account or "the stored login")
                return payload
        except Exception as e:
            status = getattr(e, "status", None)
            if status in (401, 403):
                await credential_alerts.auth_failed("home_assistant", account or "the stored login", f"HTTP {status}")
            log.error(f"[ha_client] get_state({entity_id}) failed: {type(e).__name__}: {e}")
            return None

async def get_all_states(ha_url: str, ha_token: str, account: str = "") -> list:
    """Retrieve all entity states from HA."""
    return await get_states(ha_url, ha_token, account)

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

async def get_states(ha_url: str, ha_token: str, account: str = "") -> list:
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
                payload = await resp.json()
                await credential_alerts.auth_ok("home_assistant", account or "the stored login")
                return payload
        except Exception as e:
            status = getattr(e, "status", None)
            if status in (401, 403):
                await credential_alerts.auth_failed("home_assistant", account or "the stored login", f"HTTP {status}")
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

async def get_areas(ha_url: str, ha_token: str, account: str = "") -> dict:
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
                raw_data = await resp.json(content_type=None)
                await credential_alerts.auth_ok("home_assistant", account or "the stored login")
                # Convert list of dicts to a single mapping dict
                if isinstance(raw_data, list):
                    return {item["eid"]: item["a"] for item in raw_data if item.get("a")}
                return {}
        except Exception as e:
            status = getattr(e, "status", None)
            if status in (401, 403):
                await credential_alerts.auth_failed("home_assistant", account or "the stored login", f"HTTP {status}")
            log.error(f"[ha_client] get_areas failed: {e}")
            return {}
    return {}  # Fallback

_ENTITY_ID_RE = re.compile(r"^[a-z0-9_]+\.[a-z0-9_]+$")
# media_player -> remote lookups barely change; one HA round-trip per key press
# would make the Remote page's d-pad feel sluggish.
_REMOTE_CACHE: dict[tuple[str, str], tuple[float, str]] = {}
_REMOTE_CACHE_TTL = 600.0


async def _render_template(ha_url: str, ha_token: str, template: str) -> str | None:
    headers = {"Authorization": f"Bearer {ha_token}", "Content-Type": "application/json"}
    url = f"{ha_url.rstrip('/')}/api/template"
    async with _ha_session(ha_url) as client:
        try:
            async with client.post(url, headers=headers, json={"template": template}, timeout=_TIMEOUT) as resp:
                resp.raise_for_status()
                return (await resp.text()).strip()
        except Exception as e:
            log.warning(f"[ha_client] template render failed: {type(e).__name__}: {e}")
            return None


def _remote_by_name(all_states: list, media_entity_id: str) -> str | None:
    """A remote.* entity whose name matches the media player's (heuristic)."""
    media_friendly = ""
    for state in all_states:
        if state.get("entity_id") == media_entity_id:
            media_friendly = (state.get("attributes", {}).get("friendly_name") or "").lower()
            break
    media_short = media_entity_id.split(".")[-1].replace("_", " ")
    for state in all_states:
        eid = state.get("entity_id", "")
        if not eid.startswith("remote."):
            continue
        friendly = (state.get("attributes", {}).get("friendly_name") or "").lower()
        if (media_friendly and media_friendly in friendly) or (media_short and media_short in friendly):
            return eid
    base = media_entity_id.replace("media_player.", "remote.", 1)
    if any(state.get("entity_id") == base for state in all_states):
        return base
    return None


async def find_companion_remote(ha_url: str, ha_token: str, media_entity_id: str) -> str:
    """The ``remote.*`` entity that sends keys to the same device as a media player.

    The two are rarely named alike (live: ``media_player.office_tv`` is driven by
    ``remote.office_tv_remote``, ``media_player.gracies_tv`` by
    ``remote.28_tcl_roku_tv``), and Home Assistant answers a service call to a
    non-existent entity with success -- so guessing the name made remote keys
    vanish silently. Ask HA's device registry first, then match by name, and only
    then fall back to the same-named entity.
    """
    key = (ha_url, media_entity_id)
    hit = _REMOTE_CACHE.get(key)
    now = time.monotonic()
    if hit and now - hit[0] < _REMOTE_CACHE_TTL:
        return hit[1]

    remote: str | None = None
    if _ENTITY_ID_RE.match(media_entity_id):
        rendered = await _render_template(
            ha_url,
            ha_token,
            "{% set d = device_id('" + media_entity_id + "') %}"
            "{% if d %}{{ device_entities(d) | select('match', 'remote\\.') | first | default('', true) }}{% endif %}",
        )
        if rendered and _ENTITY_ID_RE.match(rendered) and rendered.startswith("remote."):
            remote = rendered
    if not remote:
        remote = _remote_by_name(await get_states(ha_url, ha_token), media_entity_id)
    remote = remote or media_entity_id.replace("media_player.", "remote.", 1)
    _REMOTE_CACHE[key] = (now, remote)
    return remote


async def resolve_entity_by_name(ha_url: str, ha_token: str, device_name: str, domain: str = "media_player", media_type: str | None = None) -> str | None:
    """
    Resolve a human-readable device name to an HA entity_id.
    Searches all states for entities matching the device_name in their friendly_name or entity_id.
    Prefers exact matches over partial matches. Name-match score always dominates:
    capability bonuses only break ties between candidates with the same name-match
    score, so a well-named entity is never beaten by an unrelated capable one.

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

        # Name-match score. This always outranks capability bonuses so an
        # unrelated but capable entity can never beat a well-named one.
        name_score = 0

        # Exact friendly name match (highest priority)
        if friendly_name == search:
            name_score += 100
        # Exact entity_id base match (e.g., "office_tv" matches "media_player.office_tv")
        elif eid_base == search.replace(" ", "_"):
            name_score += 90
        # Starts with search (e.g., "office tv" matches "office tv chrome")
        elif friendly_name.startswith(search):
            name_score += 50
        elif eid_base.startswith(search.replace(" ", "_")):
            name_score += 40
        # Contains search (fallback)
        elif search in friendly_name:
            name_score += 10
        elif search.replace(" ", "_") in eid_base:
            name_score += 5

        # Bonus for word-level matches
        for word in search.split():
            if word in friendly_name:
                name_score += 3
            if word in eid_base:
                name_score += 2

        # Penalty for numeric suffixes (e.g., "office_tv_3" when searching "office tv")
        if search.replace(" ", "_") in eid_base and eid_base != search.replace(" ", "_"):
            if any(c.isdigit() for c in eid_base.split("_")[-1:]):
                name_score -= 5

        # Capability bonuses break ties between equal name scores only.
        bonus = 0
        attrs = state.get("attributes", {})
        device_class = attrs.get("device_class", "")
        if device_class == "tv":
            bonus += 200
        elif device_class == "speaker":
            bonus += 50

        # Context-aware bonuses based on media_type
        if media_type == "music":
            # Prefer Music Assistant Queue entities for music playback
            if attrs.get("active_queue"):
                bonus += 500
            if attrs.get("mass_player_type"):
                bonus += 200
        elif media_type == "video":
            # Prefer Cast-capable or Android TV entities for video
            if attrs.get("app_name") == "Default Media Receiver":
                bonus += 500
            if attrs.get("app_name") == "com.google.android.apps.mediashell":
                bonus += 400
            if device_class == "tv":
                bonus += 300

        if name_score > 0 or bonus > 0:
            candidates.append((name_score, bonus, entity_id))

    if not candidates:
        return None

    # Name match dominates; capability bonuses only break ties.
    candidates.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return candidates[0][2]


async def get_services(ha_url: str, ha_token: str) -> list[dict]:
    """Fetch available Home Assistant service domains and services from /api/services."""
    if not ha_url or not ha_token:
        return []
    headers = {"Authorization": f"Bearer {ha_token}", "Content-Type": "application/json"}
    url = f"{ha_url.rstrip('/')}/api/services"
    async with _ha_session(ha_url) as client:
        try:
            async with client.get(url, headers=headers, timeout=_TIMEOUT) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data if isinstance(data, list) else []
        except Exception as e:
            log.debug(f"[ha_client] get_services failed: {e}")
    return []

