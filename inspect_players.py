
from app.settings import GlobalResources, load_resources
import asyncio
import logging
import os

# Setup logging
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("inspect_entities")

async def list_all_players():
    print("--- Listing All Media Players ---")
    await load_resources()
    
    # Get all media players
    docs = GlobalResources.ha_collection.get()
    
    ma_players = []
    cast_players = []
    others = []
    
    if docs and docs['ids']:
        for i, eid in enumerate(docs['ids']):
            if not eid.startswith('media_player.'): continue
            
            meta = docs['metadatas'][i]
            attrs = str(meta.get("attributes", "")).lower()
            integ = meta.get("integration", "")
            name = meta.get("friendly_name", "")
            
            info = {
                "id": eid,
                "name": name,
                "integration": integ,
                "model": meta.get("model"),
                "mfr": meta.get("manufacturer"),
                "is_ma": "music_assistant" in integ or "mass_" in eid or "music_assistant" in attrs
            }
            
            if info["is_ma"]:
                ma_players.append(info)
            elif "cast" in integ or "chrome" in eid:
                cast_players.append(info)
            else:
                others.append(info)

    print("\n--- MUSIC ASSISTANT PLAYERS ---")
    for p in ma_players:
        print(f"ID: {p['id']} | Name: {p['name']} | Model: {p['model']} | Mfr: {p['mfr']}")

    print("\n--- CAST PLAYERS ---")
    for p in cast_players:
        print(f"ID: {p['id']} | Name: {p['name']} | Model: {p['model']} | Mfr: {p['mfr']}")

if __name__ == "__main__":
    if not os.getenv("OPENAI_API_KEY"):
         # Mock key to prevent startup warning crash if any
         os.environ["OPENAI_API_KEY"] = "sk-dummy"
    asyncio.run(list_all_players())
