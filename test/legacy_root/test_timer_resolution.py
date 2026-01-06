
import asyncio
import logging
import sys
import os

# Add app directory to path
sys.path.append(os.path.join(os.path.dirname(__file__), 'app'))

# Fix Logging Path for Test
os.environ["LOG_FILE"] = "./test_app.log"

from app.settings import load_resources, GlobalResources
from app.logic.timer_ops import _extract_target_device
from app.logic.media_ops import smart_resolve_entity

# Config logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
log = logging.getLogger(__name__)

async def test_resolution():
    log.info("Loading resources...")
    await load_resources()
    
    collection = GlobalResources.ha_collection
    if not collection:
        log.error("Failed to load HA Collection")
        return

    queries = [
        "set an egg timer on the office tv",
        "set a timer for 5 minutes on office tv",
        "set timer on master bedroom tv"
    ]

    for q in queries:
        log.info(f"\nTesting Query: '{q}'")
        
        # Test 1: Full Extraction Logic
        target_id, target_name = await _extract_target_device(q, collection)
        log.info(f"Extraction Result -> ID: {target_id}, Name: {target_name}")
        
        if not target_id and target_name:
            # Test 2: Direct Smart Resolution Debug
            log.info(f"Retrying direct resolution for '{target_name}' with 'play_media' intent...")
            res = await smart_resolve_entity(target_name, "play_media", collection)
            log.info(f"Smart Resolution Result: {res}")

if __name__ == "__main__":
    asyncio.run(test_resolution())
