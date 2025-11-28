# app/ha_ingest.py — Semantic Home Assistant Ingestion (Clean)
import os
import time
import json
import threading
import requests
import sys

# Import Dependencies with Error Handling for Container Logs
try:
    from langchain_core.documents import Document
    from langchain_huggingface import HuggingFaceEmbeddings
    from langchain_chroma import Chroma
except ImportError as e:
    print(f"CRITICAL: Missing dependency in Docker: {e}")
    sys.exit(1)

# Load .env
if os.getenv("DOCKER_ENV") != "1" and os.path.exists(".env"):
    from dotenv import load_dotenv
    load_dotenv(".env")

HA_URL = os.getenv("HA_URL")
HA_DEFAULT_USER = os.getenv("HA_DEFAULT_USER", "Admin")
CHROMA_DIR = os.getenv("CHROMA_PERSIST_DIR", "/data/chroma_db")

ALLOWED_DOMAINS = [
    "light", "switch", "sensor", "binary_sensor", "climate", 
    "lock", "cover", "person", "weather", "calendar", 
    "input_boolean", "media_player"
]

def get_user_creds(user=None):
    user = user or HA_DEFAULT_USER
    ha_token = os.getenv(f"HA_{user}_TOKEN") or os.getenv("HA_TOKEN")
    return {"user": user, "ha_token": ha_token}

def format_entity_natural_language(entity):
    eid = entity.get("entity_id", "")
    domain = eid.split(".")[0]
    state = entity.get("state", "unknown")
    attrs = entity.get("attributes", {})
    name = attrs.get("friendly_name", eid)

    if state in ["unavailable", "unknown"]:
        return None

    desc = f"{name} ({eid})"
    if domain == "light": desc += " is a light."
    elif domain == "switch": desc += " is a switch or smart plug."
    elif domain == "media_player": desc += " is a media player or smart speaker."
    elif domain == "binary_sensor": desc += " is a binary sensor."
    elif domain == "person": desc += " is a person tracker."
    elif domain == "climate": desc += " is a thermostat."
    elif domain == "lock": desc += " is a smart lock."
    elif domain == "sensor": 
        unit = attrs.get("unit_of_measurement", "")
        desc += f" is a sensor measuring {unit}."
        
    return desc, eid

def persist_ha_to_chroma():
    creds = get_user_creds()
    ha_token = creds["ha_token"]
    
    if not HA_URL or not ha_token:
        print("ERROR: HA configuration missing (URL or TOKEN). check .env")
        return

    base_url = HA_URL.rstrip("/")
    print(f"Connecting to Home Assistant at {base_url}...")

    try:
        headers = {"Authorization": f"Bearer {ha_token}"}
        r = requests.get(f"{base_url}/api/states", headers=headers, timeout=10)
        r.raise_for_status()
        states = r.json()

        # --- STEP 1: Process in Memory (Safe) ---
        docs = []
        skipped_count = 0
        
        for s in states:
            domain = s["entity_id"].split(".")[0]
            if domain not in ALLOWED_DOMAINS: continue

            result = format_entity_natural_language(s)
            if result:
                text, eid = result
                if text.strip():
                    docs.append(Document(page_content=text, metadata={"source": "home_assistant", "entity_id": eid}))
            else:
                skipped_count += 1

        print(f"Fetched {len(states)} raw entities. Processed {len(docs)} valid. Skipped {skipped_count} unavailable.")

        # --- STEP 2: Verify ---
        if not docs:
            print("WARNING: No valid entities to ingest. Database NOT wiped.")
            return

        # --- STEP 3: Write ---
        print(f"Initializing ChromaDB at {CHROMA_DIR}...")
        embeddings = HuggingFaceEmbeddings(model_name="sentence-transformers/all-MiniLM-L6-v2")
        
        # Reset Collection
        try:
            # We use a temp client to force a clean reset
            temp_db = Chroma(collection_name="ha_sensors", embedding_function=embeddings, persist_directory=CHROMA_DIR)
            try:
                temp_db.delete_collection()
            except: pass
        except Exception as e:
            print(f"Non-critical reset warning: {e}")

        # Re-initialize and Add
        vectordb = Chroma(collection_name="ha_sensors", embedding_function=embeddings, persist_directory=CHROMA_DIR)
        vectordb.add_documents(docs)
        # REMOVED .persist() call as it's deprecated and automatic in newer versions

        print(f"SUCCESS: Persisted {len(docs)} HA entities to Chroma.")

    except Exception as e:
        print(f"CRITICAL INGESTION ERROR: {e}")

if __name__ == "__main__":
    persist_ha_to_chroma()
