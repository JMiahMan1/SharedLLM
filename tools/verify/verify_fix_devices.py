import asyncio
import os
import sys
from unittest.mock import MagicMock

# Add project root to path
sys.path.append(os.getcwd())

sys.modules['app.logic.pattern_matching'] = MagicMock()
# Mock detect_number_pattern to return empty (no pattern)
sys.modules['app.logic.pattern_matching'].detect_number_pattern = MagicMock(return_value=None)

# Mock GlobalResources
mock_resources = MagicMock()
sys.modules['app.settings'].GlobalResources = mock_resources  # type: ignore[attr-defined]


# helper for run_blocking (it just awaits the lambda or executes it)
async def mock_run_blocking(func):
    if asyncio.iscoroutinefunction(func):  # type: ignore[deprecated]
        return await func()
    return func()
sys.modules['app.settings'].run_blocking = mock_run_blocking  # type: ignore[attr-defined]

# Import the target function
# We need to ensure we import it AFTER mocking
from app.domains.media.devices import smart_resolve_entity  # pyright: ignore[reportMissingImports]


async def test_resolve_priority():
    print("Testing Smart Resolve Entity Priority...")

    # Setup mock collection response for EXACT match
    # We simulate GlobalResources.ha_collection.get() returning all matches
    mock_metadatas = [
        {"friendly_name": "Office TV", "entity_id": "media_player.office_tv", "integration": "androidtv"},
        {"friendly_name": "Office TV", "entity_id": "media_player.office_tv_chrome", "integration": "cast"},
        # Add a MA one just in case
        {"friendly_name": "Office TV", "entity_id": "media_player.office_tv_mass", "integration": "music_assistant"},
    ]

    # Mock GlobalResources.ha_collection.get() to return our list
    # The code calls: GlobalResources.ha_collection.get()
    mock_resources.ha_collection.get = MagicMock(return_value={"metadatas": mock_metadatas})

    # Test 1: Video Intent (or default) -> Should prefer AndroidTV
    print("\n[Test 1] Video/Default Request ('turn_off')")
    # Resolve
    res = await smart_resolve_entity("Office TV", "turn_off", mock_resources.ha_collection, is_music=False, is_video=True)

    if not res:
        print("FAIL: No result returned.")
        return

    # Result format is (eid, integ, meta)
    if len(res) == 3:
        eid, integ, _meta = res
    else:
        eid, integ = res

    print(f"Result: {eid} ({integ})")
    if integ == 'androidtv' or integ == 'roku':
        print("PASS: Selected TV device for non-music.")
    else:
        print(f"FAIL: Selected {integ} instead of androidtv.")

    # Test 2: Music Intent -> Should prefer Cast/MA
    print("\n[Test 2] Music Request")
    res_music = await smart_resolve_entity("Office TV", "play_media", mock_resources.ha_collection, is_music=True)

    if not res_music:
        print("FAIL: No result returned for Music.")
        return

    if len(res_music) == 3:
        eid_m, integ_m, _meta_m = res_music
    else:
        eid_m, integ_m = res_music

    print(f"Result: {eid_m} ({integ_m})")

    # We updated logic to give Cast/MA priority 10, TV priority 5 for music
    if integ_m in ['cast', 'music_assistant', 'sonos']:
        print("PASS: Selected Cast/MA device for music.")
    else:
        print(f"FAIL: Selected {integ_m} instead of cast/music_assistant.")

if __name__ == "__main__":
    asyncio.run(test_resolve_priority())
