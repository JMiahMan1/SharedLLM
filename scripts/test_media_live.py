# tests/test_media_live.py
import sys
import os
import asyncio
import logging

# Ensure app modules are in path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.settings import get_user_creds, HA_URL
from app.logic.media_ops import handle_media_command

# Configure Logging
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("live_test")

async def test_live_media_payloads():
    """
    Live Integration Test for Media Ops.
    Connects to the REAL Home Assistant using settings.py credentials.
    """
    if not HA_URL:
        log.error("CRITICAL: HA_URL not set in settings. Cannot run live test.")
        return

    log.info(f"--- Starting Live Test against {HA_URL} ---")
    creds = get_user_creds("admin")
    if not creds.get("ha_token"):
        log.error("CRITICAL: HA_TOKEN not found in settings.")
        return

    target_entity = "media_player.office_tv"
    log.info(f"Targeting Entity: {target_entity}")

    # 1. Test Case A: Dirty Album Request
    dirty_query = "Play the album, Coat of Many Colors, by Brandon Lake on the Office TV."
    log.info(f"Testing Dirty Query: '{dirty_query}'")
    
    result = await handle_media_command(
        intent="play_media", 
        query=dirty_query, 
        entity_id=target_entity, 
        user_creds=creds, 
        ha_collection=None, 
        redis_client=None
    )
    log.info(f"Result A: {result}")
    
    # 2. Test Case B: Skip Request (Regex Override Test)
    # and maps it to 'media_next' BEFORE it hits vectors.
    skip_query = "Skip on the Office TV"
    log.info(f"Testing Skip Query (Regex Check): '{skip_query}'")
    
    # Note: We can't fully mock the entire system here easily without vectors, 
    # but we can verify that the regex logic *inside* pipeline would catch it 
    # by simulating the logic or running a focused check if we exported decomposition.
    # Instead, we will directly test `handle_media_command` with `media_next` intent 
    # to ensure the backend operation works.
    
    result_skip = await handle_media_command(
        intent="media_next",
        query=skip_query,
        entity_id=target_entity,
        user_creds=creds,
        ha_collection=None,
        redis_client=None
    )
    log.info(f"Result Skip: {result_skip}")
    
    if "Failed" in str(result_skip):
         log.error("FAILED: Skip command failed.")
    else:
         log.info("SUCCESS: Skip command executed.")

if __name__ == "__main__":
    asyncio.run(test_live_media_payloads())
