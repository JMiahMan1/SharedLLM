import asyncio
import logging
import os
import sys
from dotenv import load_dotenv

# Setup Path
sys.path.append(os.getcwd())

# Configuration
load_dotenv()
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("VERIFY_FIREPLACE")

from app.settings import GlobalResources, load_resources
from app.domains.media.commands import handle_media_command

async def run_test():
    log.info("--- Starting Live Fireplace Verification ---")
    
    # Init Resources (Redis/Chroma needed for full pipeline)
    await load_resources()
    
    from app.settings import HA_ENV_TOKEN
    user_creds = {"user": "admin", "ha_token": HA_ENV_TOKEN}
    
    # Input
    intent = "watch_video" 
    query = "fireplace video"
    entity_id = "media_player.office_tv_chrome"
    device_name = "Office TV"
    tv_sibling = "media_player.office_tv"

    log.info(f"--- PRE-TEST: Ensuring {tv_sibling} and {entity_id} are OFF ---")
    from app.domains.shared import execute_ha_service
    await execute_ha_service(entity_id.split('.')[0], "turn_off", entity_id, user_creds, {}, None)
    await execute_ha_service(tv_sibling.split('.')[0], "turn_off", tv_sibling, user_creds, {}, None)
    
    log.info("Waiting 10s for power down...")
    await asyncio.sleep(10)

    log.info(f"Command: {intent} '{query}' on {entity_id}")
    
    # Execute
    result = await handle_media_command(
        intent=intent,
        query=query,
        entity_id=entity_id,
        user_creds=user_creds,
        ha_collection=GlobalResources.ha_collection,
        redis_client=GlobalResources.redis_client,
        device_name=device_name,
        integration="cast" # Hint integration as Router would
    )
    
    log.info(f"Result: {result}")

if __name__ == "__main__":
    if os.name == 'nt':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(run_test())
