"""A voice satellite's room reaches Jarvis only from an internal caller (homehub)."""
from types import SimpleNamespace

import pytest

from services.gateway import main as gateway_main


def _request(secret: str | None):
    headers = {"X-Internal-Secret": secret} if secret is not None else {}
    return SimpleNamespace(headers=headers)


CTX = {"area": "Den", "area_id": "den", "satellite_name": "Den speaker", "asked": "Which light?",
       "extra_system_prompt": "Keep it short.", "unexpected": "dropped"}


def test_voice_context_is_trusted_only_with_the_internal_secret(monkeypatch):
    monkeypatch.setattr(gateway_main, "INTERNAL_SECRET", "s3cret")
    body = {"query": "turn on the light", "voice_context": dict(CTX)}
    assert gateway_main._voice_context_from(_request("wrong"), body) is None
    assert "voice_context" not in body  # never passed on to the model untrusted
    body = {"voice_context": dict(CTX)}
    ctx = gateway_main._voice_context_from(_request("s3cret"), body)
    assert ctx == {"area": "Den", "area_id": "den", "satellite_name": "Den speaker", "asked": "Which light?",
                   "extra_system_prompt": "Keep it short."}


def test_the_satellite_entity_is_kept_only_when_it_is_one(monkeypatch):
    """A timer set by voice rings on the satellite that heard it, so its entity id travels on (well formed only)."""
    monkeypatch.setattr(gateway_main, "INTERNAL_SECRET", "s3cret")
    ctx = gateway_main._voice_context_from(_request("s3cret"), {"voice_context": {
        "satellite": "assist_satellite.den_speaker", "area": "Den"}})
    assert ctx == {"satellite": "assist_satellite.den_speaker", "area": "Den"}
    for bad in ("light.den", "assist_satellite.Den Speaker", "assist_satellite.", 7):
        ctx = gateway_main._voice_context_from(_request("s3cret"), {"voice_context": {"satellite": bad, "area": "Den"}})
        assert ctx == {"area": "Den"}, bad


def test_the_note_names_the_room_and_what_was_asked():
    note = gateway_main._voice_context_note({"area": "Den", "area_id": "den", "satellite_name": "Den speaker",
                                             "asked": "Which light?"})
    assert "in the Den (area id den)" in note and '"this room"' in note
    assert "You had just asked them: 'Which light?'" in note


def test_a_satellite_with_no_room_is_told_to_ask():
    note = gateway_main._voice_context_note({"satellite_name": "Spare speaker"})
    assert "no room assigned" in note
    assert gateway_main._voice_context_note({}) == ""



@pytest.mark.parametrize("query, question", [
    ("Is the hall lamp on?", True),
    ("are the kitchen lights off", True),
    ("Did I leave the porch light on?", True),
    ("Turn on the hall lamp.", False),
    ("Can you turn on the hall lamp?", False),
    ("Please turn off the den lights", False),
    ("Isabel's lamp on", False),
])
def test_a_question_about_a_light_is_not_a_command(query, question):
    assert gateway_main.asks_about_state(query) is question


def test_a_lights_state_in_words():
    lamp = {"entity_id": "light.hall_lamp", "state": "on", "attributes": {"friendly_name": "Hall Lamp", "brightness": 128}}
    assert gateway_main.describe_state(lamp) == "Hall Lamp is on at 50%."
    assert gateway_main.describe_state({"entity_id": "light.hall_lamp", "state": "off", "attributes": {}}) == "hall lamp is off."
    assert gateway_main.describe_state({"entity_id": "light.x", "state": "unavailable", "attributes": {"friendly_name": "X"}}) \
        == "X isn't responding right now."


@pytest.mark.parametrize("query, plain", [
    ("Can you skip this track?", True),
    ("Since you can't skip the track, stop the music please.", True),
    ("Just stop the music on the office TV.", True),
    ("next song", True),
    ("skip", True),
    ("Pause please.", True),
    ("Jarvis, stop", True),
    ("Stop the timer", False),
    ("Pause the vacuum", False),
    ("What is the next holiday?", False),
    ("Play some music", False),
])
def test_a_plain_media_command_is_recognised(query, plain):
    assert gateway_main.is_plain_media_command(query) is plain


def test_skip_and_stop_pick_the_named_player_that_is_playing():
    players = [
        {"entity_id": "media_player.den_tv_cast", "state": "playing", "attributes": {"friendly_name": "Den TV"}},
        {"entity_id": "media_player.den_tv", "state": "idle", "attributes": {"friendly_name": "Den TV"}},
        {"entity_id": "media_player.loft_speaker", "state": "idle", "attributes": {"friendly_name": "Loft Speaker"}},
    ]
    pick = gateway_main.resolve_media_target
    assert pick("stop the music on the den tv", players, media_type="transport", requested_device="the den tv") \
        == "media_player.den_tv_cast"
    assert pick("skip on the loft speaker", players, media_type="transport", requested_device="loft speaker") \
        == "media_player.loft_speaker"
    # No player named: the last one used, while it plays.
    assert pick("skip this track", players, media_type="transport", cached_device="media_player.den_tv_cast") \
        == "media_player.den_tv_cast"
