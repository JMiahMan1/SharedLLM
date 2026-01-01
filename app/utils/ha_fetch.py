
import os
import requests
import logging
import sys
from typing import Dict, Any, Tuple, List
from datetime import datetime, timedelta, timezone

# Constants
HA_URL = os.getenv("HA_URL", "http://172.24.0.1:8123")
HA_TOKEN = os.getenv("HA_TOKEN")

logger = logging.getLogger("HA_Fetch")

def fetch_ha_data(ha_url: str = None, ha_token: str = None) -> Tuple[List[Dict[str, Any]], Dict[str, Any], Dict[str, Any], Dict[str, str]]:
    """Fetches all states, device registry, entity registry, and area registry info from Home Assistant.
    
    Returns:
        Tuple of (states, device_registry, entity_registry, area_registry)
    """
    _url = ha_url or HA_URL
    _token = ha_token or HA_TOKEN
    
    if not _token:
        logger.error("HA_TOKEN not configured.")
        return [], {}, {}, {}
    
    headers = {"Authorization": f"Bearer {_token}", "Content-Type": "application/json"}
    
    def fetch_endpoint(endpoint):
        url = f"{_url.rstrip('/')}/api/{endpoint}"
        try:
            resp = requests.get(url, headers=headers, timeout=10)
            if resp.status_code == 200:
                return resp.json()
            logger.warning(f"Failed to fetch {endpoint}: HTTP {resp.status_code}")
        except Exception as e:
            logger.warning(f"Connection error fetching {endpoint}: {e}")
        return None

    logger.info(f"Connecting to Home Assistant at {_url}...")
    states = fetch_endpoint("states") or []

    # Try standard endpoints first
    device_registry_list = fetch_endpoint("config/device_registry/list") or []
    entity_registry_list = fetch_endpoint("config/entity_registry/list") or []
    area_registry_list = fetch_endpoint("config/area_registry/list") or []

    # Fallback: Use Template API if registries are empty (Common with non-admin tokens)
    if not device_registry_list:
        logger.info("Registry endpoints failed (404/403). Attempting fallback via Template API...")
        
        template_str = """
        {
          "devices": [
            {% set dev_ids = states | map(attribute='entity_id') | map('device_id') | unique | select('string') | list %}
            {% for did in dev_ids %}
            {
              "id": {{ did | to_json }},
              "manufacturer": {{ (device_attr(did, 'manufacturer') or 'unknown') | to_json }},
              "model": {{ (device_attr(did, 'model') or 'unknown') | to_json }},
              "name": {{ (device_attr(did, 'name') or 'unknown') | to_json }},
              "area_id": {{ (area_id(did) or '') | to_json }},
              "area_name": {{ (area_name(did) or '') | to_json }},
              "identifiers": {{ device_attr(did, 'identifiers') | list | to_json }}
            }{{ "," if not loop.last else "" }}
            {% endfor %}
          ],
          "entities": [
            {% for entity_id in states | map(attribute='entity_id') | list %}
            {
              "entity_id": {{ entity_id | to_json }},
              "device_id": {{ device_id(entity_id) | to_json }},
              "area_id": {{ area_id(entity_id) | to_json }}
            }{{ "," if not loop.last else "" }}
            {% endfor %}
          ]
        }
        """
        
        try:
            tmpl_url = f"{_url.rstrip('/')}/api/template"
            resp = requests.post(tmpl_url, headers=headers, json={"template": template_str}, timeout=20)
            if resp.status_code == 200:
                data = resp.json()
                device_registry_list = data.get("devices", [])
                fallback_entities = data.get("entities", [])
                
                if not entity_registry_list:
                     entity_registry_list = [
                         {"entity_id": e["entity_id"], "device_id": e["device_id"], "area_id": e["area_id"], "platform": "unknown"}
                         for e in fallback_entities
                     ]

                area_registry_list = []
                _seen_areas = set()
                for d in device_registry_list:
                    aid = d.get("area_id")
                    aname = d.get("area_name")
                    if aid and aname and aid not in _seen_areas:
                        area_registry_list.append({"area_id": aid, "name": aname})
                        _seen_areas.add(aid)
                
                logger.info(f"Fallback successful: Retrieved {len(device_registry_list)} devices and {len(area_registry_list)} areas via template.")
            else:
                logger.warning(f"Template API fallback failed: {resp.status_code} - {resp.text}")
        except Exception as e:
            logger.error(f"Template API error: {e}")
            if 'resp' in locals():
                 logger.error(f"Response text start: {resp.text[:200]}")

    # Index registries
    device_registry = {dev["id"]: dev for dev in device_registry_list if "id" in dev}
    entity_registry = {ent["entity_id"]: ent for ent in entity_registry_list if "entity_id" in ent}
    area_registry = {area["area_id"]: area["name"] for area in area_registry_list if "area_id" in area}

    return states, device_registry, entity_registry, area_registry

def get_device_info(entity_id: str, device_registry: Dict[str, Any], entity_registry: Dict[str, Any], area_registry: Dict[str, str]) -> Tuple[str, str, str]:
    """Retrieves device name, integration, and area name from registry data."""
    registry_entry = entity_registry.get(entity_id, {})
    device_id = registry_entry.get("device_id")
    platform = registry_entry.get("platform", "unknown")
    area_id = registry_entry.get("area_id")
    
    integration = platform
    device_name = ""
    
    if device_id and device_id in device_registry:
        device = device_registry[device_id]
        if not area_id:
            area_id = device.get("area_id")
            
        idents = device.get("identifiers", [])
        ident_tell = ""
        if idents and isinstance(idents[0], (list, tuple)) and len(idents[0]) > 0:
             ident_tell = idents[0][0]

        manufacturer = device.get("manufacturer", "")
        model = device.get("model", "")
        
        if ident_tell and ident_tell != "unknown":
             integration = ident_tell
        elif platform and platform != "unknown":
             integration = platform
        else:
             integration = (manufacturer + " " + model).strip()
             
        device_name = device.get("name_by_user") or device.get("name") or ""

    if not integration.strip() or integration == "unknown":
        integration = platform
        
    area_name = area_registry.get(area_id, "") if area_id else ""
        
    return device_name, integration.strip(), area_name
