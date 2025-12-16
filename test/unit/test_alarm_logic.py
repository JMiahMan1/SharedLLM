import asyncio
import sys
import os
from unittest.mock import MagicMock, patch

# Mocking dependencies for test environment
from unittest.mock import MagicMock
sys.modules['fastapi'] = MagicMock()
sys.modules['fastapi.middleware.cors'] = MagicMock()
sys.modules['fastapi.responses'] = MagicMock()
sys.modules['pydantic'] = MagicMock()
sys.modules['uvicorn'] = MagicMock()
sys.modules['dateparser'] = MagicMock()

# Add app to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../app')))

from logic.alarm_audio import audio_manager
from logic.media_ops import smart_resolve_entity, handle_media_command
from settings import GlobalResources

class MockRedis:
    def __init__(self):
        self.data = {}
    def setex(self, key, time, value):
        self.data[key] = value
    def get(self, key):
        val = self.data.get(key)
        if val: return val.encode('utf-8') # Redis returns bytes
        return None

async def test_alarm_logic():
    print("--- Testing Alarm Logic ---")
    mock_redis = MockRedis()
    GlobalResources.redis_client = mock_redis
    
    # Mocking media_ops helpers
    with patch('logic.alarm_audio.get_active_media_players', return_value=[]), \
         patch('logic.alarm_audio.get_available_media_players', return_value=['media_player.kitchen', 'media_player.bedroom']), \
         patch('logic.alarm_audio.execute_ha_service') as mock_exec:
        
        mock_exec.return_value = {"status": "SUCCESS"}

        # SCENARIO 1: No last entity, no origin, no target. Should FAIL (no longer broadcast all)
        # Note: audio_manager.play_alarm_sequence is async
        print("1. Testing Fallback (No Target) - Should Log Error, NOT Broadcast")
        timer = {"id": "1", "title": "Test Alarm", "origin_device": None, "target_device": None}
        await audio_manager.play_alarm_sequence(timer, {"user": "test"}, mock_redis)
        
        # Verify execute_ha_service was NOT called
        assert mock_exec.call_count == 0, f"FAIL: Services called {mock_exec.call_count} times: {mock_exec.call_args_list}"
        print("PASS: No broadcast sent.")

        # SCENARIO 2: Last Entity Set
        print("\n2. Testing Last Entity Preference")
        mock_redis.setex("rag:last_entity:test", 86400, "media_player.living_room")
        
        # Reset mock
        mock_exec.reset_mock()
        
        # SCENARIO 2a: Last Entity Only (No Origin)
        await audio_manager.play_alarm_sequence(timer, {"user": "test"}, mock_redis)
        
        if mock_exec.call_count > 0:
            args, _ = mock_exec.call_args
            target = args[2]
            assert target == "media_player.living_room", f"FAIL: Targeted '{target}' instead of 'media_player.living_room'"
            print(f"PASS: Targeted last entity '{target}'")
        else:
            raise AssertionError("FAIL: No service called.")

        # SCENARIO 2b: Origin vs Last Entity (Origin Should Win)
        print("\n2b. Testing Origin > Last Entity Preference")
        mock_exec.reset_mock()
        timer_with_origin = {"id": "2", "title": "Origin Timer", "origin_device": "media_player.kitchen", "target_device": None}
        
        await audio_manager.play_alarm_sequence(timer_with_origin, {"user": "test"}, mock_redis)
        
        if mock_exec.call_count > 0:
            args, _ = mock_exec.call_args
            target = args[2]
            assert target == "media_player.kitchen", f"FAIL: Targeted '{target}' instead of 'media_player.kitchen' (Origin)"
            print(f"PASS: Targeted Origin '{target}' over Last Entity")
        else:
            raise AssertionError("FAIL: No service called.")

    print("\n--- Testing Media Ops Logic ---")
    
    # Test smart_resolve_entity strictness for "Turn On"
    # We need to mock ha_collection search results
    mock_collection = MagicMock()
    # Mock return: [Doc(metadata={'entity_id': 'remote.living_room'}), Doc(metadata={'entity_id': 'media_player.living_room'})]
    
    # We can't easily run the complex async logic of smart_resolve without more heavy mocking of 'run_blocking' and 'safe_similarity_search'
    # So we'll test handle_media_command logic for "turn_on"
    
    with patch('logic.media_ops.get_entity_state', return_value="off"), \
         patch('logic.media_ops.execute_ha_service', return_value={"status": "SUCCESS"}) as mock_ha:
             
         print("3. Testing Turn On TV Logic")
         # Case: "Turn on Living Room TV" -> Should map to media_player, NOT remote (unless nav)
         # We simulated a resolved entity 'media_player.living_room' coming from orchestrator or smart_resolve
         
         # If we pass entity_id="media_player.living_room" and intent="turn_on"
         await handle_media_command("turn_on", "turn on living room tv", "media_player.living_room", {"user": "test"}, None, mock_redis)
         
         args, _ = mock_ha.call_args
         # domain, service, entity_id
         domain, service, eid = args[0], args[1], args[2]
         
         print(f"Call: {domain}.{service} on {eid}")
         assert domain == "media_player" and service == "turn_on", f"FAIL: Used {domain}.{service}"
         print("PASS: Used media_player.turn_on")

if __name__ == "__main__":
    asyncio.run(test_alarm_logic())
