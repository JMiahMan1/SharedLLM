# app/ha_ingest.py
import os
import requests
import json
import logging
import sys
import shutil
from typing import Dict, Any, Tuple

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

def fetch_ha_data() -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
    """Fetches all states, device registry, and entity registry info from Home Assistant."""
    if not HA_TOKEN:
        logger.error("HA_TOKEN not configured.")
        return [], {}, {}
    
    headers = {"Authorization": f"Bearer {HA_TOKEN}", "Content-Type": "application/json"}
    
    def fetch_endpoint(endpoint):
        url = f"{HA_URL.rstrip('/')}/api/{endpoint}"
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
    device_registry_list = fetch_endpoint("config/device_registry/list") or []
    entity_registry_list = fetch_endpoint("config/entity_registry/list") or []
    area_registry_list = fetch_endpoint("config/area_registry/list") or []

    # Index registries for fast lookup
    device_registry = {dev["id"]: dev for dev in device_registry_list if "id" in dev}
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
            
        integration = (device.get("manufacturer", "") + " " + device.get("model", "")).strip()
        device_name = device.get("name_by_user") or device.get("name") or ""
        
        if platform and "integration" not in integration.lower():
            integration = platform

    if not integration.strip():
        integration = platform
        
    area_name = area_registry.get(area_id, "") if area_id else ""
        
    return device_name, integration.strip(), area_name

# ----------------------
# Ingestion Main
# ----------------------

def ingest_ha_metadata():
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
    states, device_registry, entity_registry, area_registry = fetch_ha_data()
    if not states:
        logger.error("No states received from HA. Aborting.")
        return

    # 4. Process Entities
    docs_to_add = []
    skipped_count = 0
    
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
        
        # Filter 1: Skip unwanted types
        if entity_id.startswith(("group.", "zone.", "sun.")):
            skipped_count += 1
            continue

        domain = entity_id.split('.')[0]
        
        # Filter 2: Domain & State
        if domain not in ALLOWED_DOMAINS:
            continue
        if current_state in ["unavailable", "unknown", "none"]:
            skipped_count += 1
            continue
            
        # Get enriched metadata
        device_name, integration, area_name = get_device_info(entity_id, device_registry, entity_registry, area_registry)
        
        # --- MUSIC ASSISTANT SELF-CORRECTION ---
        # If integration is unknown but it has MA attributes, force it.
        if "music_assistant" not in integration.lower() and (
            "mass_player_type" in attributes or "active_queue" in attributes
        ):
            integration = "music_assistant"

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
    ingest_ha_metadata()
