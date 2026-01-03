
import asyncio
import logging
import sys
from unittest.mock import MagicMock, patch

# Adjust path
sys.path.append("/home/jeremiah/Summers Drive/Code/SharedLLM")

from app.domains.media.integrations.media_assistant_roku import RokuMediaAssistantIntegration

# Configure Logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
log = logging.getLogger("TestRokuFix")

async def test_roku_play_music_resolution():
    """
    Verify that a simple 'play' command triggers a search and populates metadata
    """
    log.info("--- Starting Roku Play Music Resolution Test ---")
    
    # Mock Credentials
    user_creds = {"ha_token": "test_token", "ha_url": "http://ha.local"}
    
    # Mock Dependencies
    mock_search_result = {
        "status": "SUCCESS",
        "results": [
            {
                "title": "Shape of You",
                "artist": "Ed Sheeran",
                "album": "Divide",
                "image_url": "http://img.url/cover.jpg",
                "media_content_id": "spotify://track/12345",
                "match_score": 0.95
            }
        ]
    }
    
    with patch("app.domains.media.integrations.media_assistant_roku.requests.post") as mock_post, \
         patch("app.logic.music_assistant_ops.tool_music_search", return_value=mock_search_result) as mock_search, \
         patch("app.domains.media.integrations.media_assistant_roku.RokuMediaAssistantIntegration.get_state") as mock_state, \
         patch("app.domains.media.integrations.media_assistant_roku.execute_ha_service") as mock_ha_service:

        # Mock State (On/Idle)
        mock_state.return_value = MagicMock(state="idle", attributes={"friendly_name": "Living Room TV"})
        
        # Mock Discovery
        with patch.object(RokuMediaAssistantIntegration, "_get_roku_ip", return_value="192.168.1.100"):
            
            integration = RokuMediaAssistantIntegration()
            
            # Test Case: Simple Query "Play Shape of You" (No metadata provided)
            query = "Shape of You"
            entity_id = "media_player.living_room_tv"
            
            log.info(f"Testing play_media with query: '{query}' (No metadata)")
            
            result = await integration.play_media(entity_id, query, "music", user_creds)
            
            # Verifications
            mock_search.assert_called()
            log.info("Search was called as expected.")
            
            # Check ECP Post params
            # call_args[1] is kwargs, params should be there
            call_args = mock_post.call_args
            if not call_args:
                log.error("ECP Post was NOT called!")
                return
                
            url = call_args[0][0]
            params = call_args[1].get("params")
            
            log.info(f"ECP URL: {url}")
            log.info(f"ECP Params: {params}")
            
            assert "782875" in url
            assert params["t"] == "a"
            assert params["songName"] == "Shape of You"
            assert params["artistName"] == "Ed Sheeran"
            assert params["albumName"] == "Divide"
            assert params["albumArt"] == "http://img.url/cover.jpg"
            assert params["u"] == "spotify://track/12345" # Should prefer URI if found
            
            log.info("SUCCESS: Parameters correctly populated from search result!")

if __name__ == "__main__":
    asyncio.run(test_roku_play_music_resolution())
