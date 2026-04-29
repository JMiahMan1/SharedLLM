# services/execution/ha_client.py
"""
Lightweight, stateless Home Assistant REST client.
All methods are synchronous — callers wrap in run_in_executor if needed.
"""
import logging
import requests

log = logging.getLogger("execution.ha_client")

_TIMEOUT = 10


def call_service(
    ha_url: str,
    ha_token: str,
    domain: str,
    service: str,
    entity_id: str,
    service_data: dict | None = None,
) -> dict:
    """Call a Home Assistant service and return the response JSON."""
    headers = {"Authorization": f"Bearer {ha_token}", "Content-Type": "application/json"}
    url = f"{ha_url.rstrip('/')}/api/services/{domain}/{service}"
    payload = {"entity_id": entity_id}
    if service_data:
        payload.update(service_data)
    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=_TIMEOUT)
        resp.raise_for_status()
        log.info(f"[ha_client] {domain}.{service} → {entity_id} OK (HTTP {resp.status_code})")
        return {"ok": True, "status_code": resp.status_code}
    except requests.HTTPError as e:
        log.error(f"[ha_client] HTTP error: {e}")
        return {"ok": False, "error": str(e), "status_code": getattr(e.response, "status_code", None)}
    except requests.RequestException as e:
        log.error(f"[ha_client] Request error: {e}")
        return {"ok": False, "error": str(e)}


def get_state(ha_url: str, ha_token: str, entity_id: str) -> dict | None:
    """Retrieve the current state of an HA entity."""
    headers = {"Authorization": f"Bearer {ha_token}"}
    url = f"{ha_url.rstrip('/')}/api/states/{entity_id}"
    try:
        resp = requests.get(url, headers=headers, timeout=_TIMEOUT)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        log.error(f"[ha_client] get_state({entity_id}) failed: {e}")
        return None
