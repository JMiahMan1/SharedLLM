import unittest
from unittest.mock import MagicMock, patch, AsyncMock
import sys
import os

# Adjust path to import app modules
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../app')))

from logic.media_ops import handle_media_command, COLOR_MAP

class TestLightControl(unittest.IsolatedAsyncioTestCase):

    async def test_set_color_red(self):
        """Test: 'Set Piano Lamp to red' should generate rgb_color service data."""
        mock_collection = MagicMock()
        mock_redis = None
        user_creds = {"user": "test", "ha_token": "fake", "ha_url": "http://test"}
        
        # Mock entity resolution (entity_id passed directly for simplicity)
        entity_id = "light.piano_lamp"
        
        with patch('logic.media_ops.execute_ha_service', new_callable=AsyncMock) as mock_exec, \
             patch('logic.media_ops.get_device_capabilities', new_callable=AsyncMock) as mock_caps:
            mock_exec.return_value = {"status": "SUCCESS", "message": "Color set"}
            mock_caps.return_value = {"domain": "light", "has_brightness": True, "has_color": True, "color_modes": ["rgb"], "friendly_name": "Piano Lamp"}
            
            result_list = await handle_media_command(
                intent="set_color",
                query="set piano lamp to red",
                entity_id=entity_id,
                user_creds=user_creds,
                ha_collection=mock_collection,
                redis_client=mock_redis
            )
            
            result = result_list[0] if isinstance(result_list, list) else result_list
            
            # Verify execute_ha_service was called with correct RGB
            mock_exec.assert_called_once()
            call_args = mock_exec.call_args
            self.assertEqual(call_args[0][0], "light")  # domain
            self.assertEqual(call_args[0][1], "turn_on")  # service
            self.assertEqual(call_args[0][2], entity_id)
            self.assertEqual(call_args[0][4]["rgb_color"], COLOR_MAP["red"])

    async def test_set_brightness_percentage(self):
        """Test: 'Set Piano Lamp to 50%' should set brightness to 127."""
        mock_collection = MagicMock()
        mock_redis = None
        user_creds = {"user": "test", "ha_token": "fake"}
        entity_id = "light.piano_lamp"
        
        with patch('logic.media_ops.execute_ha_service', new_callable=AsyncMock) as mock_exec, \
             patch('logic.media_ops.get_device_capabilities', new_callable=AsyncMock) as mock_caps:
            mock_exec.return_value = {"status": "SUCCESS"}
            mock_caps.return_value = {"domain": "light", "has_brightness": True, "has_color": True, "friendly_name": "Piano Lamp"}
    
            result_list = await handle_media_command(
                intent="set_brightness",
                query="set piano lamp to 50%",
                entity_id=entity_id,
                user_creds=user_creds,
                ha_collection=mock_collection,
                redis_client=mock_redis
            )
            
            result = result_list[0] if isinstance(result_list, list) else result_list
    
            # Verify brightness calculation (50% of 255 = 127.5 → 127)
            call_args = mock_exec.call_args
            service_data = call_args[0][4]
            brightness = service_data["brightness"]
            self.assertEqual(brightness, 127)

    async def test_dim_command(self):
        """Test: 'Dim the lights' should set brightness to ~30%."""
        mock_collection = MagicMock()
        mock_redis = None
        user_creds = {"user": "test", "ha_token": "fake"}
        entity_id = "light.piano_lamp"
        
        with patch('logic.media_ops.execute_ha_service', new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = {"status": "SUCCESS"}
            with patch('logic.media_ops.get_device_capabilities', new_callable=AsyncMock) as mock_caps:
                mock_caps.return_value = {"domain": "light", "has_brightness": True, "has_color": True, "friendly_name": "Piano Lamp"}
    
                result_list = await handle_media_command(
                    intent="dim",
                    query="dim the piano lamp",
                    entity_id=entity_id,
                    user_creds=user_creds,
                    ha_collection=mock_collection,
                    redis_client=mock_redis
                )
                
                result = result_list[0] if isinstance(result_list, list) else result_list
    
                # Verify dim sets brightness to 70 (~30%)
                call_args = mock_exec.call_args
                service_data = call_args[0][4]
                brightness = service_data["brightness"]
                self.assertEqual(brightness, 70)

    async def test_brighten_command(self):
        """Test: 'Brighten the lights' should set brightness to max (255)."""
        mock_collection = MagicMock()
        mock_redis = None
        user_creds = {"user": "test", "ha_token": "fake"}
        entity_id = "light.piano_lamp"
        
        with patch('logic.media_ops.execute_ha_service', new_callable=AsyncMock) as mock_exec, \
             patch('logic.media_ops.get_device_capabilities', new_callable=AsyncMock) as mock_caps:
            
            mock_exec.return_value = {"status": "SUCCESS"}
            mock_caps.return_value = {"domain": "light", "has_brightness": True, "has_color": True, "color_modes": ["rgb"], "friendly_name": "Piano Lamp"}

            result_list = await handle_media_command(
                intent="brighten",
                query="brighten the piano lamp",
                entity_id=entity_id,
                user_creds=user_creds,
                ha_collection=mock_collection,
                redis_client=mock_redis
            )
            
            # Handle list return
            result = result_list[0] if isinstance(result_list, list) else result_list

            # Verify brighten sets to max
            call_args = mock_exec.call_args
            # Args: domain, service, entity_id, user_creds, service_data, redis
            # service_data is index 4
            service_data = call_args[0][4]
            brightness = service_data["brightness"]
            self.assertEqual(brightness, 255)

    async def test_color_on_non_light_fails(self):
        """Test: Setting color on a non-light device should fail gracefully."""
        mock_collection = MagicMock()
        mock_redis = None
        user_creds = {"user": "test", "ha_token": "fake"}
        entity_id = "switch.office_fan"  # Not a light
        
        with patch('logic.media_ops.get_device_capabilities', new_callable=AsyncMock) as mock_caps:
            mock_caps.return_value = {"domain": "switch", "friendly_name": "Office Fan"}
            
            result_list = await handle_media_command(
                intent="set_color",
                query="set office fan to red",
                entity_id=entity_id,
                user_creds=user_creds,
                ha_collection=mock_collection,
                redis_client=mock_redis
            )
            
            result = result_list[0] if isinstance(result_list, list) else result_list
        
            # Should return failure
            self.assertEqual(result["status"], "FAILURE")
            self.assertIn("only works with lights", result["message"])

if __name__ == '__main__':
    unittest.main()
