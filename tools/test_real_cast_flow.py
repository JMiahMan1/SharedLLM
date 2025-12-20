#!/usr/bin/env python3
"""
Test Script: Real-World Android Cast Flow
Verifies: Search (Simulated) -> REAL Download (yt-dlp) -> Direct Play (Media Assistant)
"""
import asyncio
import logging
import os
import sys
import shutil

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
log = logging.getLogger()

# Add project root to path
sys.path.append(os.getcwd())

# Set Environment Variables needed for app.settings
os.environ["HA_URL"] = "https://ha.sumemail.com" # Mock/Real
os.environ["SERVER_URL"] = "http://192.168.2.211:11435" # Important for local_url generation
os.environ["CAST_CACHE_DIR"] = os.path.join(os.getcwd(), "temp", "cast_videos") # Local writable path

from app.domains.media.integrations.roku import RokuIntegration

# Mock User Creds
USER_CREDS = {"ha_token": os.getenv("HA_TOKEN", "fake_token")}

# Target Entity
ENTITY_ID = "media_player.28_tcl_roku_tv"
SEARCH_QUERY = "Brandon Lake Gratitude"

# IMPORTANT: We need to mock 'tool_web_search' to return a deterministic URL
# because we might not have internet search keys or want flaky results.
# But we DO want the rest of the flow (Download -> Cast) to be REAL.

import app.logic.web_search
async def mock_tool_web_search(query):
    log.info(f"[MockSearch] Searching for: {query}")
    # Return a real, short video URL to test downloading (Brandon Lake - Gratitude)
    # Using a shorter test video might be faster for testing, but let's use the requested one.
    # Provided URL is an example. Let's use a known safe test URL if possible, OR the real one.
    # Real One: https://www.youtube.com/watch?v=dQw4w9WgXcQ (Actually Rick Roll :P)
    # Let's use the one from the file check earlier if we want to be safe? 
    # No, user said "starts to download it". So let's download something new or existing.
    
    # Let's use a Rick Roll for reliability of existence, or a small video.
    # Small video: https://www.youtube.com/watch?v=jNQXAC9IVRw (Me at the zoo)
    return "URL: https://www.youtube.com/watch?v=jNQXAC9IVRw"

# Patch the web search function
app.logic.web_search.tool_web_search = mock_tool_web_search

async def main():
    log.info(f"--- Starting REAL Android Cast Test: '{SEARCH_QUERY}' ---")
    
    roku = RokuIntegration()
    
    # Verify yt-dlp is present
    if not shutil.which("yt-dlp"):
         log.error("❌ yt-dlp is NOT installed! Real download will fail.")
         return

    # Call play_media with the SEARCH QUERY string.
    # This forces RokuIntegration to call 'tool_web_search' (mocked),
    # get the URL, then call '_download_and_serve_video' (REAL),
    # then call '_play_media_direct' (REAL).
    
    result = await roku.play_media(
        entity_id=ENTITY_ID, 
        query=SEARCH_QUERY, 
        media_type="video", 
        user_creds=USER_CREDS
    )
    
    log.info(f"--- Test Result: {result} ---")

if __name__ == "__main__":
    asyncio.run(main())
