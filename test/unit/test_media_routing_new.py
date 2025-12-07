import unittest
from unittest.mock import MagicMock, patch, AsyncMock
import sys
import os
import asyncio

# Adjust path to import app modules
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../app')))

# --- MOCK SETTINGS MODULE BEFORE IMPORTING MEDIA_OPS ---
# This prevents loading 'settings.py' which might hang due to environment issues or heavy imports
mock_settings = MagicMock()
mock_settings.run_blocking = AsyncMock() 
async def mock_run_blocking(func, *args):
    if callable(func):
        return func(*args)
    return func
mock_settings.run_blocking.side_effect = mock_run_blocking
mock_settings.HA_URL = "http://mock-ha"
mock_settings.DEFAULT_MODEL = "mock-model"
mock_settings.GlobalResources = MagicMock()

sys.modules['settings'] = mock_settings
# -------------------------------------------------------

from logic.media_ops import smart_resolve_entity, handle_media_command

# Mock generic document object from LangChain/Chroma
class MockDoc:
    def __init__(self, entity_id, integration):
        self.metadata = {"entity_id": entity_id, "integration": integration}
        self.page_content = entity_id

class TestMediaRoutingNew(unittest.IsolatedAsyncioTestCase):

    async def test_audio_first_priority(self):
        """
        Scenario: "Play Brandon Lake"
        Entities:
         - media_player.office_tv_android (Android TV)
         - media_player.office_tv_mass (Music Assistant)
        
        Expected: Should pick Music Assistant entity because it is a playback request.
        """
        mock_results = [
            MockDoc("media_player.office_tv_android", "androidtv"),
            MockDoc("media_player.office_tv_mass", "music_assistant"),
        ]
        
        mock_collection = MagicMock()
        with patch('logic.media_ops.safe_similarity_search', return_value=mock_results):
             # Direct smart_resolve call with is_music=True (Simulating logic in handle_media_command)
             eid, integration = await smart_resolve_entity("Office TV", "play_media", mock_collection, is_music=True)
             
             self.assertEqual(eid, "media_player.office_tv_mass")
             self.assertEqual(integration, "music_assistant")

    async def test_hardware_control_priority(self):
        """
        Scenario: "Turn on Office TV"
        Entities:
         - media_player.office_tv_android (Android TV)
         - media_player.office_tv_mass (Music Assistant)
        
        Expected: Should pick Android TV because it is a power command.
        """
        mock_results = [
            MockDoc("media_player.office_tv_android", "androidtv"),
            MockDoc("media_player.office_tv_mass", "music_assistant"),
        ]
        
        mock_collection = MagicMock()
        with patch('logic.media_ops.safe_similarity_search', return_value=mock_results):
            eid, integration = await smart_resolve_entity("Office TV", "turn_on", mock_collection)
            
            self.assertEqual(eid, "media_player.office_tv_android")
            self.assertEqual(integration, "androidtv")

    async def test_video_exception_routing(self):
        """
        Scenario: "Watch YouTube on Office TV"
        Entities: Same
        
        Expected: Should pick Android TV because "YouTube" implies video/app launch.
        """
        mock_results = [
            MockDoc("media_player.office_tv_mass", "music_assistant"),
            MockDoc("media_player.office_tv_android", "androidtv")
        ]
        
        mock_collection = MagicMock()
        with patch('logic.media_ops.safe_similarity_search', return_value=mock_results):
            # Passing is_video=True to verify fix
            eid, integration = await smart_resolve_entity("Office TV", "play_media", mock_collection, is_music=False, is_video=True)
            
            self.assertEqual(eid, "media_player.office_tv_android")

    async def test_audiobook_routing(self):
        """
        Scenario: "Read The Martian"
        Entities: 
         - media_player.office_speaker_mass
        """
        # Placeholder for future expansion
        pass

if __name__ == '__main__':
    unittest.main()
