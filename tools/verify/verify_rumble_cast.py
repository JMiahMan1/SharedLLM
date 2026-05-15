
import asyncio
import logging
import sys
import os
import json

# Ensure we can import app modules
sys.path.append(os.getcwd())

from app.settings import load_resources, GlobalResources
from app.domains.shared import execute_ha_service

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
log = logging.getLogger("RUMBLE_TEST")

# Sample Rumble Video (Viral cute cat video or similar stable URL)
# Using a generic popular one found in public searches or a placeholder
RUMBLE_URL = "https://rumble.com/v2n9z0w-cat-meowing.html"
# Alternatively, I will use a known working test URL if that fails.
# Actually, let's use a very standard one.
RUMBLE_URL = "https://rumble.com/v117k0d-relaxing-fireplace-4k-fire-place.html" 

# Mock Credentials (from settings or env)
USER_CREDS = {"ha_token": os.environ.get("HA_TOKEN", "")}

async def extract_url(url):
    try:
        import yt_dlp
        log.info(f"Extracting with yt-dlp: {url}")
        
        ydl_opts = {
            'format': 'best[ext=mp4]/best',
            'quiet': True,
            'no_warnings': True,
            'noplaylist': True,
            'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/90.0.4430.212 Safari/537.36',
        }
        
        # Run in executor
        loop = asyncio.get_running_loop()
        def _extract():
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                return ydl.extract_info(url, download=False)
        
        info = await loop.run_in_executor(None, _extract)
        return info.get('url'), info.get('title')
        
    except Exception as e:
        log.error(f"Extraction failed: {e}")
        return None, None

async def main():
    log.info("--- Starting Rumble Cast Verification ---")
    await load_resources()
    
    entity_id = "media_player.office_tv_chrome"
    
    # 1. Extract
    stream_url, title = await extract_url(RUMBLE_URL)
    
    if not stream_url:
        log.error("Could not extract stream URL. Aborting.")
        return

    log.info(f"Extracted: {title}")
    log.info(f"Stream URL: {stream_url}")
    
    # 2. Cast
    log.info(f"Casting to {entity_id}...")
    
    # For generic video, we use media_content_type: 'video'
    # This triggers the Default Media Receiver
    payload = {
        "media_content_id": stream_url,
        "media_content_type": "video",
        "title": title # Optional, HA might pass this to Chromecast
    }
    
    res = await execute_ha_service(
        "media_player", 
        "play_media", 
        entity_id, 
        USER_CREDS, 
        payload, 
        None # No Redis needed for this direct test
    )
    
    log.info(f"Service Call Result: {res}")
    log.info("Check TV for playback!")

if __name__ == "__main__":
    asyncio.run(main())
