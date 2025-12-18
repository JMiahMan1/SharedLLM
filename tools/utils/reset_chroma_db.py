# reset_db.py
from langchain_chroma import Chroma
from langchain_huggingface import HuggingFaceEmbeddings
import os

CHROMA_DIR = os.getenv("CHROMA_PERSIST_DIR", "/data/chroma_db")
emb = HuggingFaceEmbeddings(model_name="sentence-transformers/all-MiniLM-L6-v2")

print(f"Wiping collections in {CHROMA_DIR}...")

# Wipe HA Collection
try:
    ha_db = Chroma(
        collection_name="ha_sensors",
        embedding_function=emb,
        persist_directory=CHROMA_DIR,
    )
    ha_db.delete_collection()
    print("Deleted 'ha_sensors' collection.")
except:
    pass

# Wipe Nextcloud Collection
try:
    nc_db = Chroma(
        collection_name="nextcloud_docs",
        embedding_function=emb,
        persist_directory=CHROMA_DIR,
    )
    nc_db.delete_collection()
    print("Deleted 'nextcloud_docs' collection.")
except:
    pass

print("Done. Database is empty.")
