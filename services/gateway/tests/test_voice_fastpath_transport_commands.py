"""Voice fast-path transport command extraction."""
from services.gateway.intent_engine import IntentEngine
from services.gateway.main import extract_media_transport_command, extract_volume_level


def test_volume_directions():
    assert extract_media_transport_command("louder") == "volume_up"
    assert extract_media_transport_command("turn it up") == "volume_up"
    assert extract_media_transport_command("volume up") == "volume_up"
    assert extract_media_transport_command("quieter") == "volume_down"
    assert extract_media_transport_command("turn it down") == "volume_down"


def test_mute_and_unmute():
    assert extract_media_transport_command("mute") == "volume_mute"
    assert extract_media_transport_command("unmute") == "volume_unmute"


def test_volume_set_variants():
    assert extract_media_transport_command("volume 5") == "volume_set"
    assert extract_media_transport_command("set the volume to 50 percent") == "volume_set"
    assert extract_volume_level("volume 5") == 0.5
    assert extract_volume_level("set the volume to 50 percent") == 0.5


def test_volume_never_pause():
    for q in ("louder", "turn it up", "volume up", "quieter", "turn it down",
              "mute", "unmute", "volume 5", "set the volume to 50 percent"):
        assert extract_media_transport_command(q) != "pause"


def test_plain_pause_still_pauses():
    assert extract_media_transport_command("pause") == "pause"


def test_audiobook_phrases():
    ie = IntentEngine()
    assert ie.classify("play my audiobook")[0] == "play_media"
    assert ie.classify("continue my book")[0] == "play_media"
    assert ie.classify("read some title")[0] == "play_media"
    assert ie.classify("read my messages")[0] != "play_media"
