
import sys
import os

# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# pyright: ignore[reportMissingImports]
from app.domains.media.devices import _route_by_intent  # pyright: ignore[reportMissingImports]

def test_dual_mode_routing():
    print("Testing 'Play' vs 'Watch' routing priority...")
    
    # Mock Group Members
    # Scenario: A room with a TV (Android) and a Smart Speaker (Cast/MA)
    members = [
        {
            "entity_id": "media_player.living_room_tv",
            "integration": "androidtv",
            "friendly_name": "Living Room TV",
            "domain": "media_player",
            "capabilities": "turn_on,play_media",
            "attributes": {"device_class": "tv"}
        },
        {
            "entity_id": "media_player.living_room_speaker",
            "integration": "music_assistant", # Explicit MA integration
            "friendly_name": "Living Room Speaker",
            "domain": "media_player",
            "capabilities": "play_media",
            "attributes": {"mass_player_type": "player"} # MA marker
        }
    ]
    
    # Test Case 1: "Watch" Command (Video)
    # Expected: Prioritize Android TV
    print("\nCase 1: Intent 'watch_media' (Video)")
    selected_video = _route_by_intent("watch_media", members, is_music=False, is_video=True)
    
    if selected_video and selected_video["entity_id"] == "media_player.living_room_tv":
        print(f"PASS: Selected {selected_video['entity_id']} for Video.")
    else:
        print(f"FAIL: Selected {selected_video['entity_id'] if selected_video else 'None'} instead of Video Device.")
        exit(1)

    # Test Case 2: "Play" Command (Default/Music)
    # Expected: Prioritize Music Assistant
    print("\nCase 2: Intent 'play_media' (Music Context)")
    # Logic in devices.py infers is_music=True if intent is play_media and not is_video
    selected_music = _route_by_intent("play_media", members, is_music=True, is_video=False)
    
    if selected_music and selected_music["entity_id"] == "media_player.living_room_speaker":
         print(f"PASS: Selected {selected_music['entity_id']} for Music.")
    else:
         print(f"FAIL: Selected {selected_music['entity_id'] if selected_music else 'None'} instead of Music Device.")
         exit(1)

    print("\nAll routing regression tests passed!")

if __name__ == "__main__":
    test_dual_mode_routing()
