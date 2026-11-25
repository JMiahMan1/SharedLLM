"""Spoken volume commands go straight to the speaker, without the model (fast_volume.py).

2026-11-24: "Set volume to 100%" at an Echo went to the model, took 30 s and changed nothing.
Every name is made up (the den satellite from test_fast_media.py).
"""
from __future__ import annotations

import pytest

from services.gateway import fast_volume
from services.gateway.fast_volume import VolumeAsk
from services.gateway.tests.test_fast_media import KITCHEN, SAT_SPEAKER, TV, VOICE, _ask


def _player(eid, name, state="idle", volume=None):
    attrs = {"friendly_name": name}
    if volume is not None:
        attrs["volume_level"] = volume
    return {"entity_id": eid, "state": state, "attributes": attrs}


# --------------------------------------------------------------------------- the words


@pytest.mark.parametrize("query, level", [
    ("Set volume to 100%", 1.0),
    ("set the volume to 40 percent", 0.4),
    ("Jarvis, set the volume to 35%.", 0.35),
    ("change the volume to 60", 0.6),
    ("volume 50", 0.5),
    ("Volume 7", 0.7),               # a 0-10 scale, like the speaker's buttons
    ("volume to 3 percent", 0.03),   # said as a percentage, read as one
    ("max volume", 1.0),
    ("full volume", 1.0),
    ("set the volume to max", 1.0),
    ("turn it all the way up", 1.0),
    ("half volume", 0.5),
    ("set the volume to half", 0.5),
    ("volume 0", 0.0),
])
def test_absolute_levels(query, level):
    assert fast_volume.parse(query) == VolumeAsk("set", level=level)


@pytest.mark.parametrize("query, level", [
    ("Set volume to 150%", 1.0),
    ("volume 250", 1.0),
])
def test_levels_are_clamped_to_the_speakers_range(query, level):
    assert fast_volume.parse(query) == VolumeAsk("set", level=level)


@pytest.mark.parametrize("query, delta", [
    ("turn it up", 0.10),
    ("turn it down", -0.10),
    ("turn the volume up", 0.10),
    ("turn up the music", 0.10),
    ("volume up", 0.10),
    ("volume down please", -0.10),
    ("turn it up a bit", 0.05),
    ("turn it down a little", -0.05),
    ("turn it up a lot", 0.25),
    ("louder", 0.10),
    ("make it louder", 0.10),
    ("a little louder", 0.05),
    ("quieter", -0.10),
    ("Quieter please", -0.10),
    ("a bit softer", -0.05),
])
def test_relative_steps(query, delta):
    assert fast_volume.parse(query) == VolumeAsk("step", delta=delta)


@pytest.mark.parametrize("query, kind", [
    ("mute", "mute"), ("mute it", "mute"), ("mute the tv", "mute"),
    ("unmute", "unmute"), ("Unmute the music", "unmute"),
])
def test_mute_and_unmute(query, kind):
    assert fast_volume.parse(query) == VolumeAsk(kind)


@pytest.mark.parametrize("query", [
    "turn up the heat", "turn it up to 72 degrees", "turn on the lights", "what is the volume",
    "mute notifications for the family chat", "play louder than words", "set a timer for 10 minutes",
    "turn down the brightness",
])
def test_other_requests_are_not_volume_commands(query):
    assert fast_volume.parse(query) is None


@pytest.mark.parametrize("query, place", [
    ("Set volume to 100%", None),
    ("turn it up", None),
    ("Set volume to 100% on the kitchen speaker", "kitchen speaker"),
    ("turn it down in the den", "den"),
    ("set the kitchen speaker volume to 30", "kitchen speaker"),
    ("mute the tv", "tv"),
])
def test_the_place_a_command_names(query, place):
    assert fast_volume.place(query) == place


def test_payloads():
    assert fast_volume.transport_payload(VolumeAsk("set", level=1.0), 0.9) == {
        "command": "volume_set", "volume_level": 1.0}
    assert fast_volume.transport_payload(VolumeAsk("step", delta=0.1), 0.95) == {
        "command": "volume_set", "volume_level": 1.0}            # clamped
    assert fast_volume.transport_payload(VolumeAsk("step", delta=-0.25), 0.1) == {
        "command": "volume_set", "volume_level": 0.0}
    assert fast_volume.transport_payload(VolumeAsk("step", delta=0.1), None) == {"command": "volume_up"}
    assert fast_volume.transport_payload(VolumeAsk("step", delta=-0.1), None) == {"command": "volume_down"}
    assert fast_volume.transport_payload(VolumeAsk("mute"), None) == {"command": "volume_mute", "muted": True}
    assert fast_volume.transport_payload(VolumeAsk("unmute"), None) == {"command": "volume_unmute", "muted": False}


# --------------------------------------------------------------------------- the chat handler


PLAYERS = [
    _player(SAT_SPEAKER, "Den Satellite Speaker", "idle", volume=0.9),
    _player(TV, "Den TV Chrome", "playing", volume=0.3),
    _player(KITCHEN, "Kitchen Speaker", "idle", volume=0.2),
]


def test_set_volume_at_a_satellite_sets_its_own_speaker_without_the_model(monkeypatch):
    said, executed = _ask(monkeypatch, "Set volume to 100%", "unknown", voice=VOICE, players=PLAYERS, cache=TV,
                          reply="Volume is at 100% on Den Satellite Speaker.")
    assert said == "Volume is at 100% on Den Satellite Speaker."
    [(path, payload)] = executed
    assert path == "/media/transport"
    assert payload["entity_id"] == SAT_SPEAKER     # not the TV that is playing, not the last one used
    assert payload["command"] == "volume_set" and payload["volume_level"] == 1.0


def test_a_relative_step_starts_from_the_speakers_own_level(monkeypatch):
    _, executed = _ask(monkeypatch, "turn it down a bit", "turn_off", voice=VOICE, players=PLAYERS)
    [(_, payload)] = executed
    assert payload["entity_id"] == SAT_SPEAKER
    assert payload["command"] == "volume_set" and payload["volume_level"] == pytest.approx(0.85)


def test_a_named_device_is_set_instead_of_the_satellite(monkeypatch):
    _, executed = _ask(monkeypatch, "set the volume to 30 percent on the kitchen speaker", "unknown",
                       voice=VOICE, players=PLAYERS)
    [(_, payload)] = executed
    assert payload["entity_id"] == KITCHEN
    assert payload["volume_level"] == 0.3


def test_mute_at_a_satellite(monkeypatch):
    _, executed = _ask(monkeypatch, "mute", "unknown", voice=VOICE, players=PLAYERS)
    [(_, payload)] = executed
    assert payload["entity_id"] == SAT_SPEAKER
    assert payload["command"] == "volume_mute" and payload["muted"] is True


def test_without_a_satellite_the_playing_player_is_turned_up(monkeypatch):
    _, executed = _ask(monkeypatch, "louder", "unknown", voice=None, players=PLAYERS)
    [(_, payload)] = executed
    assert payload["entity_id"] == TV
    assert payload["volume_level"] == pytest.approx(0.4)


def test_a_failure_the_speaker_reports_is_said_as_it_is(monkeypatch):
    said, _ = _ask(monkeypatch, "max volume", "unknown", voice=VOICE, players=PLAYERS,
                   reply="I asked Den Satellite Speaker for 100%, but it still reports 90%.")
    assert said == "I asked Den Satellite Speaker for 100%, but it still reports 90%."
