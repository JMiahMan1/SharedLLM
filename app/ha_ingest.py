# app/ha_ingest.py — Semantic Home Assistant Ingestion (Hybrid Granular)
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
    "light",
    "switch",
    "sensor",
    "binary_sensor",
    "climate",
    "lock",
    "cover",
    "person",
    "weather",
    "calendar",
    "input_boolean",
    "media_player",
]

# Attributes to ignore to save context tokens
IGNORE_ATTRS = [
    "icon",
    "friendly_name",
    "supported_features",
    "attribution",
    "device_class",
    "state_class",
    "last_changed",
    "last_updated",
]


def get_user_creds(user=None):
    user = user or HA_DEFAULT_USER
    ha_token = os.getenv(f"HA_{user}_TOKEN") or os.getenv("HA_TOKEN")
    return {"user": user, "ha_token": ha_token}


def format_entity_natural_language(entity):
    """
    Converts raw JSON entity data into a natural language description for indexing.
    Returns tuple: (Description, Entity_ID)
    """
    eid = entity.get("entity_id", "")
    domain = eid.split(".")[0]
    state = entity.get("state", "unknown")
    attrs = entity.get("attributes", {})
    name = attrs.get("friendly_name", eid)

    # Skip unavailable items
    if state in ["unavailable", "unknown"]:
        return None

    # Descriptive string for Vector Search (Identity > State)
    # We emphasize WHAT it is, so the RAG can find it.
    desc = f"{name} ({eid})"

    if domain == "light":
        desc += " is a light."
    elif domain == "switch":
        desc += " is a switch or smart plug."
    elif domain == "binary_sensor":
        if "motion" in eid:
            desc += " is a motion sensor."
        elif "door" in eid or "window" in eid:
            desc += " is a door/window sensor."
        else:
            desc += " is a binary sensor."
    elif domain == "person":
        desc += " is a person tracker."
    elif domain == "climate":
        desc += " is a thermostat/climate control."
    elif domain == "lock":
        desc += " is a smart lock."
    elif domain == "sensor":
        unit = attrs.get("unit_of_measurement", "")
        desc += f" is a sensor measuring {unit}."
    elif domain == "media_player":
        desc += " is a media player or smart speaker." # Added media_player context
        
    # CRITICAL ENSURE: Return a non-empty string.
    return desc, eid


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

        embeddings = HuggingFaceEmbeddings(
            model_name="sentence-transformers/all-MiniLM-L6-v2"
        )
        vectordb = Chroma(
            collection_name="ha_sensors",
            embedding_function=embeddings,
            persist_directory=CHROMA_DIR,
        )

        # --- Cleanup Old Data (Crucial for Hybrid) ---
        # The simplest way to clear potential bad data is to delete and re-add.
        try:
            # We attempt to delete the collection entirely, as the failure logs suggest data corruption.
            vectordb.delete_collection()
            # Re-create the collection
            vectordb = Chroma(
                collection_name="ha_sensors",
                embedding_function=embeddings,
                persist_directory=CHROMA_DIR,
            )
            print("Cleared and reset 'ha_sensors' collection to fix indexing errors.")
        except Exception as e:
             # If deletion itself fails (e.g., initial run), just log and continue
            print(f"Warning during collection reset: {e}")


        # --- Format & Index ---
        docs = []
        for s in states:
            domain = s["entity_id"].split(".")[0]
            if domain not in ALLOWED_DOMAINS:
                continue

            result = format_entity_natural_language(s)
            if result:
                text, eid = result
                # Final guard: ensure text is not empty before creating Document
                if text.strip():
                    # Create Document with Entity ID in metadata
                    doc = Document(
                        page_content=text,
                        metadata={"source": "home_assistant", "entity_id": eid},
                    )
                    docs.append(doc)

        if docs:
            vectordb.add_documents(docs)
            print(f"Persisted {len(docs)} HA entities to Chroma (Granular).")
            # CRITICAL: Persist after successful update
            vectordb.persist() 
        else:
            print("No valid HA entities found to persist.")

    except Exception as e:
        print("Failed to persist HA data:", e)


def start_ha_polling():
    def loop():
        while True:
            # Added a print statement for polling
            print(f"HA Polling: Running incremental update...")
            persist_ha_to_chroma()
            time.sleep(HA_POLL_INTERVAL)

    t = threading.Thread(target=loop, daemon=True)
    t.start()


if __name__ == "__main__":
    persist_ha_to_chroma()
