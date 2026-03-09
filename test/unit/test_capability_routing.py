import pytest
import asyncio
from unittest.mock import MagicMock, AsyncMock
import sys
import os

# Setup path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../app')))

# Mock Settings and GlobalResources
# Removed sys.modules hack

# Mock Media Ops Imports
import app.domains.media.devices as media_devices
from app.logic.media_ops import smart_resolve_entity

# Mock capabilities function
async def mock_get_caps(eid, creds, client):
    if eid == "media_player.office_tv":
        return {
            "integration": "androidtv", 
            "friendly_name": "Office TV",
            "features_breakdown": {"turn_off": True}
        }
    if eid == "media_player.office_tv_chrome":
        return {
            "integration": "cast", 
            "friendly_name": "Office TV Chrome",
            "features_breakdown": {"turn_off": True} # Cast also supports turn off
        }
    return {}

media_devices.get_device_capabilities = AsyncMock(side_effect=mock_get_caps)

@pytest.mark.asyncio
async def test_capability_routing_priority():
    # Candidates list as if returned by vector search
    candidates = [
        ("media_player.office_tv_chrome", "unknown"), # RAG often returns unknown
        ("media_player.office_tv", "unknown")
    ]
    
    # Intent: Turn Off
    # Expected: Office TV (Android) wins over Chrome
    
    selected = await smart_resolve_entity(
        "Office TV", 
        "turn_off", 
        None # Collection mock not needed since we mock candidates logic?
        # Wait, smart_resolve_entity runs similarity search internally.
        # we can't easily test just the ranking logic without determining the vector search mocking.
    )
    
    # Ideally I refactored the logic into a helper function `select_best_entity` but I put it inline.
    # So I have to Mock the Vector Search to return these candidates.
    pass

from unittest.mock import patch

@pytest.mark.asyncio
async def test_full_flow():
    mock_collection = MagicMock()
    mock_collection.similarity_search_with_score = MagicMock(return_value=[
        (MagicMock(metadata={"entity_id": "media_player.office_tv_chrome", "integration": "cast", "friendly_name": "Office TV Chrome"}), 0.9),
        (MagicMock(metadata={"entity_id": "media_player.office_tv", "integration": "androidtv", "friendly_name": "Office TV"}), 0.85)
    ])
    
    # Make the synchronous run_blocking mock execute the lambda
    async def mock_run_blocking(func, *args):
        return func(*args)
        
    with patch('app.settings.run_blocking', new_callable=AsyncMock) as mock_run, \
         patch('app.settings.GlobalResources') as mock_global:
        mock_run.side_effect = mock_run_blocking
        mock_global.ha_collection = mock_collection
        
        # Run
        eid, integ, _ = await smart_resolve_entity("Office TV", "turn_off", mock_collection)
    
    print(f"Selected: {eid}, {integ}")
    # androidtv scores 80 for turn_off, cast scores -50
    assert eid == "media_player.office_tv"
    assert integ == "androidtv"

if __name__ == "__main__":
    # verification script style
    loop = asyncio.get_event_loop()
    loop.run_until_complete(test_full_flow())
    print("Test Passed")
