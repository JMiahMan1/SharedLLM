# app/ha_ingest.py
import os
import time
import json
import threading
import requests
from langchain_core.documents import Document
from langchain_community.embeddings import HuggingFaceEmbeddings
from langchain_community.vectorstores import Chroma

# Load .env for local runs
if os.getenv("DOCKER_ENV") != "1" and os.path.exists(".env"):
    from dotenv import load_dotenv
    load_dotenv(".env")

HA_URL = os.getenv("HA_URL")
HA_DEFAULT_USER = os.getenv("HA_DEFAULT_USER", "Admin")
HA_POLL_INTERVAL = int(os.getenv("HA_POLL_INTERVAL_SEC", 60))
CHROMA_DIR = os.getenv("CHROMA_PERSIST_DIR", "/data/chroma_db")

def get_user_creds(user=None):
    user = user or HA_DEFAULT_USER
    ha_token = os.getenv(f"HA_{user}_TOKEN") or os.getenv("HA_TOKEN")
    return {"user": user, "ha_token": ha_token}

def persist_ha_to_chroma():
    """Pull all sensor data from Home Assistant and store in Chroma DB."""
    creds = get_user_creds()
    ha_token = creds["ha_token"]
    if not HA_URL or not ha_token:
        print("HA URL or token not configured, skipping HA ingest.")
        return

    try:
        headers = {"Authorization": f"Bearer {ha_token}"}
        r = requests.get(f"{HA_URL}/api/states", headers=headers, timeout=15)
        r.raise_for_status()
        states = r.json()

        # Build text representation of all sensors
        lines = []
        for s in states:
            eid = s.get("entity_id")
            st = s.get("state")
            attrs = s.get("attributes", {})
            if isinstance(st, dict):
                st = json.dumps(st)
            lines.append(f"{eid}: {st} | attrs: {json.dumps(attrs)}")

        ha_text = "\n".join(lines)
        if not ha_text.strip():
            print("⚠ No sensor data found to store.")
            return

        print(f"Initializing Chroma store and embeddings for HA data...")
        embeddings = HuggingFaceEmbeddings(model_name="sentence-transformers/all-MiniLM-L6-v2")
        vectordb = Chroma(
            collection_name="ha_sensors",
            embedding_function=embeddings,
            persist_directory=CHROMA_DIR
        )

        doc = Document(page_content=ha_text, metadata={"source": "home_assistant", "user": creds["user"]})
        vectordb.add_documents([doc])
        vectordb.persist()
        print(f"HA data persisted with {len(states)} entities.")

    except Exception as e:
        print("Failed to persist HA data:", e)

def start_ha_polling():
    """Start background thread to pull HA data every POLL_INTERVAL."""
    def loop():
        while True:
            persist_ha_to_chroma()
            time.sleep(HA_POLL_INTERVAL)
    t = threading.Thread(target=loop, daemon=True)
    t.start()
    print(f"Started HA polling every {HA_POLL_INTERVAL} seconds.")

if __name__ == "__main__":
    persist_ha_to_chroma()

