import asyncio
import logging
from unittest.mock import MagicMock, patch

# pyright: ignore[reportMissingImports]
from app.domains.media.integrations.standard import StandardIntegration  # pyright: ignore[reportMissingImports]

# Configure logging
logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

async def test_video_auto_search():
    """
    Verifies that StandardIntegration searches for a URL when given a natural language video query.
    """
    print("\n--- Testing Video Auto-Search Logic ---")

    integration = StandardIntegration()
    user_creds = {"user": "test_user"}
    entity_id = "media_player.living_room_tv"

    # CASE 1: Natural Language Query (Should Trigger Search)
    query = "fireplace video"
    media_type = "video"

    print(f"Input: Query='{query}', Type='{media_type}'")

    # Mock the web search tool to return a fake YouTube result
    with patch("app.logic.web_search.tool_web_search", new_callable=MagicMock) as mock_search:
        # Simulate Whoogle output containing a URL
        mock_search.return_value = "Here is a result: URL: https://www.youtube.com/watch?v=123456"

        # Mock execute_ha_service to capture the output
        with patch("app.domains.media.integrations.standard.execute_ha_service", new_callable=MagicMock) as mock_ha:

            await integration.play_media(entity_id, query, media_type, user_creds)

            # Verification
            mock_search.assert_called_once()
            call_args = mock_search.call_args[0][0]
            print(f"Search Tool Called With: '{call_args}'")

            if "fireplace video youtube" in call_args:
                print("✅ PASS: 'youtube' keyword passed to search tool.")
            else:
                print("❌ FAIL: Search query missing 'youtube' keyword.")

            # Check what was sent to HA
            if mock_ha.called:
                service_data = mock_ha.call_args[0][4]
                content_id = service_data.get("media_content_id")
                print(f"Final Media Content ID: '{content_id}'")

                if content_id == "https://www.youtube.com/watch?v=123456":
                     print("✅ PASS: URL extracted and sent to device.")
                else:
                     print(f"❌ FAIL: Expected URL, got '{content_id}'")
            else:
                print("❌ FAIL: HA Service not called.")

    # CASE 2: Direct URL (Should Skip Search)
    print("\n--- Testing Direct URL Passthrough ---")
    direct_url = "https://www.youtube.com/watch?v=DIRECT"

    with patch("app.logic.web_search.tool_web_search", new_callable=MagicMock) as mock_search:
         with patch("app.domains.media.integrations.standard.execute_ha_service", new_callable=MagicMock) as mock_ha:
            await integration.play_media(entity_id, direct_url, "video", user_creds)

            if not mock_search.called:
                print("✅ PASS: Search skipped for direct URL.")
            else:
                print("❌ FAIL: Search triggered for direct URL!")

if __name__ == "__main__":
    asyncio.run(test_video_auto_search())
