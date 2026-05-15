import asyncio
import logging
from logic import media_ops
from logic.web_search import tool_web_search
from settings import GlobalResources, HA_ENV_TOKEN
import re

# Setup Logging
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("test_watch")

async def test_watch():
    print("--- STARTING WATCH TEST ---")
    
    # Mock Creds
    user_creds = {"ha_token": HA_ENV_TOKEN}
    entity_id = "media_player.28_tcl_roku_tv"
    command = "Watch trending cat videos"

    print("\n--- DIRECT SEARCH TEST ---")
    search_res = await tool_web_search("site:youtube.com trending cat videos")
    print("RAW SEARCH RESULT START:")
    print(search_res[:1000]) # Print first 1000 chars
    print("RAW SEARCH RESULT END")
    
    match = re.search(r'(https?://(?:www\.)?(?:youtube\.com/watch\?v=[^"\s]+|youtu\.be/[^"\s]+))', search_res)
    print(f"Regex Match: {match}")

    print("\n--- MEDIA OPS RESULT ---")
    try:
        result = await media_ops.handle_media_command(
            intent="play_media",
            query=command,
            entity_id=entity_id,
            user_creds=user_creds,
            ha_collection=None,
            redis_client=None
        )
        print(result)
    except Exception as e:
        print(f"ERROR: {e}")

if __name__ == "__main__":
    asyncio.run(test_watch())
