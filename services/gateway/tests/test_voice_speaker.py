from services.common.voice_speaker import decode


def test_decode_returns_username_for_a_valid_record():
    assert decode('{"username": "sam"}') == "sam"


def test_decode_ignores_non_string_values():
    assert decode(object()) is None
