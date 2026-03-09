
import asyncio
import logging
import sys
import os
from unittest.mock import MagicMock, patch

# Add app to path
sys.path.append(os.getcwd())

from app.domains.media.devices import smart_resolve_entity
from app.domains.media.integrations.factory import IntegrationFactory
from app.settings import GlobalResources

# Setup Logging
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("AndroidVerifier")

async def main():
    print("\n--- Testing Android TV Resolution ---")
    
    # Mock ChromaDB Collection
    mock_collection = MagicMock()
    GlobalResources.ha_collection = mock_collection
    
    # Mock Data: Office TV (Android TV)
    office_tv_meta = {
        "entity_id": "media_player.office_tv",
        "friendly_name": "Office TV",
        "integration": "androidtv",
        "supported_features": 12345,
        "group_id": "office_group"
    }
    
    # Mock Data: Office TV Remote (Android TV Remote)
    office_remote_meta = {
        "entity_id": "remote.office_tv",
        "friendly_name": "Office TV Remote",
        "integration": "androidtv_remote",
        "group_id": "office_group"
    }
    
    # Mock Search Results
    # 1. Exact Match for "Office TV"
    mock_collection.get.side_effect = lambda where=None, ids=None, include=None: {
        "metadatas": [office_tv_meta] if ids else []
    }
    # Mock Group Search (for siblings)
    mock_collection._collection.get.return_value = {
        "metadatas": [office_tv_meta, office_remote_meta]
    }
    
    # TEST 1: Resolve "Turn on Office TV"
    print("\n[Test 1] Query: 'Turn on Office TV'")
    
    # We have to patch the internal Chroma queries in smart_resolve_entity if we can't mock the collection perfectly
    # But let's verify if smart_resolve_entity uses GlobalResources.ha_collection directly
    
    # For this test, valid exact match is critical.
    # The logic does: exact_matches.append if entity_id or friendly_name matches.
    
    # We'll use a simplified flow: Mock `get_device_capabilities` to ensure high score
    with patch("app.domains.media.devices.run_blocking", new_callable=MagicMock) as mock_blocking:
        mock_blocking.return_value = {"metadatas": [office_tv_meta]}
        
        # We need to populate strict matches in smart_resolve_entity memory or mock the DB call
        # smart_resolve_entity calls `GlobalResources.ha_collection.get` inside `run_blocking` usually?
        # Actually it calls `run_blocking(lambda: ha_collection.get(...))`
        
        # Let's bypass the complex DB logic and test Integration Factory directly for the Android TV entity
        # This confirms if "Android integrations... are not working" due to code breakage vs resolution breakage.
        
        
        print("Checking Integration Factory for 'media_player.office_tv' (integration=androidtv)...")
        
        # Patch where IntegrationFactory might import get_device_capabilities from, OR mostly it doesn't use it.
        # Factory logic uses the passed string locally.
        
        try:
            # Use get_handler which exists
            integration = IntegrationFactory.get_handler("androidtv")
            print(f"Integration Loaded: {integration}")
            print(f"Type: {type(integration)}")
            print(f"Has turn_on: {hasattr(integration, 'turn_on')}")
            print(f"Has turn_off: {hasattr(integration, 'turn_off')}")
            print(f"Has play_media: {hasattr(integration, 'play_media')}")
            
        except Exception as e:
            print(f"FAILURE: Could not load integration. Error: {e}")
            import traceback
            traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(main())
