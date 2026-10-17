"""The short Redis record that says someone asked Jarvis to learn their voice."""
from services.common import voice_learning


def test_a_session_is_a_plain_count_that_can_be_decremented():
    assert voice_learning.encode() == str(voice_learning.TARGET_SAMPLES)
    assert voice_learning.remaining(voice_learning.encode()) == voice_learning.TARGET_SAMPLES
    assert voice_learning.remaining("1") == 1
    assert voice_learning.remaining("0") is None
    assert voice_learning.remaining(None) is None
    assert voice_learning.remaining("not a count") is None


def test_sessions_belong_to_one_person():
    assert voice_learning.key("jeremiah") == "voice:learning:jeremiah"
    assert voice_learning.key("michele") != voice_learning.key("jeremiah")
