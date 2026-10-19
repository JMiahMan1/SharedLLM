from services.common.voice_speaker import decode


def test_decode_returns_username_for_a_valid_record():
    assert decode('{"username": "michele"}') == "michele"


def test_decode_ignores_non_string_values():
    assert decode(object()) is None
