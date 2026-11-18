"""The records a satellite teaching session keeps in Redis."""
from services.common import voice_enroll, voice_speaker


def test_a_session_round_trips_and_rejects_garbage():
    state = voice_enroll.new_session("robin", "abc")
    assert voice_enroll.decode(voice_enroll.encode(state)) == state
    assert voice_enroll.decode(None) is None
    assert voice_enroll.decode("not json") is None
    assert voice_enroll.decode('{"username": "", "session": "x"}') is None
    assert voice_enroll.decode('{"username": "robin"}') is None
    assert voice_enroll.decode('{"username": "robin", "session": "x", "got": "two"}')["got"] == 0


def test_a_take_meets_its_chat_request_despite_punctuation_and_case():
    assert voice_enroll.take_key("The morning sun.") == voice_enroll.take_key("the  morning sun")
    assert voice_enroll.take_key("The morning sun.") != voice_speaker.key("The morning sun.")
    assert voice_enroll.take_key(" ... ") is None


def test_a_take_carries_the_signature_and_nothing_else():
    raw = voice_enroll.encode_take({"embedding": [0.1, 0.2], "speech_seconds": 3.0, "model": "m",
                                    "matched_username": "robin", "candidates": []})
    assert voice_enroll.decode_take(raw) == {"embedding": [0.1, 0.2], "speech_seconds": 3.0, "model": "m"}
    assert voice_enroll.encode_take({"embedding": []}) is None
    assert voice_enroll.encode_take(None) is None
    assert voice_enroll.decode_take("[]") is None


def test_there_is_a_sentence_for_every_take_and_they_end_cleanly():
    assert len(voice_enroll.SENTENCES) >= voice_enroll.TAKES
    assert all(s.endswith(".") and "?" not in s for s in voice_enroll.SENTENCES)
    assert voice_enroll.sentence(len(voice_enroll.SENTENCES)) == voice_enroll.SENTENCES[0]
