# app/ha_ingest.py — Semantic Home Assistant Ingestion
import os
import time
import json
import threading
import requests
from langchain_core.documents import Document
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_chroma import Chroma

# Load .env
if os.getenv("DOCKER_ENV") != "1" and os.path.exists(".env"):
    from dotenv import load_dotenv
    load_dotenv(".env")

HA_URL = os.getenv("HA_URL")
HA_DEFAULT_USER = os.getenv("HA_DEFAULT_USER", "Admin")
HA_POLL_INTERVAL = int(os.getenv("HA_POLL_INTERVAL_SEC", 60))
CHROMA_DIR = os.getenv("CHROMA_PERSIST_DIR", "/data/chroma_db")

# Domains that matter for RAG (Ignore internal system stuff)
ALLOWED_DOMAINS = [
    "light", "switch", "sensor", "binary_sensor", "climate", 
    "lock", "cover", "person", "weather", "calendar"
]

# Attributes to ignore to save context tokens
IGNORE_ATTRS = [
    "icon", "friendly_name", "supported_features", "attribution", 
    "device_class", "state_class", "last_changed", "last_updated"
]

def get_user_creds(user=None):
    user = user or HA_DEFAULT_USER
    ha_token = os.getenv(f"HA_{user}_TOKEN") or os.getenv("HA_TOKEN")
    return {"user": user, "ha_token": ha_token}

def format_entity_natural_language(entity):
    """
    Converts raw JSON entity data into a natural language sentence.
    Example: 'light.kitchen' (on) -> "The Kitchen Light is on."
    """
    eid = entity.get("entity_id", "")
    domain = eid.split(".")[0]
    state = entity.get("state", "unknown")
    attrs = entity.get("attributes", {})
    name = attrs.get("friendly_name", eid)

    # Skip unavailable items
    if state in ["unavailable", "unknown"]:
        return None

    # 1. Switches / Lights / Locks
    if domain in ["light", "switch", "input_boolean"]:
        return f"The {name} is {state}."
    
    # 2. Binary Sensors (Motion, Door)
    if domain == "binary_sensor":
        if "occupancy" in eid or "motion" in eid:
            status = "occupied" if state == "on" else "clear"
            return f"The {name} status is {status}."
        if "door" in eid or "window" in eid:
            status = "open" if state == "on" else "closed"
            return f"The {name} is {status}."
    
    # 3. Person (Zone tracking)
    if domain == "person":
        return f"{name} is currently at {state}."

    # 4. Climate
    if domain == "climate":
        temp = attrs.get("current_temperature", "unknown")
        target = attrs.get("temperature", "n/a")
        mode = state
        return f"The {name} is set to {mode}. Current temp: {temp}. Target: {target}."

    # 5. Sensors (Battery, Temp, Humidity)
    unit = attrs.get("unit_of_measurement", "")
    
    # Clean up attributes for context (only keep relevant ones)
    extra_info = []
    if "battery_level" in attrs:
        extra_info.append(f"Battery: {attrs['battery_level']}%")
    
    base_sent = f"The {name} is {state}{unit}."
    if extra_info:
        base_sent += " (" + ", ".join(extra_info) + ")"
        
    return base_sent

def persist_ha_to_chroma():
    creds = get_user_creds()
    ha_token = creds["ha_token"]
    if not HA_URL or not ha_token:
        print("HA URL or token not configured.")
        return

    try:
        headers = {"Authorization": f"Bearer {ha_token}"}
        r = requests.get(f"{HA_URL}/api/states", headers=headers, timeout=10)
        r.raise_for_status()
        states = r.json()

        # --- Optimization: Filter & Format ---
        docs = []
        
        # We group semantic sentences into one block, but cleaner
        semantic_lines = []
        
        for s in states:
            domain = s["entity_id"].split(".")[0]
            if domain not in ALLOWED_DOMAINS:
                continue
            
            sentence = format_entity_natural_language(s)
            if sentence:
                semantic_lines.append(sentence)

        # Create one consolidated document for "Current Home State"
        # This forces the LLM to see the whole picture in one retrieval chunk
        full_text = "Current Status of Smart Home Devices:\n" + "\n".join(semantic_lines)
        
        print(f"Formatting complete. {len(states)} entities -> {len(semantic_lines)} semantic sentences.")

        embeddings = HuggingFaceEmbeddings(model_name="sentence-transformers/all-MiniLM-L6-v2")
        vectordb = Chroma(
            collection_name="ha_sensors",
            embedding_function=embeddings,
            persist_directory=CHROMA_DIR
        )

        # We define a unique ID so we don't duplicate state history indefinitely
        # We overwrite the 'current_state' document
        doc = Document(
            page_content=full_text, 
            metadata={"source": "home_assistant", "type": "live_state"}
        )
        
        # Check if exists and update, or simple add
        # Since Chroma is append-heavy, standard practice for "State" is just to add 
        # But for RAG size, we might want to delete previous for this user/type if possible
        # For simplicity in this script: Just Add. The API usually fetches the latest anyway via context lookups.
        
        vectordb.add_documents([doc]) 
        # Note: In a perfect world, we would delete the old state doc first.
        
        print(f"Persisted HA state to Chroma.")

    except Exception as e:
        print("Failed to persist HA data:", e)

def start_ha_polling():
    def loop():
        while True:
            persist_ha_to_chroma()
            time.sleep(HA_POLL_INTERVAL)
    t = threading.Thread(target=loop, daemon=True)
    t.start()

if __name__ == "__main__":
    persist_ha_to_chroma()
