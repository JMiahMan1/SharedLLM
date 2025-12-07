import pytest
import asyncio
from unittest.mock import MagicMock
import sys
import os

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../app')))

from logic.pattern_matching import detect_entity_pattern, filter_entities_by_pattern

def test_pattern_detection():
    # 1. Locations
    assert detect_entity_pattern("Turn on Upstairs Lights")[0] == "location"
    assert detect_entity_pattern("Turn on North Bedroom")[0] == "direction"
    
    # 2. Numbers
    assert detect_entity_pattern("Turn on Kitchen Light 1")[0] == None # Specific number extraction done inside filter, not query pattern?
    # Wait, the logic for specific singular number isn't a "pattern" that returns a set.
    # It returns None for pattern, but smart_resolve logic relies on 'detect_number_pattern' to enable 'allow_multiple'?
    # Actually, for "Light 1", we want specific resolution.
    
    # 3. Lists
    assert detect_entity_pattern("Turn on Light 1 and 2")[0] == "list"

def test_filtering_logic():
    entities = [
        ("light.kitchen_1", "hue"),
        ("light.kitchen_2", "hue"),
        ("light.bedroom_north", "hue"),
        ("light.bedroom_south", "hue"),
        ("light.upstairs_hall", "hue"),
        ("light.downstairs_hall", "hue")
    ]
    
    friendly_names = {
        "light.kitchen_1": "Kitchen Light 1",
        "light.kitchen_2": "Kitchen Light 2",
        "light.bedroom_north": "North Bedroom Light",
        "light.bedroom_south": "South Bedroom Light",
        "light.upstairs_hall": "Upstairs Hallway",
        "light.downstairs_hall": "Downstairs Hallway"
    }
    
    # Test Location: Upstairs
    filtered_up = filter_entities_by_pattern(entities, "location", {'value': 'upstairs'}, friendly_names)
    assert len(filtered_up) == 1
    assert filtered_up[0][0] == "light.upstairs_hall"
    
    # Test Direction: North
    filtered_north = filter_entities_by_pattern(entities, "direction", {'value': 'north'}, friendly_names)
    assert len(filtered_north) == 1
    assert filtered_north[0][0] == "light.bedroom_north"
    
    # Test Number: Even (Kitchen Light 2)
    filtered_even = filter_entities_by_pattern(entities, "even", {}, friendly_names)
    # Both Hallways don't have numbers, so they shouldn't match even/odd logic unless name has number.
    # Kitchen Light 2 should match.
    # Should Kitchen Light 1 match 'even'? No.
    ids = [e[0] for e in filtered_even]
    assert "light.kitchen_2" in ids
    assert "light.kitchen_1" not in ids

    # Test Plural: "Lights" (should match all lights)
    # friendly_names has "Kitchen Light 1", "Kitchen Light 2", "North Bedroom Light" etc.
    filtered_lights = filter_entities_by_pattern(entities, "plural", {'value': 'lights'}, friendly_names)
    # Should match all 6 entities because they all have "Light" or "Hallway" (Wait, Hallway doesn't have Light?)
    # "Upstairs Hallway" -> does not have "light".
    # So "Turn on Upstairs Lights" -> "Upstairs" (Location) + "Lights" (Plural)?
    # My detection logic only returns ONE pattern type currently.
    # If I say "Upstairs Lights", likely "Upstairs" (Location) is detected first or "Lights" (Plural)?
    # The order of regex checks triggers.
    # PATTERNS items iteration order matters. dict insertion order is preserved in Py3.7+.
    # 'location' comes before 'plural'. So "Upstairs Lights" -> Location: Upstairs.
    # So filter_entities filters by "Upstairs".
    # And ignores "Lights". Matches "Upstairs Hallway". Correct.
    
    # But "Turn on Kitchen Lights" -> Location: None (Kitchen not in list). Plural: Lights.
    # Filters by "Light".
    # "Kitchen Light 1" -> Matches "Light".
    # "Kitchen Light 2" -> Matches "Light".
    # "Upstairs Hallway" -> No Match "Light".
    
    match_ids = [e[0] for e in filtered_lights]
    assert "light.kitchen_1" in match_ids
    assert "light.kitchen_2" in match_ids
    assert "light.upstairs_hall" not in match_ids # "Hallway" != "Light"

if __name__ == "__main__":
    test_pattern_detection()
    test_filtering_logic()
    print("All tests passed!")
