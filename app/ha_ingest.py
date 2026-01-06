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

# Import shared fetch logic
from app.utils.ha_fetch import fetch_ha_data, get_device_info, HA_URL, HA_TOKEN

# --- Configuration ---
# We use os.getenv directly to ensure we use the Docker container's environment
# HA_URL and HA_TOKEN are imported from ha_fetch
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
    
    # Create a lookup dict for easy entity access
    states_dict = {s['entity_id']: s for s in states}
    
    # Cutoff for 'Active' Devices (30 Days)
    cutoff_time = datetime.now(timezone.utc) - timedelta(days=30)
    # Cutoff for 'Active' Devices (30 Days)
    cutoff_time = datetime.now(timezone.utc) - timedelta(days=30)
    logger.info(f"Checking for entities older than: {cutoff_time.isoformat()}")

    # Domains to index (controllable or useful info)
    # Strictly physical/actionable domains. Removed 'automation', 'timer', 'person'.
    ALLOWED_DOMAINS = [
        "light", "switch", "media_player", "climate", 
        "fan", "cover", "lock", "script", "scene"
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

        # Filter 2: Strict "No Synthetic/Logical Entities" Rule
        # We strictly skip legacy groups, zones, and system entities.
        # "group." domains are filtered unless they are valid media_player groups (which appear as media_player.X).
        if entity_id.startswith(("group.", "zone.", "sun.", "update.", "sensor.", "binary_sensor.", "person.", "timer.", "automation.")):
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
        
        # Mapping: Domain -> Friendly Integration Name
        DOMAIN_MAP = {
            "androidtv_remote": "android_tv",
            "google_cast": "chromecast",
            "cast": "chromecast",
            "roku": "roku",
            "smartthings": "smartthings",
            "webostv": "webos_tv",
            "braviatv": "bravia_tv"
        }
        
        if integration.lower() in DOMAIN_MAP:
            integration = DOMAIN_MAP[integration.lower()]
        
        # Use attributes as secondary refinement if still unknown
        if integration == "unknown" or integration == "chromecast":
             if "com.google.android" in str(attributes.get("app_id", "")):
                  integration = "android_tv"
             elif "cast" in str(attributes.get("app_id", "")) or "cast" in platform.lower():
                  integration = "chromecast"
        
        # If it's Music Assistant, try to find the linked hardware integration in the description
        if is_mass and (integration == "music_assistant" or integration == "unknown"):
             # Look at active_queue or mass_player_id
             mass_target = attributes.get("active_queue") or attributes.get("mass_player_id")
             if mass_target and mass_target in states_dict:
                 target_info = get_device_info(mass_target, device_registry, entity_registry, area_registry)
                 if target_info[1] and target_info[1] != "unknown":
                      integration = target_info[1]
                      if integration.lower() in DOMAIN_MAP:
                           integration = DOMAIN_MAP[integration.lower()]
        
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
