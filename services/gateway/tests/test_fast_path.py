import pytest
from services.gateway.intent_engine import IntentEngine


def test_fast_path_intent_classification():
    ie = IntentEngine()

    test_cases = [
        ("Play the Heidi Saint John Podcast", "play_media", 1.0),
        ("Play the Heidi St. John podcast", "play_media", 1.0),
        ("play brandon lake on office tv", "play_media", 1.0),
        ("play some jazz", "play_media", 1.0),
        ("listen to podcast", "play_media", 1.0),
        ("next track", "media_transport", 1.0),
        ("skip this song", "media_transport", 1.0),
        ("skip", "media_transport", 1.0),
        ("pause", "media_transport", 1.0),
        ("resume", "media_transport", 1.0),
        ("stop", "media_transport", 1.0),
        ("volume up", "media_transport", 1.0),
        ("turn on kitchen light", "turn_on", 1.0),
        ("turn off the living room lights", "turn_off", 1.0),
        ("turn the piano lamp off", "turn_off", 1.0),
        (" Turn the piano lamp off, turn the piano lamp off.", "turn_off", 1.0),
        ("turn the office light on", "turn_on", 1.0),
        ("piano lamp off", "turn_off", 1.0),
        ("reindex my storage files", "index_storage", 1.0),
        ("sync home assistant", "sync_ha", 1.0),
    ]

    for query, expected_intent, expected_conf in test_cases:
        intent, conf = ie.classify(query)
        assert intent == expected_intent, f"Query '{query}' expected intent '{expected_intent}' but got '{intent}'"
        assert conf == expected_conf, f"Query '{query}' expected conf {expected_conf} but got {conf}"


def test_fast_path_raven_not_hijacked():
    ie = IntentEngine()
    # Raven explicit missions should not trigger fast path
    intent, conf = ie.classify("Raven, build a test app")
    assert intent == "raven_mission"
    assert conf == 0.0
