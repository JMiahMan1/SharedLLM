import asyncio
import logging
from logic import media_ops
from settings import GlobalResources, HA_ENV_TOKEN

# Setup Logging
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("test_watch")

async def test_watch():
    print("--- STARTING WATCH TEST ---")
    
    # Mock Creds
    user_creds = {"ha_token": HA_ENV_TOKEN}
    
    # Mock Redis (Optional, might be skipped by ops if None)
    # GlobalResources.redis_client is usually None in standalone script unless initialized
    
    # Test Command
    entity_id = "media_player.28_tcl_roku_tv"
    command = "Watch trending cat videos"
    
    print(f"Testing command: '{command}' on '{entity_id}'")
    
    try:
        # Call the function directly
        result = await media_ops.handle_media_command(
            intent="play_media",
            query=command,
            entity_id=entity_id,
            user_creds=user_creds,
            ha_collection=None, # pass None for collection if safe, else we might crash
            redis_client=None
        )
        
        print("\n--- RESULT ---")
        print(result)
        
    except Exception as e:
        print(f"\nCRITICAL ERROR: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test_watch())
