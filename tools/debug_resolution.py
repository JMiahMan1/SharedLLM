import asyncio
import os
import sys
import logging
import asyncio
import os
import sys
import logging
import json

# [FIX] Container has /app/__init__.py, making /app a package.
# To allow 'from app.settings', we must have '/' in python path so 'app' is found.
if "/" not in sys.path:
    sys.path.append("/")

# Configure logging
logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

async def debug_resolution(device_name, intent="play_media"):
    print(f"\n==================================================")
    print(f"DEBUG: Resolving '{device_name}' with intent '{intent}'")
    print(f"==================================================")

    try:
        from app.settings import GlobalResources
        from app.domains.media.devices import smart_resolve_entity, get_device_capabilities
        from app.domains.media.commands import _execute_transport_command, handle_media_command # To check swap logic if possible, or simulate it
        
        # 1. Simulate Smart Resolution
        print(f"\n[1] Calling smart_resolve_entity('{device_name}')...")
        resolved = await smart_resolve_entity(
            device_name, 
            intent, 
            GlobalResources.ha_collection, 
            is_music=True, # Assume music for tougher test
            is_video=False,
            allow_multiple=True
        )
        
        print(f"    Raw Result: {resolved}")
        
        entities = []
        if isinstance(resolved, list):
            entities = resolved
        elif isinstance(resolved, tuple):
            entities = [resolved]
        elif resolved:
            entities = [(resolved, "unknown")]
            
        if not entities:
            print("    [FAIL] No entities resolved.")
            return

        # 2. Simulate MASS Swap Logic (Copy-paste of logic in commands.py for verification)
        print(f"\n[2] Simulating MASS Intelligence Swap...")
        final_entities = []
        
        user_creds = {"user": "debug_user", "ha_token": os.getenv("HA_TOKEN"), "ha_url": os.getenv("HA_URL")}
        
        for entity_id, integration in entities:
            print(f"    Checking: {entity_id} ({integration})")
            
            # Fetch Caps
            caps = await get_device_capabilities(entity_id, user_creds, None)
            friendly_name = caps.get("friendly_name", device_name)
            print(f"    Friendly Name: {friendly_name}")
            
            # SWAP LOGIC
            new_id = entity_id
            new_int = integration
            
            # Condition: Music request, not already MA
            if integration != "music_assistant":
                print(f"    -> Not MA. Searching for shadow player for '{friendly_name}'...")
                search_name = friendly_name.replace(" TV", "").replace(" Speaker", "")
                
                ma_docs = GlobalResources.ha_collection.similarity_search(f"{search_name} music assistant", k=3)
                found = False
                for d in ma_docs:
                    d_id = d.metadata.get("entity_id")
                    d_int = d.metadata.get("integration")
                    d_name = d.metadata.get("friendly_name")
                    print(f"       Found candidate: {d_id} ({d_int}) - Name: {d_name}")
                    
                    if d_int == "music_assistant":
                         # Loose matching logic check
                         if search_name.lower() in d_name.lower() or search_name.lower() in d_id.lower():
                             print(f"       [SWAP MATCH] Swapping {entity_id} -> {d_id}")
                             new_id = d_id
                             new_int = "music_assistant"
                             found = True
                             break
                if not found:
                    print("       [NO SWAP] No matching MA player found.")
            else:
                print("    -> Already MA player.")
                
            final_entities.append((new_id, new_int))

        print(f"\n[3] Final Resolution Result:")
        for e, i in final_entities:
            print(f"    Target: {e} | Integration: {i}")

    except Exception as e:
        print(f"ERROR: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 debug_resolution.py <device_name>")
        sys.exit(1)
    
    device = sys.argv[1]
    asyncio.run(debug_resolution(device))
