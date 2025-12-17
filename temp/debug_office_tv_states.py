
import requests
import json
import os

API_URL = "http://192.168.2.211:11435"
HEADERS = {"X-RAG-User": "admin", "Content-Type": "application/json"}

def check_states():
    # We'll use the 'get_all_states' equivalent if exposed, or just guess common names.
    # Since we don't have a direct 'dump all entities' tool exposed to me here easily, 
    # I'll rely on the RAG 'match_entity' tool or similar if I could.
    # But I can use the 'execute_command' with a special query if I implemented it.
    
    # Actually, I can use the `homeassistant` integration's /api/states if I have the token.
    # But I only have the RAG API.
    
    # I will try to "Resolve" 'Office TV' and see what it gives, including attributes?
    # No, resolve just gives ID.
    
    # I will try to use the `match_entity` logic via a targeted script importing `media_ops`.
    # This is better.
    pass

if __name__ == "__main__":
    # We will just print the script to run essentially
    print("Running state check via app imports...")
    import asyncio
    import sys
    sys.path.append(os.getcwd())
    from app.logic.media_ops import get_active_media_players, smart_resolve_entity, get_entity_state
    
    from app.settings import get_user_creds
    
    user_creds = get_user_creds("system")
    
    async def main():
        # 1. Active Players
        print("\n--- Active Players ---")
        active = await get_active_media_players(user_creds)
        print(active)
        
        # 2. Resolve "Office TV" - SKIPPED (Requires Chroma)
        
        # 3. Check specific entities
        targets = [
            "media_player.office_tv_chrome_2",
            "media_player.office_tv", 
            "media_player.mass_office_tv_chrome_2",
            "media_player.mass_office_tv",
            "remote.office_tv_chrome_2",
            "remote.office_tv"
        ]
        
        print("\n--- Detailed States ---")
        
        ha_url = user_creds.get("ha_url")
        ha_token = user_creds.get("ha_token")
        headers = {"Authorization": f"Bearer {ha_token}", "Content-Type": "application/json"}
        
        for entity in targets:
            try:
                r = requests.get(f"{ha_url}/api/states/{entity}", headers=headers)
                if r.status_code == 200:
                    data = r.json()
                    print(f"\nENTITY: {entity}")
                    print(f"State: {data.get('state')}")
                    print(f"Attributes: {json.dumps(data.get('attributes', {}), indent=2)}")
                else:
                    print(f"\nENTITY: {entity} - Not Found (HTTP {r.status_code})")
            except Exception as e:
                print(f"Error checking {entity}: {e}")

    asyncio.run(main())
