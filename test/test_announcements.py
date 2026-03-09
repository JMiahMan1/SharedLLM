import pytest
from unittest.mock import AsyncMock, patch
from app.domains.announcements.logic import process_announcement

@pytest.mark.asyncio
async def test_announcement_logic_emojis():
    # Test that emojis are stripped and sounds identified
    
    # Mock resources
    AsyncMock()
    
    # We patch execute_ha_service to verify calls
    with patch("app.domains.announcements.logic.execute_ha_service", new_callable=AsyncMock) as mock_ha:
        mock_ha.return_value = {"status": "SUCCESS"}
        
        # Test 1: Simple Announcement
        # Mock target resolution for 'living_room_tv' to return a list
        with patch("app.domains.announcements.logic.smart_resolve_entity", new_callable=AsyncMock) as mock_resolve, \
             patch("app.domains.media.devices.get_device_capabilities", new_callable=AsyncMock) as mock_caps, \
             patch.dict("app.domains.announcements.logic.DEFAULT_SOUND_MAP", {"🔔": "ding.mp3"}):
            mock_resolve.return_value = ["media_player.living_room_tv"]
            mock_caps.return_value = {"has_play_media": True, "domain": "media_player"}
            
            res = await process_announcement("Dinner is ready 🔔", "living_room_tv", {"ha_token": "dummy_token"})
            
            assert res["status"] == "SUCCESS"
            
            # Verify Sound Call (🔔 -> ding.mp3)
            # Check call args. Expected: Play Media on living_room_tv with ding.mp3
            # We assume DEFAULT_SOUND_MAP has 🔔
            expected_sound = "ding.mp3"
            if expected_sound:
                 # Check that one of the calls was for the sound
                 sound_call_found = False
                 for call in mock_ha.call_args_list:
                     args, kwargs = call
                     data = args[4] # 5th arg is service_data
                     if data.get("media_content_id", "").endswith(expected_sound):
                         sound_call_found = True
                         break
                 assert sound_call_found, "Sound effect was not played"
            
            # Verify TTS Call
            tts_call_found = False
            for call in mock_ha.call_args_list:
                args, kwargs = call
                data = args[4]
                # Debug print
                print(f"DEBUG CALL: domain={args[0]} service={args[1]} data={data}")
                
                # We expect media_player.play_media with a media-source URL containing encoded "Dinner is ready"
                if args[0] == "media_player" and args[1] == "play_media":
                    content_id = data.get("media_content_id", "")
                    if "media-source://tts/" in content_id and "Dinner%20is%20ready" in content_id and "%F0" not in content_id:
                        tts_call_found = True
                        break
            assert tts_call_found, "TTS message was not played or emojis not stripped"

@pytest.mark.asyncio
async def test_announcement_scheduling_integration():
    # Test that the handler correctly routes "in 5 minutes" to timer_add
    
    # We test the handler function which we appended to handlers.py
    # Since we can't easily import it without loading the whole app context, 
    # we simulate the logic here or try to import if possible.
    
    from app.logic.execution.handlers import handle_announce
    
    # Patch the function where it is DEFINED, not where it is used (since it is a local import in the handler)
    # Actually, since it is a local import 'from app.logic.timer_ops import tool_timer_add', 
    # we must patch 'app.logic.timer_ops.tool_timer_add' for the import to pick up the mock.
    with patch("app.logic.timer_ops.tool_timer_add", new_callable=AsyncMock) as mock_timer_add:
        mock_timer_add.return_value = {"status": "SUCCESS", "message": "Timer set"}
        
        await handle_announce("Announce hello in 5 minutes", {}, None)
        
        # Verify timer_add called with correct params
        mock_timer_add.assert_called_once()
        _, kwargs = mock_timer_add.call_args_list[0]
        kwargs.get("params") or mock_timer_add.call_args[1].get("params") # check different ways args are passed
        
        # Handle positional vs keyword args depending on implementation
        # our implementation uses positional + keyword params
        # await tool_timer_add(query, ..., params={...})
        
        call_kwargs = mock_timer_add.call_args.kwargs
        call_params = call_kwargs.get("params")
        
        assert call_params is not None
        assert call_params["timer_type"] == "announcement"
        # The regex in handlers.py should now clean "in 5 minutes"
        # If the input was "Announce hello in 5 minutes", clean logic is:
        # 1. duration_match = re.search(...) -> found "5 minutes"
        # 2. split by "in 5 minutes" -> parts=["announce hello", ...]
        # 3. clean_msg = "announce hello" -> but `message` extraction was initially naive `match.group(1)` -> "hello in 5 minutes"
        # So logic inside `handlers.py` strips it.
        # Let's fix the test expectation or input to be more precise.
        # If input is "Announce hello in 5 minutes", message extracted is "hello in 5 minutes".
        # cleaning splits by "in 5" -> "hello".
        assert call_params["metadata"]["message"].strip() == "hello"

if __name__ == "__main__":
    pass
