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

from app.logic.media_ops import smart_resolve_entity

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
        # Mock ChromaDB returning the three different integrations for the "Office TV"
        mock_results = [
            (MagicMock(metadata={"entity_id": "media_player.office_tv_android", "integration": "androidtv", "friendly_name": "Office TV"}), 0.9),
            (MagicMock(metadata={"entity_id": "media_player.office_tv_mass", "integration": "music_assistant", "friendly_name": "Office TV Mass"}), 0.95),
            (MagicMock(metadata={"entity_id": "media_player.office_tv_cast", "integration": "cast", "friendly_name": "Office TV Cast"}), 0.92)
        ]
        
        mock_collection = MagicMock()
        mock_collection.similarity_search_with_score = MagicMock(return_value=mock_results)
        
        async def mock_run_blocking(func, *args):
            return func(*args)
            
        with patch('app.settings.run_blocking', new_callable=AsyncMock) as mock_run, \
             patch('app.settings.GlobalResources') as mock_global:
            mock_run.side_effect = mock_run_blocking
            mock_global.ha_collection = mock_collection
            
            # TEST 1: Turn Off (Power)
            # Should prefer the Android TV integration because it controls the actual hardware power
            # androidtv scores 80, cast scores -50, music_assistant scores -10
            eid, integration, _ = await smart_resolve_entity("Office TV", "turn_off", mock_collection)
            print(f"\n[Turn Off] Resolved: {eid} ({integration})")
            self.assertEqual(eid, "media_player.office_tv_android")
            self.assertEqual(integration, "androidtv")

            # TEST 2: Play Music
            # Should prefer Music Assistant (scores 200 for is_music)
            eid_music, int_music, _ = await smart_resolve_entity("Office TV", "play_media", mock_collection, is_music=True)
            print(f"[Play Music] Resolved: {eid_music} ({int_music})")
            self.assertEqual(eid_music, "media_player.office_tv_mass")
            self.assertEqual(int_music, "music_assistant")

            # TEST 3: Watch Video
            # Should prefer Android TV (Hardware) - scores 100 for is_video
            # music_assistant scores -100 for video (strict prohibition)
            eid_video, int_video, _ = await smart_resolve_entity("Office TV", "watch_media", mock_collection, is_video=True)
            print(f"[Watch Video] Resolved: {eid_video} ({int_video})")
            self.assertEqual(eid_video, "media_player.office_tv_android")

            # TEST 4: Ambiguous "Play" (No music flag)
            # music_assistant scores 50, androidtv scores 20, cast scores 0
            eid_amb, int_amb, _ = await smart_resolve_entity("Office TV", "play_media", mock_collection)
            print(f"[Ambiguous Play] Resolved: {eid_amb} ({int_amb})")
            self.assertEqual(int_amb, "music_assistant")

if __name__ == '__main__':
    unittest.main()
