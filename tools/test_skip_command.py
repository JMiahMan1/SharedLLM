
import sys
import os
import asyncio
from unittest.mock import MagicMock, AsyncMock

sys.path.append(os.getcwd())

# Mock the integration factory and handler
sys.modules["app.domains.media.integrations.factory"] = MagicMock()
from app.domains.media.integrations.factory import IntegrationFactory
from app.domains.media.commands import _execute_transport_command

async def main():
    print("Testing 'Skip' (media_next) Command Mapping...")
    
    # Setup Mock Handler
    mock_handler = AsyncMock()
    mock_handler.next_track = AsyncMock(return_value={"status": "SUCCESS", "message": "Next track"})
    
    # Setup Factory to return our mock
    IntegrationFactory.get_handler = MagicMock(return_value=mock_handler)
    
    # Test Case 1: media_next (The alias that was failing)
    print("\nTest 1: Intent='media_next'")
    result = await _execute_transport_command(
        intent="media_next",
        entity_id="media_player.test",
        domain="media_player",
        user_creds={},
        integration="music_assistant"
    )
    
    if mock_handler.next_track.called:
        print("PASS: 'media_next' triggered handler.next_track()")
    else:
        print("FAIL: 'media_next' did NOT trigger handler.next_track()")
        exit(1)

    # Test Case 2: media_previous (The other alias)
    print("\nTest 2: Intent='media_previous'")
    mock_handler.previous_track = AsyncMock(return_value={"status": "SUCCESS", "message": "Prev track"})
    
    await _execute_transport_command(
        intent="media_previous",
        entity_id="media_player.test",
        domain="media_player",
        user_creds={},
        integration="music_assistant"
    )
    
    if mock_handler.previous_track.called:
        print("PASS: 'media_previous' triggered handler.previous_track()")
    else:
        print("FAIL: 'media_previous' did NOT trigger handler.previous_track()")
        exit(1)

    # Test Case 3: Verify RokuIntegration structure (Static check)
    print("\nTest 3: Checking RokuIntegration methods...")
    from app.domains.media.integrations.roku import RokuIntegration
    if hasattr(RokuIntegration, "next_track") and hasattr(RokuIntegration, "previous_track"):
        print("PASS: RokuIntegration has next_track and previous_track methods.")
    else:
        print("FAIL: RokuIntegration is missing transport methods!")
        exit(1)
        
    print("\nAll tests passed. The fix is verified in the codebase.")

if __name__ == "__main__":
    asyncio.run(main())
