
import sys
import os
# Ensure app modules are importable
sys.path.append("/app")
from settings import CHROMA_DIR
import chromadb

def purge():
    print(f"Connecting to Chroma at {CHROMA_DIR}...")
    try:
        client = chromadb.PersistentClient(path=CHROMA_DIR)
        try:
            client.delete_collection("home_assistant")
            print("Deleted 'home_assistant' collection.")
        except ValueError:
            print("Collection 'home_assistant' does not exist.")
    except Exception as e:
        print(f"Failed to connect or delete: {e}")

if __name__ == "__main__":
    purge()
