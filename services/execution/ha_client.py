# services/execution/ha_client.py
"""
Asynchronous Home Assistant REST client using httpx.
"""
import logging
import httpx

log = logging.getLogger("execution.ha_client")

import re
_TIMEOUT = httpx.Timeout(45.0, connect=5.0)

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
    entity_id: str,
    service_data: dict | None = None,
) -> dict:
    """Call a Home Assistant service and return the response JSON."""
    if not ha_url:
        log.error("[ha_client] ha_url is None or empty. Cannot call service.")
        return {"ok": False, "error": "Home Assistant URL not configured for this user."}
    
    headers = {"Authorization": f"Bearer {ha_token}", "Content-Type": "application/json"}
    url = f"{ha_url.rstrip('/')}/api/services/{domain}/{service}"
    payload = {"entity_id": entity_id}
    if service_data:
        payload.update(service_data)
        
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        try:
            log.info(f"HA CALL: {domain}.{service} -> {entity_id} | url={url} | payload={payload}")
            resp = await client.post(url, headers=headers, json=payload)
            resp.raise_for_status()
            log.info(f"[ha_client] {domain}.{service} → {entity_id} OK (HTTP {resp.status_code})")
            return {"ok": True, "status_code": resp.status_code}
        except httpx.HTTPStatusError as e:
            log.error(f"[ha_client] HTTP error: {e}")
            detail = ""
            try:
                detail = e.response.json().get("detail", e.response.text)
            except:
                detail = e.response.text
            if e.response.status_code in (401, 403):
                return {"ok": False, "error": "Your Home Assistant token is invalid or expired. Please update it in Jarvis.", "status_code": e.response.status_code}
            return {"ok": False, "error": f"HA returned {e.response.status_code}: {detail}", "status_code": e.response.status_code}
        except httpx.RequestError as e:
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
    
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        try:
            log.debug(f"[ha_client] GET {url}")
            resp = await client.get(url, headers=headers)
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            log.error(f"[ha_client] get_state({entity_id}) failed: {e}")
            return None

async def get_states(ha_url: str, ha_token: str) -> list:
    """Retrieve all states from HA."""
    if not ha_url:
        log.error("[ha_client] ha_url is None or empty. Cannot get states.")
        return []
        
    headers = {"Authorization": f"Bearer {ha_token}"}
    url = f"{ha_url.rstrip('/')}/api/states"
    
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        try:
            log.info(f"[ha_client] GET {url}")
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            log.error(f"[ha_client] get_states failed: {e}")
async def get_history(ha_url: str, ha_token: str, entity_id: str, days: int = 1) -> list:
    """Retrieve history for a specific entity from HA."""
    if not ha_url:
        log.error("[ha_client] ha_url is None or empty. Cannot get history.")
        return []
        
    import datetime
    start_time = (datetime.datetime.now() - datetime.timedelta(days=days)).isoformat()
    headers = {"Authorization": f"Bearer {ha_token}"}
    url = f"{ha_url.rstrip('/')}/api/history/period/{start_time}"
    params = {"filter_entity_id": entity_id, "no_attributes": ""}
    
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        try:
            log.info(f"[ha_client] GET {url}")
            resp = await client.get(url, headers=headers, params=params)
            resp.raise_for_status()
            data = resp.json()
            # HA returns a list of lists (one per entity)
            return data[0] if data else []
        except Exception as e:
            log.error(f"[ha_client] get_history({entity_id}) failed: {e}")
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
    
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        try:
            resp = await client.post(url, headers=headers, json={"template": template})
            resp.raise_for_status()
            raw_data = resp.json()
            # Convert list of dicts to a single mapping dict
            if isinstance(raw_data, list):
                return {item["eid"]: item["a"] for item in raw_data if item.get("a")}
            return {}
        except Exception as e:
            log.error(f"[ha_client] get_areas failed: {e}")
            return {}
