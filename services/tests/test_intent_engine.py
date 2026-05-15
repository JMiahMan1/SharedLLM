import pytest
from gateway.intent_engine import IntentEngine


def test_keyword_fallback_play():
    engine = IntentEngine()
    intent, confidence = engine.classify("play music")
    assert intent == "play_media"
    assert confidence == 1.0


def test_keyword_fallback_pause():
    engine = IntentEngine()
    intent, confidence = engine.classify("pause music")
    assert intent == "pause_media"
    assert confidence == 1.0


def test_keyword_fallback_turn_on():
    engine = IntentEngine()
    intent, confidence = engine.classify("turn on")
    assert intent == "turn_on"
    assert confidence == 1.0


@pytest.mark.local_only
def test_regex_override_play_media_without_model():
    """Requires fastembed + phrasebook loaded."""
    engine = IntentEngine()
    engine.load()
    if not engine.is_active:
        pytest.skip("Semantic Router not available")

    intent, confidence = engine.classify("Play Brandon Lake on the Office TV")
    assert intent == "play_media"
    assert confidence == 1.0


@pytest.mark.local_only
def test_regex_override_pause_media_without_model():
    """Requires fastembed + phrasebook loaded."""
    engine = IntentEngine()
    engine.load()
    if not engine.is_active:
        pytest.skip("Semantic Router not available")

    intent, confidence = engine.classify("Pause the music in the kitchen")
    assert intent == "pause_media"
    assert confidence == 1.0


@pytest.mark.local_only
def test_keyword_fallback_turn_on_without_model():
    """Requires fastembed + phrasebook loaded."""
    engine = IntentEngine()
    engine.load()
    if not engine.is_active:
        pytest.skip("Semantic Router not available")

    intent, confidence = engine.classify("Please turn on the living room lights")
    assert intent == "turn_on"
    assert confidence > 0.0
