import unittest
from unittest.mock import MagicMock, patch, AsyncMock
import sys

# Mocking dependencies for test environment
sys.modules['fastapi'] = MagicMock()
sys.modules['pydantic'] = MagicMock()
sys.modules['uvicorn'] = MagicMock()
sys.modules['langchain_chroma'] = MagicMock()
sys.modules['settings'] = MagicMock() # Often imported by media_ops

import os
# Adjust path to import app modules
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../app')))

from logic.media_ops import smart_resolve_entity

# Mock generic document object from LangChain/Chroma
class MockDoc:
    def __init__(self, entity_id, integration):
        self.metadata = {"entity_id": entity_id, "integration": integration}
        self.page_content = entity_id

class TestEntityResolution(unittest.IsolatedAsyncioTestCase):

    async def test_office_tv_routing(self):
        """
        Scenario: User has 'Office TV' which appears as:
        1. media_player.office_tv_mass (Music Assistant)
        2. media_player.office_tv_android (Android TV)
        
        Intent: 'turn_off' -> Should pick Android TV (Hardware)
        Intent: 'play_media' (music) -> Should pick Mass (Music Service)
        """
        
        # Mock Collection Search Results
        # Simulating that searching 'Office TV' returns both candidates
        mock_results = [
            MockDoc("media_player.office_tv_mass", "music_assistant"),
            MockDoc("media_player.office_tv_android", "androidtv"),
            MockDoc("light.office_lights", "hue") # Noise
        ]
        
        mock_collection = MagicMock()
        
        # Configure mocked settings.run_blocking to be AsyncMock
        # smart_resolve_entity does a local import: 'from settings import run_blocking'
        sys.modules['settings'].run_blocking = AsyncMock(return_value=mock_results)
        
        # TEST 1: Turn Off (Power)
        # Should prefer the Android TV integration because it controls the actual hardware power
        eid, integration = await smart_resolve_entity("Office TV", "turn_off", mock_collection)
        print(f"\n[Turn Off] Resolved: {eid} ({integration})")
        
        # CURRENTLY: This might fail or be flaky depending on list order without the fix.
        # We want to assertion to be Android TV.
        self.assertEqual(eid, "media_player.office_tv_android")
        self.assertEqual(integration, "androidtv")

        # TEST 2: Play Music
        # Should prefer Music Assistant
        eid_music, int_music = await smart_resolve_entity("Office TV", "play_media", mock_collection, is_music=True)
        print(f"[Play Music] Resolved: {eid_music} ({int_music})")
        
        self.assertEqual(eid_music, "media_player.office_tv_mass")
        self.assertEqual(int_music, "music_assistant")

if __name__ == '__main__':
    unittest.main()
