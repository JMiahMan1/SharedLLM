# services/execution/ha_client.py
"""
Asynchronous Home Assistant REST client using httpx.
"""
import logging
import httpx

log = logging.getLogger("execution.ha_client")

_TIMEOUT = 10

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
            log.info(f"[ha_client] POST {url}")
            resp = await client.post(url, headers=headers, json=payload)
            resp.raise_for_status()
            log.info(f"[ha_client] {domain}.{service} → {entity_id} OK (HTTP {resp.status_code})")
            return {"ok": True, "status_code": resp.status_code}
        except httpx.HTTPStatusError as e:
            log.error(f"[ha_client] HTTP error: {e}")
            return {"ok": False, "error": str(e), "status_code": e.response.status_code}
        except httpx.RequestError as e:
            log.error(f"[ha_client] Request error: {e}")
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
    
    async with httpx.AsyncClient(timeout=30) as client:
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
