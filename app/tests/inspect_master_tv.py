
import asyncio
import logging
import sys
import os
from pprint import pprint

# Add app to path
sys.path.append(os.getcwd())

# Force load settings to get env vars and init resources
from dotenv import load_dotenv
load_dotenv()

from app.settings import GlobalResources, init_chroma
# We rely on app's internal chroma init which might work better or reuse existing connection?
# Actually, verifying `get_device_capabilities` usage.

from app.domains.media.devices import get_device_capabilities

# Setup Logging
logging.basicConfig(level=logging.INFO)

async def main():
    # We need to initialize GlobalResources manually if not running full app
    import chromadb
    from app.settings import CHROMA_DIR
    try:
        # Try direct init again? No, it failed.
        # Let's try to mock the DB client if we can't load it, 
        # BUT we need real data.
        # The app runs fine, meaning the environment for the APP is different or I am running in a shell with different libs?
        # The previous run_command used python3. 
        
        # Let's try to just use the factory to resolve integration type for the entity
        # Factory relies on string passed to it, or capabilities.
        pass
    except:
        pass

    # Alternative: Use the HA API to get attributes, which often mirror what stored in Chroma
    # Chroma is populated FROM HA.
    # So if I inspect HA attributes for `master_bedroom_tv`, I can infer the integration.
    
    import aiohttp
    from app.settings import HA_URL, HA_TOKEN
    
    url = f"{HA_URL}/api/states/media_player.master_bedroom_tv"
    headers = {"Authorization": f"Bearer {HA_TOKEN}", "Content-Type": "application/json"}
    
    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers) as resp:
            if resp.status == 200:
                state = await resp.json()
                print("\n[HA Attributes]")
                pprint(state)
                
                attrs = state.get("attributes", {})
                friendly = attrs.get("friendly_name")
                
                # Check known signatures
                # Roku usually has 'app_name', 'source_list' with channels
                # Android TV often has 'adb_response', 'app_id'
                
                if "roku" in friendly.lower() or "roku" in str(attrs).lower():
                    print("\n[Inference] Likely Roku.")
                elif "android" in str(attrs).lower():
                    print("\n[Inference] Likely Android TV.")
            else:
                print(f"Failed to get state: {resp.status}")

if __name__ == "__main__":
    asyncio.run(main())
