"""How Jarvis hears a spoken request and follows up on it (the 2026-11-24 live run).

Speech to text glued verbs to titles ("PlayDork Diaries 3"), heard "Read chapter 1 of" as
"Reach after one of", and an answer to "the song, or the audiobook?" came back as "The
audio." The model then played the song, on another device. Replies also read entity ids
aloud. Every title, name and device here is made up.
"""
from __future__ import annotations

import fakeredis
import pytest

from services.common.spoken_names import humanize, speakable
from services.gateway import fast_media, voice_transcript


@pytest.fixture(autouse=True)
def _redis(monkeypatch):
    from services.gateway import cache

    monkeypatch.setattr(cache, "_redis", fakeredis.FakeRedis(decode_responses=True))


# --------------------------------------------------------------------------- what was heard

@pytest.mark.parametrize(("heard", "meant"), [
    ("PlayMoon Garden 3.", "Play Moon Garden 3."),
    ("ReMoon Garden.", "Read Moon Garden."),
    ("ReadMoon Garden", "Read Moon Garden"),
    ("Play3 Little Birds", "Play 3 Little Birds"),
    ("Reach after one of Lantern Keepers.", "Read chapter one of Lantern Keepers."),
    ("Red chapter 2 of Lantern Keepers", "Read chapter 2 of Lantern Keepers"),
    ("Rechapter 4 in Lantern Keepers", "Read chapter 4 in Lantern Keepers"),
    ("Chapter one of Lantern Keepers", "Read chapter one of Lantern Keepers"),
    ("Jarvis, PlayMoon Garden", "Jarvis, Play Moon Garden"),
])
def test_a_glued_or_misheard_verb_is_repaired(heard, meant):
    assert voice_transcript.normalize(heard) == meant


@pytest.mark.parametrize("said", [
    "Play Moon Garden.",
    "Read chapter 2 of Lantern Keepers",
    "After one of these, call me.",        # "after" is "chapter" only after a "read" word
    "PlayStation is on in the den",
    "Playlist Sunday Mix",
    "Remind me to water the plants",
    "Reach the top shelf",
    "Turn on the den lamp",
    "",
])
def test_anything_else_is_left_as_it_was_heard(said):
    assert voice_transcript.normalize(said) == said


# --------------------------------------------------------------------------- the follow-up

CHOICE = {"query": "Harbor", "entity_id": "media_player.den_satellite_speaker", "options": [
    {"label": "the song", "words": ["song", "music", "track", "the example band"],
     "pick": {"ident": "t", "kind": "track"}},
    {"label": "the audiobook", "words": ["audiobook", "audio book", "book", "series", "story"],
     "pick": {"ident": "b", "kind": "audiobook"}},
]}


@pytest.mark.parametrize(("answer", "ident"), [
    ("The audio.", "b"), ("audio book", "b"), ("the book one", "b"), ("The audiobook.", "b"), ("audibook", "b"),
    ("play the audiobook", "b"), ("the song one", "t"), ("music", "t"), ("the example band one", "t"),
])
def test_a_close_variant_names_its_option(answer, ident):
    got = fast_media.read_choice_answer(answer, CHOICE)
    assert got.kind == "option" and got.option["pick"]["ident"] == ident


@pytest.mark.parametrize(("answer", "kind"), [
    ("Play Brandon Example", "new_request"),
    ("Play the playlist Sunday Mix", "new_request"),
    ("turn off the den lamp", "new_request"),
    ("what time is it", "new_request"),
    ("never mind", "cancel"),
    ("no", "cancel"),
    ("um", "unclear"),
    ("the uh", "unclear"),
    ("yes", "unclear"),
])
def test_anything_else_is_a_new_request_a_cancel_or_unclear(answer, kind):
    assert fast_media.read_choice_answer(answer, CHOICE).kind == kind


def test_the_question_asked_again_is_brief():
    assert fast_media.choice_reask(CHOICE) == "Sorry, the song or the audiobook?"
    offer = {"options": [{"label": "read it", "words": ["yes"], "read": {"book_id": "1"}}]}
    assert fast_media.choice_reask(offer) == "Sorry, should I read it? Say yes or no."


# --------------------------------------------------------------------------- no entity ids aloud

@pytest.mark.parametrize(("text", "said"), [
    ("Playing worship, the radio station, on media_player.den_example_echo_media_player.",
     "Playing worship, the radio station, on Den Example Echo."),
    ("The light.kitchen_ceiling_2 is on.", "The Kitchen Ceiling 2 is on."),
    ("Nothing internal here.", "Nothing internal here."),
    ("It is 3.5 degrees and version 2.1.", "It is 3.5 degrees and version 2.1."),
])
def test_an_entity_id_is_never_said(text, said):
    assert speakable(text) == said


def test_the_devices_friendly_name_is_said_when_known():
    names = {"media_player.den_example_echo_media_player": "Den Echo"}
    assert speakable("Playing on media_player.den_example_echo_media_player.", names) == "Playing on Den Echo."
    assert humanize("media_player.den_example_echo_media_player") == "Den Example Echo"


# --------------------------------------------------------------------------- through the chat handler

def _ask(monkeypatch, query, intent="unknown", **kw):
    from services.gateway.tests.test_fast_media import TV, VOICE
    from services.gateway.tests.test_fast_media import _ask as ask

    return ask(monkeypatch, query, intent, voice=VOICE, cache=TV, **kw)


def test_the_audio_plays_the_audiobook_on_the_device_that_asked(monkeypatch):
    fast_media.remember_choice("sam", CHOICE)
    said, executed = _ask(monkeypatch, "The audio.", reply="Playing Harbor, the audiobook, on Den Satellite.")
    assert said == "Playing Harbor, the audiobook, on Den Satellite."
    [(path, payload)] = executed
    assert path == "/media/play"
    assert payload["pick"]["ident"] == "b"
    assert payload["entity_id"] == CHOICE["entity_id"]  # never the household's last TV
    assert fast_media.pending_choice("sam") is None


def test_an_unclear_answer_is_asked_again_once_never_by_the_model(monkeypatch):
    fast_media.remember_choice("sam", CHOICE)
    said, executed = _ask(monkeypatch, "Um.")
    assert said == "Sorry, the song or the audiobook?" and executed == []
    assert fast_media.pending_choice("sam")["reasked"] is True
    said, executed = _ask(monkeypatch, "The uh.")
    assert said.startswith("Sorry, I still didn't catch that") and executed == []
    assert fast_media.pending_choice("sam") is None


def test_a_new_request_drops_the_question_and_goes_on(monkeypatch):
    from services.gateway.tests.test_fast_media import SAT_SPEAKER

    fast_media.remember_choice("sam", CHOICE)
    _said, executed = _ask(monkeypatch, "Play Moon Garden.", "play_media")
    [(path, payload)] = executed
    assert path == "/media/play" and payload["query"] == "Moon Garden" and payload["entity_id"] == SAT_SPEAKER
    assert fast_media.pending_choice("sam") is None


def test_a_glued_play_is_routed_as_a_play(monkeypatch):
    _said, executed = _ask(monkeypatch, "PlayMoon Garden 3.", "play_media")
    [(path, payload)] = executed
    assert path == "/media/play" and payload["query"] == "Moon Garden 3"


def test_a_spoken_reply_never_reads_an_entity_id(monkeypatch):
    from services.gateway.tests.test_fast_media import SAT_SPEAKER

    said, _executed = _ask(monkeypatch, "Play some worship music.", "play_media",
                           reply=f"Playing worship, the radio station, on {SAT_SPEAKER}.")
    assert "media_player." not in said
    assert said == "Playing worship, the radio station, on Den Satellite Speaker."
