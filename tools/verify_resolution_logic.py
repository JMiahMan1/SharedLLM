
import asyncio
import logging
from unittest.mock import MagicMock

# Mock logging
logging.basicConfig(level=logging.INFO)

# 1. Mock Data for Devices
# Roku Device (Gracie's TV)
ROKU_CANDIDATE = {
    "entity_id": "media_player.roku_2n0062385487",
    "integration": "roku",
    "friendly_name": "Gracie's TV",
    "attributes": {"friendly_name": "Gracie's TV"} 
}
# Android TV - Cast Device (Should be lower priority for Power, high for Music)
OFFICE_CAST_CANDIDATE = {
    "entity_id": "media_player.office_tv_chrome_2",
    "integration": "cast",
    "friendly_name": "Office TV",
    "attributes": {"friendly_name": "Office TV", "app_id": "CC1AD845"} # Cast app id
}
# Remote (Hypothetical, addressing user feedback)
OFFICE_REMOTE_CANDIDATE = {
    "entity_id": "remote.office_tv",
    "integration": "lovelace", # generic
    "friendly_name": "Office TV",
    "attributes": {"friendly_name": "Office TV"}
}

ALL_CANDIDATES = [ROKU_CANDIDATE, OFFICE_CAST_CANDIDATE, OFFICE_REMOTE_CANDIDATE]

# 2. Mock GlobalResources and HA Collection
class MockCollection:
    def get(self):
        # Return all mock candidates in the structure expected: {"metadatas": [...]}
        return {"metadatas": ALL_CANDIDATES}
    
    def similarity_search_with_score(self, query, k=10):
        # Simple mock matching
        results = []
        q = query.lower()
        for c in ALL_CANDIDATES:
            if c["friendly_name"].lower() in q:
                # Mock a Document object
                doc = MagicMock()
                doc.metadata = c
                results.append(doc)
        return results

# 3. Import the Target Function (Needs mocking of dependencies inside modules)
import sys
import types
sys.modules['app.settings'] = MagicMock()
sys.modules['app.settings'].GlobalResources.ha_collection = MockCollection()
# Mock blocking runner to just execute async
async def mock_run_blocking(f):
    if asyncio.iscoroutinefunction(f):
        return await f()
    return f()

sys.modules['app.settings'].run_blocking = mock_run_blocking 

# Mock lazy imports
sys.modules['langchain_chroma'] = MagicMock()
sys.modules['app.logic.pattern_matching'] = MagicMock()
sys.modules['app.logic.pattern_matching'].detect_number_pattern = lambda x: [] # No pattern

# Now import the code under test
import os
sys.path.append(os.getcwd())

from app.domains.media.devices import smart_resolve_entity

async def run_test(name, query, intent, is_music, is_video, expected_id):
    print(f"--- TEST: {name} ---")
    print(f"Query: '{query}', Intent: '{intent}', Music={is_music}, Video={is_video}")
    
    # Run resolution
    # Note: smart_resolve_entity signature: (query_name, intent, ha_collection, is_music, is_video, allow_multiple)
    try:
        result = await smart_resolve_entity(
            query, intent, MockCollection(), is_music=is_music, is_video=is_video
        )
        
        # Result is (entity_id, integration, meta)
        if result and result[0] == expected_id:
            print(f"✅ PASS: Resolved to {result[0]}")
        else:
            print(f"❌ FAIL: Expected {expected_id}, got {result[0] if result else 'None'}")
            if result: print(f"   (Integration: {result[1]})")

    except Exception as e:
        print(f"❌ ERROR: {e}")

async def main():
    print("=== Modular Resolution Logic Verification ===\n")

    # TEST 1: Roku Power Off
    # Should resolve to the Roku media player (roku_2n0062385487)
    await run_test(
        "Roku Turn Off", 
        "Turn off Gracie's TV", 
        "turn_off", 
        is_music=False, is_video=False, 
        expected_id="media_player.roku_2n0062385487"
    )

    # TEST 2: Android TV Power Off
    # Should resolve to the pure TV or Remote (not Cast)
    # Here we expect the Samsung TV entity or Remote over the Cast entity
    # Based on our priority list: remote > samsungtv/androidtv > cast
    # If we have "remote.office_tv" (mocked), it should win.
    await run_test(
        "Android TV Power Off (Remote Pref)", 
        "Turn off Office TV", 
        "turn_off", 
        is_music=False, is_video=False, 
        expected_id="remote.office_tv" 
    )

    # TEST 3: Android TV Music Play
    # Should resolve to the Cast device (Music Assistant preference)
    await run_test(
        "Android TV Music", 
        "Play music on Office TV", 
        "play_media", 
        is_music=True, is_video=False, 
        expected_id="media_player.office_tv_chrome_2"
    )

    # TEST 4: Roku Video Play
    # Should resolve to Roku (Native)
    await run_test(
        "Roku Video", 
        "Watch video on Gracie's TV", 
        "play_media", # or watch_media if mapped
        is_music=False, is_video=True, 
        expected_id="media_player.roku_2n0062385487"
    )

if __name__ == "__main__":
    asyncio.run(main())
