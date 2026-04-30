from gateway.intent_engine import IntentEngine


def test_regex_override_play_media_without_model():
    engine = IntentEngine()

    intent, confidence = engine.classify("Play Brandon Lake on the Office TV")

    assert intent == "play_media"
    assert confidence == 1.0


def test_regex_override_pause_media_without_model():
    engine = IntentEngine()

    intent, confidence = engine.classify("Pause the music in the kitchen")

    assert intent == "pause_media"
    assert confidence == 1.0


def test_keyword_fallback_turn_on_without_model():
    engine = IntentEngine()

    intent, confidence = engine.classify("Please turn on the living room lights")

    assert intent == "turn_on"
    assert confidence > 0.0
