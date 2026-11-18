"""A transport command's reply is words a voice can say, never an entity id."""
from services.execution.handlers.media import transport_said


def test_a_transport_reply_names_the_player():
    assert transport_said("next", {"friendly_name": "Den TV"}, "media_player.den_tv_cast") == "Skipped to the next track on Den TV."
    assert transport_said("stop", {}, "media_player.loft_speaker") == "Stopped on loft speaker."
    assert transport_said("volume_set", {"friendly_name": "Den TV"}, "media_player.x") == "Sent volume_set to Den TV."
