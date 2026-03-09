import unittest
from unittest.mock import MagicMock, patch, AsyncMock
import sys
import os
import json

# Adjust path to import app modules
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../app')))

from app.domains.media.devices import get_device_capabilities
from app.domains.lighting.commands import handle_light_command

class TestCapabilityDetection(unittest.IsolatedAsyncioTestCase):

    async def test_light_rgb_capable(self):
        """Test: Light with RGB support correctly detected."""
        user_creds = {"user": "test", "ha_token": "fake"}
        mock_redis = MagicMock()
        mock_redis.get.return_value = None  # Cache miss
        
        # Mock HA API response
        mock_ha_response = {
            "entity_id": "light.rgb_bulb",
            "state": "on",
            "attributes": {
                "supported_features": 17,  # Brightness (1) + Color (16)
                "supported_color_modes": ["rgb", "hs"],
                "friendly_name": "RGB Bulb"
            }
        }
        
        with patch('app.domains.media.devices.HA_URL', 'http://test'), \
             patch('app.domains.media.devices.run_blocking', new_callable=AsyncMock) as mock_run:
            
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = mock_ha_response
            mock_run.return_value = mock_response
            
            caps = await get_device_capabilities("light.rgb_bulb", user_creds, mock_redis)
            
            # Verify capabilities
            self.assertEqual(caps["domain"], "light")
            self.assertTrue(caps["has_brightness"])
            self.assertTrue(caps["has_color"])
            self.assertFalse(caps.get("has_color_temp", False))
            self.assertIn("rgb", caps["color_modes"])
            
            # Verify cache write
            mock_redis.setex.assert_called_once()
            cache_key = mock_redis.setex.call_args[0][0]
            self.assertEqual(cache_key, "capabilities:light.rgb_bulb")

    async def test_light_brightness_only(self):
        """Test: Brightness-only light (no color support)."""
        user_creds = {"user": "test", "ha_token": "fake"}
        mock_redis = None  # No Redis
        
        mock_ha_response = {
            "entity_id": "light.simple",
            "state": "on",
            "attributes": {
                "supported_features": 1,  # Brightness only
                "supported_color_modes": ["brightness"],
                "friendly_name": "Simple Bulb"
            }
        }
        
        with patch('app.domains.media.devices.HA_URL', 'http://test'), \
             patch('app.domains.media.devices.run_blocking', new_callable=AsyncMock) as mock_run:
            
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = mock_ha_response
            mock_run.return_value = mock_response
            
            caps = await get_device_capabilities("light.simple", user_creds, mock_redis)
            
            self.assertTrue(caps["has_brightness"])
            self.assertFalse(caps["has_color"])
            self.assertEqual(caps["color_modes"], ["brightness"])

    async def test_light_color_temp_only(self):
        """Test: Light with only color temperature support."""
        user_creds = {"user": "test", "ha_token": "fake"}
        mock_redis = None
        
        mock_ha_response = {
            "entity_id": "light.warm_white",
            "state": "on",
            "attributes": {
                "supported_features": 3,  # Brightness (1) + Color Temp (2)
                "supported_color_modes": ["color_temp"],
                "friendly_name": "Warm White Bulb"
            }
        }
        
        with patch('app.domains.media.devices.HA_URL', 'http://test'), \
             patch('app.domains.media.devices.run_blocking', new_callable=AsyncMock) as mock_run:
            
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = mock_ha_response
            mock_run.return_value = mock_response
            
            caps = await get_device_capabilities("light.warm_white", user_creds, mock_redis)
            
            self.assertTrue(caps["has_brightness"])
            self.assertTrue(caps["has_color_temp"])
            self.assertFalse(caps["has_color"])  # Only color_temp, not full color

    async def test_media_player_capabilities(self):
        """Test: Media player with skip/volume support."""
        user_creds = {"user": "test", "ha_token": "fake"}
        mock_redis = None
        
        mock_ha_response = {
            "entity_id": "media_player.spotify",
            "state": "playing",
            "attributes": {
                "supported_features": 549,  # Pause(1) + Volume(4) + Next(32) + Play Media(512)
                "friendly_name": "Spotify"
            }
        }
        
        with patch('app.domains.media.devices.HA_URL', 'http://test'), \
             patch('app.domains.media.devices.run_blocking', new_callable=AsyncMock) as mock_run:
            
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = mock_ha_response
            mock_run.return_value = mock_response
            
            caps = await get_device_capabilities("media_player.spotify", user_creds, mock_redis)
            
            self.assertEqual(caps["domain"], "media_player")
            self.assertTrue(caps["has_pause"])
            self.assertTrue(caps["has_next"])
            self.assertTrue(caps["has_volume"])
            self.assertTrue(caps["has_play_media"])
            self.assertFalse(caps.get("has_previous", False))  # 16 not in bitmask

    async def test_cache_hit(self):
        """Test: Capabilities loaded from Redis cache."""
        user_creds = {"user": "test", "ha_token": "fake"}
        mock_redis = MagicMock()
        
        cached_data = json.dumps({
            "domain": "light",
            "has_brightness": True,
            "has_color": True,
            "friendly_name": "Cached Light"
        })
        mock_redis.get.return_value = cached_data.encode('utf-8')
        
        with patch('app.domains.media.devices.HA_URL', 'http://test'), \
             patch('app.domains.media.devices.run_blocking', new_callable=AsyncMock) as mock_run:
            
            caps = await get_device_capabilities("light.cached", user_creds, mock_redis)
            
            # Verify cache was used (no API call)
            mock_run.assert_not_called()
            
            # Verify data from cache
            self.assertEqual(caps["friendly_name"], "Cached Light")
            self.assertTrue(caps["has_color"])

    async def test_color_validation_failure(self):
        """Test: Setting color on brightness-only light returns helpful error."""
        user_creds = {"user": "test", "ha_token": "fake"}
        mock_redis = None
        entity_id = "light.simple"
        
        # Mock capabilities response (brightness only)
        with patch('app.domains.media.get_device_capabilities', new_callable=AsyncMock) as mock_caps:
            mock_caps.return_value = {
                "domain": "light",
                "has_brightness": True,
                "has_color": False,
                "has_color_temp": False,
                "friendly_name": "Simple Bulb"
            }
            
            result_list = await handle_light_command(
                intent="set_color",
                query="set simple bulb to blue",
                entity_id=entity_id,
                user_creds=user_creds,
                redis_client=mock_redis
            )
            
            result = result_list[0] if isinstance(result_list, list) else result_list
    
            self.assertEqual(result["status"], "FAILURE")
            self.assertIn("doesn't support color control", result["message"])
            self.assertIn("Simple Bulb", result["message"])

    async def test_brightness_validation_failure(self):
        """Test: Setting brightness on on/off only light returns error."""
        user_creds = {"user": "test", "ha_token": "fake"}
        mock_redis = None
        entity_id = "light.on_off_only"
        
        with patch('app.domains.media.get_device_capabilities', new_callable=AsyncMock) as mock_caps:
            mock_caps.return_value = {
                "domain": "light",
                "has_brightness": False,
                "friendly_name": "On/Off Light"
            }
            
            result_list = await handle_light_command(
                intent="set_brightness",
                query="set on/off light to 50%",
                entity_id=entity_id,
                user_creds=user_creds,
                redis_client=mock_redis
            )
    
            result = result_list[0] if isinstance(result_list, list) else result_list
            self.assertEqual(result["status"], "FAILURE")
            self.assertIn("on/off only", result["message"])

if __name__ == '__main__':
    unittest.main()
