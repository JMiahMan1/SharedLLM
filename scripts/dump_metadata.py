

import asyncio
import logging
import sys
import os
import requests
import json

# Configure basic logging
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("MetadataDump")

# Remote API Configuration
REMOTE_URL = "http://ai.local:11435/api/rag/search"

def dump_metadata():
    query = "Gracies TV"
    log.info(f"Querying Remote API for '{query}'...")
    
    try:
        # The /api/rag/search endpoint returns vector search results
        response = requests.get(REMOTE_URL, params={"q": query, "k": 5, "source": "ha"}, timeout=10)
        
        if response.status_code == 200:
            data = response.json()
            results = data.get("results", [])
            
            print("\n--- METADATA DUMP (REMOTE) ---")
            for doc in results:
                meta = doc.get("metadata", {})
                eid = meta.get("entity_id")
                friendly = meta.get("friendly_name")
                print(f"\nEntity: {eid} ({friendly})")
                print("Metadata:")
                for k, v in meta.items():
                    print(f"  {k}: {v}")
            print("\n---------------------\n")
        else:
            log.error(f"API Request Failed: {response.status_code} - {response.text}")

    except Exception as e:
        log.error(f"Error querying remote API: {e}")

if __name__ == "__main__":
    dump_metadata()

