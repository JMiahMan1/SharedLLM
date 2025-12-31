# app/ha_ingest.py
import os
import requests
import json
import logging
import sys
import shutil
import argparse
from typing import Dict, Any, Tuple, List
from datetime import datetime, timedelta, timezone

# LangChain and Chroma Imports
try:
    from langchain_core.documents import Document
    from langchain_huggingface import HuggingFaceEmbeddings
    from langchain_chroma import Chroma
except ImportError as e:
    print(f"CRITICAL: Missing AI dependencies: {e}")
    sys.exit(1)

# --- Configuration ---
# We use os.getenv directly to ensure we use the Docker container's environment
HA_URL = os.getenv("HA_URL", "http://172.24.0.1:8123")
HA_TOKEN = os.getenv("HA_TOKEN")
CHROMA_DIR = os.getenv("CHROMA_PERSIST_DIR", "/data/chroma_db")
EMB_MODEL = os.getenv("EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2")

# CRITICAL: This must match GlobalResources.ha_collection in settings.py
COLLECTION_NAME = "home_assistant" 

# API URL for reloading resources after ingestion
API_RELOAD_URL = "http://localhost:11435/api/system/reload"

# --- Logging Setup ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
    force=True
)
logger = logging.getLogger("HA_Ingest")

# ----------------------
# Core HA Data Fetching
# ----------------------

def fetch_ha_data(ha_url: str = None, ha_token: str = None) -> Tuple[List[Dict[str, Any]], Dict[str, Any], Dict[str, Any], Dict[str, str]]:
    """Fetches all states, device registry, entity registry, and area registry info from Home Assistant.
    
    Returns:
        Tuple of (states, device_registry, entity_registry, area_registry)
    """
    # Use args if provided, else fall back to globals
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

    logger.info(f"Connecting to Home Assistant at {HA_URL}...")
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
        # Note: We construct a flattened view and extract areas from it to simplify the template logic.
        
        try:
            tmpl_url = f"{_url.rstrip('/')}/api/template"
            resp = requests.post(tmpl_url, headers=headers, json={"template": template_str}, timeout=20)
            if resp.status_code == 200:
                data = resp.json()
                device_registry_list = data.get("devices", [])
                fallback_entities = data.get("entities", [])
                
                # Populate Entity Registry from fallback if still empty
                if not entity_registry_list:
                     entity_registry_list = [
                         {"entity_id": e["entity_id"], "device_id": e["device_id"], "area_id": e["area_id"], "platform": "unknown"}
                         for e in fallback_entities
                     ]

                # Reconstruct Area Registry from the flat device list
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

    # Index registries for fast lookup
    device_registry = {dev["id"]: dev for dev in device_registry_list if "id" in dev}
    # Entity registry might still be empty if we couldn't fetch it, but that's less critical than devices/manufacturers
    entity_registry = {ent["entity_id"]: ent for ent in entity_registry_list if "entity_id" in ent}
    area_registry = {area["area_id"]: area["name"] for area in area_registry_list if "area_id" in area}

    return states, device_registry, entity_registry, area_registry

def get_device_info(entity_id: str, device_registry: Dict[str, Any], entity_registry: Dict[str, Any], area_registry: Dict[str, str]) -> Tuple[str, str, str]:
    """Retrieves device name, integration, and area name from registry data."""
    registry_entry = entity_registry.get(entity_id, {})
    device_id = registry_entry.get("device_id")
    platform = registry_entry.get("platform", "unknown")
    
    # Try to find area in entity registry first, then device registry
    area_id = registry_entry.get("area_id")
    
    integration = platform
    device_name = ""
    
    if device_id and device_id in device_registry:
        device = device_registry[device_id]
        if not area_id:
            area_id = device.get("area_id")
            
        # Identifier-based Tell: The first element of the first identifier usually matches the integration domain
        idents = device.get("identifiers", [])
        ident_tell = ""
        if idents and isinstance(idents[0], (list, tuple)) and len(idents[0]) > 0:
             ident_tell = idents[0][0]

        manufacturer = device.get("manufacturer", "")
        model = device.get("model", "")
        
        # Priority: 1. Identifiers tell, 2. platform from registry, 3. manufacturer/model combo
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

# ----------------------
# Ingestion Main
# ----------------------

def ingest_ha_metadata(ha_url: str = None, ha_token: str = None):
    logger.info(f"--- Starting Home Assistant Ingestion to '{COLLECTION_NAME}' ---")

    # 1. Initialize Embeddings
    embeddings = HuggingFaceEmbeddings(model_name=EMB_MODEL)
    
    # 2. Force Clean State (Clear old data to prevent stale entities)
    try:
        temp_db = Chroma(
            collection_name=COLLECTION_NAME,
            embedding_function=embeddings,
            persist_directory=CHROMA_DIR
        )
        if temp_db._collection.count() > 0:
            logger.info("Clearing existing Home Assistant data...")
            temp_db.delete_collection()
    except Exception:
        pass

    # 3. Fetch HA Data
    states, device_registry, entity_registry, area_registry = fetch_ha_data(ha_url, ha_token)
    if not states:
        logger.error("No states received from HA. Aborting.")
        return

    # Filter Logic Setup
    docs_to_add = []
    skipped_count = 0
    stale_count = 0
    
    # Cutoff for 'Active' Devices (30 Days)
    cutoff_time = datetime.now(timezone.utc) - timedelta(days=30)
    logger.info(f"Checking for entites older than: {cutoff_time.isoformat()}")

    # Domains to index (controllable or useful info)
    ALLOWED_DOMAINS = [
        "light", "switch", "media_player", "climate", 
        "fan", "cover", "lock", "script", "automation", 
        "timer", "person", "scene"
    ]

    for state_obj in states:
        entity_id = state_obj["entity_id"]
        attributes = state_obj.get("attributes", {})
        current_state = state_obj.get("state", "unknown")
        last_updated_str = state_obj.get("last_updated")
        
        # Filter 1: Activity Check (30 Days) - RELAXED: Log only
        if last_updated_str:
            try:
                # Handle Z or +00:00. Python 3.11 fromisoformat generally handles it.
                last_updated = datetime.fromisoformat(last_updated_str.replace("Z", "+00:00"))
                if last_updated < cutoff_time:
                    stale_count += 1
                    continue
            except Exception:
                pass

        # Filter 2: Skip unwanted types
        if entity_id.startswith(("group.", "zone.", "sun.")):
            skipped_count += 1
            continue

        domain = entity_id.split('.')[0]
        
        # Filter 3: Domain & State
        if domain not in ALLOWED_DOMAINS:
            # Check for specific mass entities if domain differs, but usually they are media_player
            continue
            
        # RELAXED: Don't skip unavailable/unknown. We want static catalog.
        # if current_state in ["unavailable", "unknown", "none"]:
        #     skipped_count += 1
        #     continue
            
        # Get enriched metadata
        device_name, integration, area_name = get_device_info(entity_id, device_registry, entity_registry, area_registry)
        
        # Get platform and device_id for self-correction logic
        registry_entry = entity_registry.get(entity_id, {})
        platform = registry_entry.get("platform", "unknown")
        device_id = registry_entry.get("device_id")
        
        # --- INTEGRATION SPECIFIC ENHANCEMENTS ---
        # Prioritize hardware integration over Music Assistant for general features
        # but keep MA as a secondary 'capability' in the description.
        is_mass = "music_assistant" in integration.lower() or "mass_" in str(attributes) or "active_queue" in attributes
        
        # Refine names
        if integration in ["unknown", "androidtv_remote", "cast"]:
             if "com.google.android" in str(attributes.get("app_id", "")) or "cast" in str(attributes.get("app_id", "")):
                  integration = "chromecast"
             elif "androidtv" in integration:
                  integration = "android_tv"
        
        # If it's a device we know is Roku/AndroidTV but it's currently shows as music_assistant, 
        # we might want to mention the hardware integration is primary.
        # But for now, let's just make sure the 'integration' field reflects the hardware if known.
        
        # Build Friendly Name


        # Build Friendly Name
        friendly_name = attributes.get("friendly_name", device_name or entity_id.split('.')[1])
        
        # Build Content (The text the LLM actually searches against)
        # We explicitly mention "is a X device" to help vector matching.
        content = f"{friendly_name} ({entity_id}) is a {integration} device."
        if area_name:
             content += f" Located in {area_name}."
        if device_name and device_name not in friendly_name:
             content += f" Part of device: {device_name}."
        
        # Add key attributes to content for searchability
        # e.g., if user asks "Which light is red?", we need attributes.
        searchable_attrs = []
        for k, v in attributes.items():
            # Skip noise attributes (but keep supported_features and supported_color_modes for metadata)
            if k not in ["friendly_name", "icon", "mass_player_type", "entity_picture", "supported_features", "supported_color_modes"] and v and len(str(v)) < 50:
                searchable_attrs.append(f"{k}: {v}")
        
        if searchable_attrs:
            content += " Attributes: " + ", ".join(searchable_attrs)

        # Build Metadata payload (Used by the API for tool calls)
        metadata = {
            "entity_id": entity_id,
            "domain": domain,
            "friendly_name": friendly_name,
            "integration": integration, 
            "device_name": device_name,
            "area_name": area_name or "",
            "state": current_state,
            "source": "home_assistant"
        }
        
        # Include supported_features and supported_color_modes for capability detection
        if "supported_features" in attributes:
            metadata["supported_features"] = str(attributes["supported_features"])
        if "supported_color_modes" in attributes:
            # Store as JSON string for compatibility
            import json
            metadata["supported_color_modes"] = json.dumps(attributes["supported_color_modes"])

        docs_to_add.append(Document(page_content=content, metadata=metadata))

    # 5. Ingest to Chroma
    if docs_to_add:
        try:
            vectordb = Chroma.from_documents(
                documents=docs_to_add,
                embedding=embeddings,
                collection_name=COLLECTION_NAME, 
                persist_directory=CHROMA_DIR
            )
            # Force persist to disk
            if hasattr(vectordb, 'persist'):
                vectordb.persist()
            
            logger.info(f"✅ Successfully ingested {len(docs_to_add)} ACTIVE entities into '{COLLECTION_NAME}'.")
            logger.info(f"Skipped {skipped_count} inactive/filtered entities.")
            logger.info(f"Skipped {stale_count} STALE entities (Older than 30 days).")
            logger.info(f"Post-ingest DB Count: {vectordb._collection.count()}")
            
            # 6. Trigger API Reload
            # This ensures the running API picks up the new database state immediately
            try:
                logger.info("Triggering API resource reload...")
                requests.post(API_RELOAD_URL, timeout=2)
            except Exception:
                logger.warning("Could not trigger API reload (API might be down or busy). Data is saved to disk though.")
                
        except Exception as e:
            logger.critical(f"CRITICAL: Failed to add documents to Chroma: {e}")
    else:
        logger.warning("No valid entities found to ingest.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Ingest Home Assistant metadata into ChromaDB.")
    parser.add_argument("--url", type=str, help="Home Assistant URL (overrides HA_URL env var)")
    parser.add_argument("--token", type=str, help="Home Assistant Long-Lived Access Token (overrides HA_TOKEN env var)")
    
    args = parser.parse_args()
    
    ingest_ha_metadata(ha_url=args.url, ha_token=args.token)
