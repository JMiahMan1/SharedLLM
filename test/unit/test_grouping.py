import pytest
import asyncio
from unittest.mock import MagicMock
import sys
import os

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../app')))

from logic.pattern_matching import detect_entity_pattern, filter_entities_by_pattern

def test_pattern_detection():
    # 1. Locations
    patterns = detect_entity_pattern("Turn on Upstairs Lights")
    types = [p[0] for p in patterns]
    assert "location" in types
    
    patterns = detect_entity_pattern("Turn on North Bedroom")
    types = [p[0] for p in patterns]
    assert "direction" in types
    
    # 2. Numbers
    pass # Skipped specific number logic test here
    
    # 3. Lists
    patterns = detect_entity_pattern("Turn on Light 1 and 2")
    types = [p[0] for p in patterns]
    assert "list" in types

def test_filtering_logic():
    # Prepare entities with metadata
    friendly_names = {
        "light.kitchen_1": "Kitchen Light 1",
        "light.kitchen_2": "Kitchen Light 2",
        "light.bedroom_north": "North Bedroom Light",
        "light.bedroom_south": "South Bedroom Light",
        "light.upstairs_hall": "Upstairs Hallway",
        "light.downstairs_hall": "Downstairs Hallway"
    }
    
    entities = []
    for eid, integration in [
        ("light.kitchen_1", "hue"),
        ("light.kitchen_2", "hue"),
        ("light.bedroom_north", "hue"),
        ("light.bedroom_south", "hue"),
        ("light.upstairs_hall", "hue"),
        ("light.downstairs_hall", "hue")
    ]:
         meta = {"friendly_name": friendly_names[eid], "domain": "light"}
         entities.append((eid, integration, meta))
    
    # Test Location: Upstairs
    patterns = [("location", {'value': 'upstairs'})]
    filtered_up = filter_entities_by_pattern(entities, patterns)
    assert len(filtered_up) == 1
    assert filtered_up[0][0] == "light.upstairs_hall"
    
    # Test Direction: North
    patterns = [("direction", {'value': 'north'})]
    filtered_north = filter_entities_by_pattern(entities, patterns)
    assert len(filtered_north) == 1
    assert filtered_north[0][0] == "light.bedroom_north"
    
    # Test Number: Even (Kitchen Light 2)
    patterns = [("even", {})]
    filtered_even = filter_entities_by_pattern(entities, patterns)
    ids = [e[0] for e in filtered_even]
    assert "light.kitchen_2" in ids
    assert "light.kitchen_1" not in ids

    # Test Plural: "Lights"
    patterns = [("plural", {'value': 'lights'})]
    filtered_lights = filter_entities_by_pattern(entities, patterns)
    match_ids = [e[0] for e in filtered_lights]
    assert "light.kitchen_1" in match_ids
    assert "light.kitchen_2" in match_ids
    assert "light.upstairs_hall" not in match_ids # "Hallway" != "Light"

if __name__ == "__main__":
    test_pattern_detection()
    test_filtering_logic()
    print("All tests passed!")
