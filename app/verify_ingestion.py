
import os
import sys
import logging
import argparse
from typing import Set

# Setup logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("IngestVerify")

# Import from existing app modules
# We need to ensure /app is in path if running from root
sys.path.append(os.getcwd())

try:
    from app.ha_ingest import fetch_ha_data, CHROMA_DIR, COLLECTION_NAME, EMB_MODEL
    from langchain_chroma import Chroma
    from langchain_huggingface import HuggingFaceEmbeddings
except ImportError as e:
    logger.error(f"Import Error: {e}. Run this from the project root or inside the container.")
    sys.exit(1)

def verify_ingestion_completeness(ha_url=None, ha_token=None):
    logger.info("--- 🔎 Verifying Ingestion Completeness ---")
    
    # 1. Fetch Source Truth (Home Assistant Live Data)
    logger.info("Fetching live data from Home Assistant...")
    states, _, _, _ = fetch_ha_data(ha_url, ha_token)
    
    if not states:
        logger.error("Failed to fetch states from HA.")
        return

    # Filter HA IDs to only the domains we care about
    ALLOWED_DOMAINS = [
        "light", "switch", "media_player", "climate", 
        "fan", "cover", "lock", "script", "automation", 
        "timer", "person", "scene"
    ]
    
    ha_entities: Set[str] = set()
    for s in states:
        eid = s['entity_id']
        domain = eid.split('.')[0]
        if domain in ALLOWED_DOMAINS:
            ha_entities.add(eid)
            
    logger.info(f"Home Assistant (Live): Found {len(ha_entities)} relevant entities.")

    # 2. Fetch Stored Truth (ChromaDB)
    logger.info(f"Fetching stored data from ChromaDB ({CHROMA_DIR})...")
    embeddings = HuggingFaceEmbeddings(model_name=EMB_MODEL)
    try:
        db = Chroma(
            collection_name=COLLECTION_NAME, 
            embedding_function=embeddings, 
            persist_directory=CHROMA_DIR
        )
        stored_data = db.get()
        stored_entities: Set[str] = set()
        
        if stored_data and 'metadatas' in stored_data:
            for meta in stored_data['metadatas']:
                if meta and 'entity_id' in meta:
                    stored_entities.add(meta['entity_id'])
                    
        logger.info(f"ChromaDB (Stored): Found {len(stored_entities)} entities.")
        
    except Exception as e:
        logger.error(f"Failed to read ChromaDB: {e}")
        return

    # 3. Compare
    missing_in_chroma = ha_entities - stored_entities
    extra_in_chroma = stored_entities - ha_entities # Old stuff that HA doesn't have anymore?

    if missing_in_chroma:
        logger.warning(f"❌ MISSING {len(missing_in_chroma)} entities in Chroma (Live in HA but not Ingested):")
        for m in sorted(list(missing_in_chroma)):
            logger.warning(f"   - {m}")
    else:
        logger.info("✅ SUCCESS: All live entities are present in Chroma.")

    # Special Check for Music Assistant
    mass_live = [e for e in ha_entities if "mass" in e or "player" in e] # Broad check
    mass_stored = [e for e in stored_entities if e in mass_live]
    
    logger.info(f"Music Assistant/Media Check: {len(mass_stored)}/{len(mass_live)} present.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", type=str, help="HA URL")
    parser.add_argument("--token", type=str, help="HA All")
    args = parser.parse_args()
    
    verify_ingestion_completeness(args.url, args.token)
