# ha_ingest.py
import os
import requests
import json
import logging
import sys
import asyncio
from typing import Dict, Any, List, Tuple

# LangChain and Chroma Imports
try:
    from langchain_core.documents import Document
    from langchain_huggingface import HuggingFaceEmbeddings
    from langchain_chroma import Chroma
except ImportError as e:
    print(f"CRITICAL: Missing AI dependencies: {e}")
    sys.exit(1)

# --- Configuration (using settings module conventions) ---
try:
    # Assuming these are available via settings.py in the running environment
    from settings import (
        HA_URL, HA_ENV_TOKEN, CHROMA_DIR, EMB_MODEL, get_user_creds, run_blocking
    )
except ImportError:
    # Fallback/Placeholder definitions for non-integrated testing
    HA_URL = os.getenv("HA_URL")
    HA_ENV_TOKEN = os.getenv("HA_TOKEN")
    CHROMA_DIR = os.getenv("CHROMA_PERSIST_DIR", "/data/chroma_db")
    EMB_MODEL = os.getenv("EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2")
    
    def get_user_creds(user=None, token=None):
        return {"user": user or "Admin", "ha_token": token or HA_ENV_TOKEN}
    
    # Simple synchronous run_blocking for non-integrated execution
    def run_blocking(fn, *args, **kwargs):
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            return executor.submit(fn, *args, **kwargs).result()

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
    creds = get_user_creds()
    token = creds.get("ha_token")

    if not HA_URL or not token:
        logger.error("HA_URL or HA_TOKEN not configured.")
        return {}, {}, {}
    
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    
    def fetch_endpoint(endpoint):
        url = f"{HA_URL.rstrip('/')}/api/{endpoint}"
        resp = requests.get(url, headers=headers, timeout=10)
        if resp.status_code == 200:
            return resp.json()
        logger.warning(f"Failed to fetch {endpoint}: HTTP {resp.status_code}")
        return None

    states = fetch_endpoint("states") or []
    device_registry_list = fetch_endpoint("config/device_registry/list") or []
    entity_registry_list = fetch_endpoint("config/entity_registry/list") or []

    device_registry = {dev["id"]: dev for dev in device_registry_list if "id" in dev}
    entity_registry = {ent["entity_id"]: ent for ent in entity_registry_list if "entity_id" in ent}

    return states, device_registry, entity_registry

def get_device_info(entity_id: str, device_registry: Dict[str, Any], entity_registry: Dict[str, Any]) -> Tuple[str, str]:
    """Retrieves device name and integration from registry data."""
    
    registry_entry = entity_registry.get(entity_id, {})
    device_id = registry_entry.get("device_id")
    platform = registry_entry.get("platform", "unknown")

    integration = platform
    device_name = ""
    
    if device_id and device_id in device_registry:
        device = device_registry[device_id]
        # Use a combination of identifiers for robust integration name
        integration = device.get("manufacturer", "") + " " + device.get("model", "")
        device_name = device.get("name_by_user") or device.get("name") or ""
        
        # Override integration if the platform name is more specific (like music_assistant)
        if platform and "integration" not in integration.lower():
             integration = platform

    # If integration is still a simple platform (e.g., 'template'), keep it.
    if not integration.strip():
        integration = platform
        
    return device_name, integration.strip()

# ----------------------
# Ingestion Main
# ----------------------

def ingest_ha_metadata():
    logger.info("--- Starting Home Assistant Metadata Ingestion ---")

    # 1. Initialize DB and Embeddings
    embeddings = HuggingFaceEmbeddings(model_name=EMB_MODEL)
    
    # TARGET COLLECTION: ha_sensors (Explicitly separate)
    vectordb = Chroma(
        collection_name="ha_sensors", 
        embedding_function=embeddings, 
        persist_directory=CHROMA_DIR
    )
    
    # 2. Fetch HA Data
    states, device_registry, entity_registry = fetch_ha_data()
    
    if not states:
        logger.error("Skipping ingestion due to missing HA state data.")
        return

    # 3. Process and Chunk Documents
    docs_to_add = []
    
    # Clear existing data
    try:
        vectordb._collection.delete(where={})
        logger.info(f"Cleared existing data from ha_sensors collection.")
    except Exception as e:
        logger.warning(f"Failed to clear collection: {e}")

    # Counter for entities processed
    skipped_count = 0
    ingested_count = 0

    for state_obj in states:
        entity_id = state_obj["entity_id"]
        attributes = state_obj.get("attributes", {})
        current_state = state_obj.get("state", "unknown")
        
        # Filter 1: Skip based on unwanted entity types (like groups/zones)
        if entity_id.startswith(("group.", "zone.", "person.", "sun.")):
            skipped_count += 1
            continue
        
        # Filter 2: Skip inactive/unhelpful states
        if current_state in ["unavailable", "unknown", "none", "uninitialized"]:
            skipped_count += 1
            continue
            
        # Get enriched metadata
        device_name, integration = get_device_info(entity_id, device_registry, entity_registry)
        
        # --- MUSIC ASSISTANT SELF-CORRECTION (CRITICAL FIX) ---
        # If the integration is unknown but the entity has MA attributes, force the integration name.
        if "music_assistant" not in integration.lower() and (
            "mass_player_type" in attributes or "active_queue" in attributes
        ):
            logger.info(f"Self-Correcting: {entity_id} detected as Music Assistant player via attributes.")
            integration = "music_assistant"

        # Build document content for similarity search
        friendly_name = attributes.get("friendly_name", device_name or entity_id.split('.')[1])
        
        # Content = all terms a user might use to reference the entity.
        content = f"{friendly_name} ({entity_id}) is a {integration} device."
        if device_name and device_name not in friendly_name:
             content += f" Associated with device: {device_name}."

        # Filter out MA-specific attributes from the general searchable list to keep content clean
        searchable_attrs = [
            f"{k}: {v}" for k, v in attributes.items() 
            if k not in ["friendly_name", "icon", "supported_features", "unit_of_measurement", "mass_player_type", "active_queue"] and v is not None and len(str(v)) < 50
        ]
        if searchable_attrs:
            content += " Key attributes: " + ", ".join(searchable_attrs)

        # Build Metadata payload
        metadata = {
            "entity_id": entity_id,
            "domain": entity_id.split('.')[0],
            "friendly_name": friendly_name,
            "integration": integration, # CRITICAL for Music Assistant logic
            "device_name": device_name,
            "state": current_state
        }

        docs_to_add.append(Document(page_content=content, metadata=metadata))
        ingested_count += 1

    # 4. Add Documents to Chroma
    if docs_to_add:
        try:
            vectordb.add_documents(docs_to_add)
            logger.info(f"Successfully ingested {ingested_count} ACTIVE Home Assistant entities.")
            logger.info(f"Skipped {skipped_count} inactive entities.")
            
            # 5. Persist
            if hasattr(vectordb, 'persist'):
                vectordb.persist()
                logger.info("Chroma database persisted.")
        except Exception as e:
            logger.critical(f"CRITICAL: Failed to add documents to Chroma: {e}")

if __name__ == "__main__":
    ingest_ha_metadata()
