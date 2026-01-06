import pytest
import asyncio
from unittest.mock import AsyncMock, patch, MagicMock
from app.domains.media.integrations.roku import RokuIntegration

@pytest.mark.asyncio
async def test_roku_play_media_video_direct_success():
    """
    Test that play_media correctly handles a video request via direct playback,
    using aiohttp instead of requests, and avoids blocking calls.
    """
    integration = RokuIntegration()
    
    # Mock credentials and entity
    user_creds = {"ha_token": "fake_token", "user": "test_user"}
    entity_id = "media_player.office_tv"
    
    # Mock dependencies
    with patch("app.domains.media.integrations.roku.aiohttp.ClientSession") as MockSession, \
         patch("app.domains.media.integrations.roku.asyncio.sleep", new_callable=AsyncMock) as mock_sleep, \
         patch("app.domains.media.integrations.roku.RokuIntegration._download_and_serve_video", new_callable=AsyncMock) as mock_download, \
         patch("app.domains.media.integrations.roku.RokuIntegration._get_roku_ip", new_callable=AsyncMock) as mock_get_ip, \
         patch("app.domains.media.integrations.roku.RokuIntegration.get_state", new_callable=AsyncMock) as mock_get_state, \
         patch("app.domains.media.integrations.roku.execute_ha_service", new_callable=AsyncMock) as mock_exec:

        # Setup mocks
        mock_get_state.return_value = MagicMock(state="on") # Already on
        mock_download.return_value = "http://192.168.1.100:8000/video.mp4"
        mock_get_ip.return_value = "192.168.1.50"
        
        # Mock ClientSession context manager for direct launch (status 200)
        mock_session_instance = MockSession.return_value
        mock_session_instance.__aenter__.return_value = mock_session_instance
        mock_session_instance.__aexit__.return_value = None
        
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.text.return_value = "OK"
        mock_session_instance.post.return_value.__aenter__.return_value = mock_resp
        
        # ACT
        result = await integration.play_media(
            entity_id, 
            "http://example.com/video.mp4", 
            "video", 
            user_creds, 
            friendly_name="Office TV"
        )
        
        # ASSERT
        assert result["status"] == "SUCCESS"
        assert result["service"] == "roku_direct_launch"
        
        # Verify no blocking sleep
        mock_sleep.assert_called() 
        
        # Verify async post called
        mock_session_instance.post.assert_called()
        args, kwargs = mock_session_instance.post.call_args
        assert "http://192.168.1.50:8060/launch/782875" in args[0]
        assert kwargs["params"]["u"] == "http://192.168.1.100:8000/video.mp4"

@pytest.mark.asyncio
async def test_roku_play_media_video_dlna_fallback():
    """Test fallback to DLNA/ECP if direct launch fails."""
    integration = RokuIntegration()
    user_creds = {"ha_token": "fake_token"}
    entity_id = "media_player.office_tv"

    with patch("app.domains.media.integrations.roku.aiohttp.ClientSession") as MockSession, \
         patch("app.domains.media.integrations.roku.asyncio.sleep", new_callable=AsyncMock) as mock_sleep, \
         patch("app.domains.media.integrations.roku.RokuIntegration._download_and_serve_video", new_callable=AsyncMock) as mock_download, \
         patch("app.domains.media.integrations.roku.RokuIntegration._get_roku_ip", new_callable=AsyncMock) as mock_get_ip, \
         patch("app.domains.media.integrations.roku.RokuIntegration.get_state", new_callable=AsyncMock) as mock_get_state:

        mock_get_state.return_value = MagicMock(state="on")
        mock_download.return_value = "http://local/vid.mp4"
        mock_get_ip.return_value = "192.168.1.50"
        
        mock_session = MockSession.return_value
        mock_session.__aenter__.return_value = mock_session
        
        # First POST (Direct Launch) -> Fails
        # Second POST (Launch 2213) -> Success
        
        mock_resp_fail = AsyncMock()
        mock_resp_fail.status = 404
        
        mock_resp_ok = AsyncMock()
        mock_resp_ok.status = 200
        
        # We need side_effect for post to handle multiple calls
        # 1. Launch 782875 (Fail)
        # 2. Launch 2213 (OK)
        # 3. Keypress Select...
        mock_session.post.return_value.__aenter__.side_effect = [
            mock_resp_fail, # Direct
            mock_resp_ok,   # DLNA App
            mock_resp_ok,   # Select
            mock_resp_ok,   # Select
            mock_resp_ok    # Play
        ]
        
        # Mock DLNA status check (GET)
        mock_resp_dlna = AsyncMock()
        mock_resp_dlna.status = 200
        mock_resp_dlna.json.return_value = {"last_browse_timestamp": 9999999999} # Future time
        mock_session.get.return_value.__aenter__.return_value = mock_resp_dlna

        # Mock time.time for smart wait
        with patch("time.time", return_value=1234567890):
             result = await integration.play_media(entity_id, "http://vid", "video", user_creds)
        
        assert result["status"] == "SUCCESS"
        assert result["service"] == "roku_ecp_launch"
        assert mock_session.post.call_count >= 2

