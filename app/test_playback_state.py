
import asyncio
import logging
import time
from app.settings import load_resources, GlobalResources, get_user_creds
from app.logic.media_ops import handle_media_command, get_entity_state
from app.tests.functional_suite import verify_state

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
log = logging.getLogger("PlaybackTest")

async def poll_state(entity_id, duration=60):
    start_time = time.time()
    last_state = None
    
    log.info(f"Polling state for {entity_id} for {duration} seconds...")
    
    while (time.time() - start_time) < duration:
        # We need to access HA directly or via get_entity_state
        # verify_state from functional_suite does direct API calls
        from app.settings import HA_URL, HA_ENV_TOKEN
        import aiohttp
        
        headers = {"Authorization": f"Bearer {HA_ENV_TOKEN}", "Content-Type": "application/json"}
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{HA_URL}/api/states/{entity_id}", headers=headers) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    state = data.get("state")
                    attrs = data.get("attributes", {})
                    app = attrs.get("app_name")
                    source = attrs.get("source")
                    
                    if state != last_state:
                        log.info(f"State Change: {state} (App: {app}, Source: {source})")
                        last_state = state
                    
                    if state == "playing":
                        log.info("SUCCESS: State is PLAYING!")
                        return True
                        
                else:
                    log.warning(f"API Error: {resp.status}")
        
        await asyncio.sleep(2)
    
    log.error("Timeout: Never reached 'playing' state.")
    return False

async def run_test():
    await load_resources()
    user_creds = get_user_creds("Jeremiah")
    
    # Resolve
    entity_id = "media_player.28_tcl_roku_tv"
    log.info(f"Target: {entity_id}")
    
    # Play
    log.info("Sending Play Command...")
    await handle_media_command(
        "play_media", 
        "Play Brandon Lake on Gracies TV", 
        entity_id=entity_id, 
        user_creds=user_creds, 
        ha_collection=GlobalResources.ha_collection, 
        redis_client=GlobalResources.redis_client,
        device_name="Gracies TV"
    )
    
    # Poll
    await poll_state(entity_id)

if __name__ == "__main__":
    asyncio.run(run_test())
