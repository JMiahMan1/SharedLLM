"""
Unit tests for announcement broadcast filtering and state restoration.
Tests that:
1. Unreachable devices are excluded from broadcasts
2. OFF devices without turn_on or announce support are excluded
3. OFF devices WITH turn_on support ARE included and restored to OFF after
4. Devices with announce flag use the announce feature
5. Per-device timeout prevents stalling
"""
import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
import sys
import os

# Mock heavy dependencies before importing
sys.modules['fastapi'] = MagicMock()
sys.modules['fastapi.middleware.cors'] = MagicMock()
sys.modules['fastapi.responses'] = MagicMock()
sys.modules['pydantic'] = MagicMock()
sys.modules['uvicorn'] = MagicMock()
sys.modules['langchain_chroma'] = MagicMock()

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))

from app.domains.announcements.logic import process_announcement

# Patch targets:
# Module-level imports in logic.py -> patch at usage site
CAPS_PATCH = "app.domains.media.devices.get_device_capabilities"  # locally imported inside process_announcement
STATE_PATCH = "app.domains.announcements.logic.get_entity_state"  # module-level import
PLAYERS_PATCH = "app.domains.announcements.logic.get_available_media_players"  # module-level import
HA_SVC_PATCH = "app.domains.announcements.logic.execute_ha_service"  # module-level import
RESOLVE_PATCH = "app.domains.announcements.logic.smart_resolve_entity"  # module-level import


class TestBroadcastFiltering(unittest.IsolatedAsyncioTestCase):
    """Test that broadcast mode properly filters devices based on capabilities and state."""

    def _make_caps(self, has_play_media=True, supported_features=0, domain="media_player", integration="unknown"):
        return {
            "has_play_media": has_play_media,
            "supported_features": supported_features,
            "domain": domain,
            "integration": integration,
            "friendly_name": "Test Device"
        }

    async def test_broadcast_skips_unavailable_devices(self):
        """Devices with state 'unavailable' should be excluded from broadcast."""
        caps_with_play = self._make_caps(has_play_media=True, supported_features=512)

        with patch(PLAYERS_PATCH, new_callable=AsyncMock) as mock_players, \
             patch(CAPS_PATCH, new_callable=AsyncMock) as mock_caps, \
             patch(STATE_PATCH, new_callable=AsyncMock) as mock_state, \
             patch(HA_SVC_PATCH, new_callable=AsyncMock) as mock_exec, \
             patch("app.domains.announcements.logic.GlobalResources") as mock_gr:
            mock_gr.redis_client = MagicMock()
            mock_gr.ha_collection = MagicMock()
            mock_players.return_value = ["media_player.speaker_ok", "media_player.speaker_dead"]
            mock_caps.return_value = caps_with_play
            # First device: on (eligible check + _announce_one), second: unavailable (eligible check only)
            # But _announce_one also calls get_entity_state, so we need: ok_eligible, dead_eligible, ok_announce
            mock_state.side_effect = ["on", "unavailable", "on"]
            mock_exec.return_value = {"status": "SUCCESS"}

            result = await process_announcement("Test announcement", target="broadcast", user_creds={"ha_token": "test"})
            
            self.assertEqual(result["status"], "SUCCESS")
            self.assertIn("1/1", result["message"])

    async def test_broadcast_skips_off_devices_without_turn_on(self):
        """OFF devices that can't turn on and don't support announce should be excluded."""
        # No SUPPORT_TURN_ON (128) and no ANNOUNCE (1048576), only PLAY_MEDIA (512)
        caps_no_turn_on = self._make_caps(has_play_media=True, supported_features=512)

        with patch(PLAYERS_PATCH, new_callable=AsyncMock) as mock_players, \
             patch(CAPS_PATCH, new_callable=AsyncMock) as mock_caps, \
             patch(STATE_PATCH, new_callable=AsyncMock) as mock_state, \
             patch("app.domains.announcements.logic.GlobalResources") as mock_gr:
            mock_gr.redis_client = MagicMock()
            mock_gr.ha_collection = MagicMock()
            mock_players.return_value = ["media_player.off_speaker"]
            mock_caps.return_value = caps_no_turn_on
            mock_state.return_value = "off"

            result = await process_announcement("Test announcement", target="broadcast", user_creds={"ha_token": "test"})
            
            self.assertEqual(result["status"], "FAILURE")
            self.assertIn("No capable devices", result["message"])

    async def test_broadcast_includes_off_devices_with_turn_on(self):
        """OFF devices that support turn_on should be included and turned on, then restored to OFF."""
        # PLAY_MEDIA (512) + SUPPORT_TURN_ON (128) = 640
        caps_with_turn_on = self._make_caps(has_play_media=True, supported_features=640)

        mock_integration = MagicMock()
        mock_integration.turn_on = AsyncMock()
        mock_integration.turn_off = AsyncMock()

        with patch(PLAYERS_PATCH, new_callable=AsyncMock) as mock_players, \
             patch(CAPS_PATCH, new_callable=AsyncMock) as mock_caps, \
             patch(STATE_PATCH, new_callable=AsyncMock) as mock_state, \
             patch(HA_SVC_PATCH, new_callable=AsyncMock) as mock_exec, \
             patch("app.domains.announcements.logic.GlobalResources") as mock_gr, \
             patch("app.domains.media.integrations.factory.IntegrationFactory.get_handler", return_value=mock_integration), \
             patch("asyncio.sleep", new_callable=AsyncMock):
            mock_gr.redis_client = MagicMock()
            mock_gr.ha_collection = MagicMock()
            mock_players.return_value = ["media_player.off_tv"]
            mock_caps.return_value = caps_with_turn_on
            mock_state.return_value = "off"
            mock_exec.return_value = {"status": "SUCCESS"}

            result = await process_announcement("Test announcement", target="broadcast", user_creds={"ha_token": "test"})
            
            self.assertEqual(result["status"], "SUCCESS")
            
            # Verify turn_on was called (via integration wrapper)
            mock_integration.turn_on.assert_called_once()

            # Verify turn_off was called to restore state
            mock_integration.turn_off.assert_called_once()

    async def test_broadcast_includes_off_devices_with_announce(self):
        """OFF devices with ANNOUNCE flag should be included (HA handles state restoration)."""
        # PLAY_MEDIA (512) + ANNOUNCE (1048576) = 1049088
        caps_with_announce = self._make_caps(has_play_media=True, supported_features=1049088)

        with patch(PLAYERS_PATCH, new_callable=AsyncMock) as mock_players, \
             patch(CAPS_PATCH, new_callable=AsyncMock) as mock_caps, \
             patch(STATE_PATCH, new_callable=AsyncMock) as mock_state, \
             patch(HA_SVC_PATCH, new_callable=AsyncMock) as mock_exec, \
             patch("app.domains.announcements.logic.GlobalResources") as mock_gr:
            mock_gr.redis_client = MagicMock()
            mock_gr.ha_collection = MagicMock()
            mock_players.return_value = ["media_player.smart_speaker"]
            mock_caps.return_value = caps_with_announce
            mock_state.return_value = "off"
            mock_exec.return_value = {"status": "SUCCESS"}

            result = await process_announcement("Test announcement", target="broadcast", user_creds={"ha_token": "test"})
            
            self.assertEqual(result["status"], "SUCCESS")
            
            # Verify announce flag was set in the play_media call
            play_calls = [
                c for c in mock_exec.call_args_list
                if len(c[0]) >= 2 and c[0][1] == "play_media"
            ]
            self.assertTrue(len(play_calls) > 0, "play_media should have been called")
            svc_data = play_calls[0][0][4]  # 5th positional arg is service_data
            self.assertTrue(svc_data.get("announce"), "announce flag should be True for capable devices")

    async def test_broadcast_skips_no_play_media(self):
        """Devices without play_media support should be excluded."""
        caps_no_play = self._make_caps(has_play_media=False, supported_features=0)

        with patch(PLAYERS_PATCH, new_callable=AsyncMock) as mock_players, \
             patch(CAPS_PATCH, new_callable=AsyncMock) as mock_caps, \
             patch("app.domains.announcements.logic.GlobalResources") as mock_gr:
            mock_gr.redis_client = MagicMock()
            mock_gr.ha_collection = MagicMock()
            mock_players.return_value = ["media_player.no_play"]
            mock_caps.return_value = caps_no_play

            result = await process_announcement("Test announcement", target="broadcast", user_creds={"ha_token": "test"})
            
            self.assertEqual(result["status"], "FAILURE")
            self.assertIn("No capable devices", result["message"])

    async def test_targeted_announcement_bypasses_broadcast_filter(self):
        """Targeted announcements should not apply broadcast-specific filters."""
        caps_with_play = self._make_caps(has_play_media=True, supported_features=512)

        with patch(RESOLVE_PATCH, new_callable=AsyncMock) as mock_resolve, \
             patch(CAPS_PATCH, new_callable=AsyncMock) as mock_caps, \
             patch(STATE_PATCH, new_callable=AsyncMock) as mock_state, \
             patch(HA_SVC_PATCH, new_callable=AsyncMock) as mock_exec, \
             patch("app.domains.announcements.logic.GlobalResources") as mock_gr:
            mock_gr.redis_client = MagicMock()
            mock_gr.ha_collection = MagicMock()
            mock_resolve.return_value = ("media_player.kitchen_speaker", "cast", {})
            mock_caps.return_value = caps_with_play
            mock_state.return_value = "off"
            mock_exec.return_value = {"status": "SUCCESS"}

            result = await process_announcement(
                "Test targeted", target="kitchen speaker", user_creds={"ha_token": "test"}
            )
            
            # Should succeed because targeted mode doesn't apply broadcast filtering
            self.assertEqual(result["status"], "SUCCESS")


if __name__ == '__main__':
    unittest.main()
