from services.common.voice_speaker import decode


def test_decode_returns_username_for_a_valid_record():
    assert decode('{"username": "sam"}') == "sam"


def test_decode_ignores_non_string_values():
    assert decode(object()) is None


def test_a_record_without_a_speaker_still_carries_the_scores():
    from services.common import voice_speaker

    raw = voice_speaker.encode(None, 0.6, {"candidates": [{"username": "a", "score": 0.6, "group": "adult"}],
                                           "speech_seconds": 2.0, "threshold": 0.8, "embedding": [1.0]})
    assert voice_speaker.decode(raw) is None
    record = voice_speaker.decode_record(raw)
    assert record["top"] == [{"username": "a", "score": 0.6}] and record["speech_seconds"] == 2.0
    assert "embedding" not in raw


def test_only_a_close_unrecognised_command_with_enough_speech_is_worth_offering():
    from services.common import voice_speaker

    close = {"matched": False, "embedding": [1.0], "speech_seconds": 2.0, "candidates": [{"score": 0.6}]}
    assert voice_speaker.worth_offering(close)
    assert not voice_speaker.worth_offering({**close, "matched": True})
    assert not voice_speaker.worth_offering({**close, "speech_seconds": 1.0})
    assert not voice_speaker.worth_offering({**close, "candidates": [{"score": 0.4}]})
    assert not voice_speaker.worth_offering({**close, "embedding": None})
