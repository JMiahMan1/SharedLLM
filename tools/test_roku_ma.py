import asyncio
import requests
import sys
import logging
from app.settings import ROKU_USE_MEDIA_ASSISTANT
from app.domains.media.integrations.factory import IntegrationFactory

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("test_roku_ma")

# Test Data
ROKU_ENTITY_ID = "media_player.28_tcl_roku_tv" # Ensure this is correct
BIG_BUCK_BUNNY_URL = "https://download.blender.org/peach/bigbuckbunny_movies/BigBuckBunny_320x180.mp4"
MUSIC_URL = "https://www.soundhelix.com/examples/mp3/SoundHelix-Song-1.mp3"

USER_CREDS = {
    # Using dummy creds as script likely runs in env where app is running or needs valid token
    # For now, relying on existing app context if running via docker exec
    "ha_token": "dummy" # The integration reads this from settings if not passed? No, passes explicit user_creds.
    # We might need to fetch a valid token if we are running this standalone. 
    # BUT, we will run this via docker exec where app context is available? 
    # The integration calls requests.get(HA_URL, headers=...). We need a valid token.
}

# HACK: If running on server, we can try to grab token from ENV or rely on `execute_ha_service` internals
# Actually, let's use the CLI arg style or just hardcode for the test if running inside container
import os
HA_TOKEN = os.getenv("HA_TOKEN")
if HA_TOKEN:
    USER_CREDS["ha_token"] = HA_TOKEN

async def test_video():
    log.info("Testing Video Playback via Media-Assistant...")
    
    factory = IntegrationFactory()
    # Force getting our handler to check logic
    handler = factory.get_handler("roku")
    
    log.info(f"Handler Class: {handler.__class__.__name__}")
    
    if handler.__class__.__name__ != "RokuMediaAssistantIntegration":
         log.error("Factory returned wrong handler! Check settings.")
         return

    # Test Video
    result = await handler.play_media(
        entity_id=ROKU_ENTITY_ID,
        query=BIG_BUCK_BUNNY_URL, # Direct URL to skip youtube search overhead for basic test
        media_type="video",
        user_creds=USER_CREDS,
        media_title="Big Buck Bunny Test",
        device_name="Test Script"
    )
    
    log.info(f"Video Result: {result}")
    
async def test_music():
    log.info("Testing Music Metadata UI via Media-Assistant...")
    
    factory = IntegrationFactory()
    handler = factory.get_handler("roku")

    result = await handler.play_media(
        entity_id=ROKU_ENTITY_ID,
        query=MUSIC_URL,
        media_type="music",
        user_creds=USER_CREDS,
        media_title="Test Song",
        media_artist="Test Artist",
        media_album_name="Test Album",
        image_url="https://via.placeholder.com/500" # Dummy art
    )
    
    log.info(f"Music Result: {result}")

if __name__ == "__main__":
    if not ROKU_USE_MEDIA_ASSISTANT:
        log.warning("ROKU_USE_MEDIA_ASSISTANT is False! Test will fail to use correct integration.")
    
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    try:
        loop.run_until_complete(test_video())
        # loop.run_until_complete(test_music()) # Commented out to focus on Video first, or uncomment
    except Exception as e:
        log.error(f"Test Exception: {e}")
    finally:
        loop.close()
