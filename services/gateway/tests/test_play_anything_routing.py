"""How the gateway hands "Play ..." and "Read ..." on: rooms, a skip, a question's answer, other reading.

Rooms and players are fakes.
"""
from __future__ import annotations

import fakeredis
import pytest

from services.gateway import fast_media
from services.gateway.intent_engine import IntentEngine


@pytest.fixture(autouse=True)
def _redis(monkeypatch):
    from services.gateway import cache

    monkeypatch.setattr(cache, "_redis", fakeredis.FakeRedis(decode_responses=True))


ROOMS = {"kitchen", "den", "back porch"}


@pytest.mark.parametrize(("said", "expected"), [
    ("some music in the kitchen", ("some music", "kitchen")),
    ("jazz in the den", ("jazz", "den")),
    ("Moon Garden in the back porch", ("Moon Garden", "back porch")),
    ("Singing in the Rain", ("Singing in the Rain", None)),
    ("Murder in the Cathedral", ("Murder in the Cathedral", None)),
])
def test_a_room_after_what_to_play_is_split_off_only_when_the_household_has_it(said, expected):
    assert fast_media.split_room(said, lambda room: room.lower() in ROOMS) == expected


def test_the_engine_knows_its_rooms_from_the_devices():
    engine = IntentEngine.__new__(IntentEngine)
    engine.device_meta = {"light.a": {"area": "kitchen", "area_aliases": ("cookhouse",)}}
    assert engine.is_area("the Kitchen")
    assert engine.is_area("cookhouse")
    assert not engine.is_area("cathedral")


CHOICE = {"query": "Harbor", "entity_id": "media_player.den", "options": [
    {"label": "the song", "words": ["song", "music", "track", "the example band"], "pick": {"ident": "t"}},
    {"label": "the audiobook", "words": ["audiobook", "audio book", "book", "series", "story"], "pick": {"ident": "b"}},
]}


@pytest.mark.parametrize(("answer", "ident"), [
    ("the audiobook", "b"), ("The audiobook, please.", "b"), ("the song", "t"), ("the book", "b"),
    ("the first one", "t"), ("second", "b"), ("the one by the example band", "t"),
])
def test_an_answer_names_one_option(answer, ident):
    assert fast_media.match_choice(answer, CHOICE)["pick"]["ident"] == ident


@pytest.mark.parametrize("answer", ["turn on the kitchen light", "what time is it", "yes"])
def test_a_new_request_is_not_taken_as_an_answer(answer):
    assert fast_media.match_choice(answer, CHOICE) is None


def test_yes_answers_a_one_option_offer():
    offer = {"options": [{"label": "read it", "words": ["yes", "read"], "read": {"book_id": "1"}}]}
    assert fast_media.match_choice("Yes please", offer)["read"]["book_id"] == "1"
    assert fast_media.match_choice("sure", offer) is not None


def test_a_question_is_remembered_until_answered():
    fast_media.remember_choice("u1", CHOICE)
    assert fast_media.pending_choice("u1")["query"] == "Harbor"
    assert fast_media.pending_choice("someone-else") is None
    fast_media.clear_choice("u1")
    assert fast_media.pending_choice("u1") is None


def _main():
    from services.gateway import main
    return main


@pytest.mark.parametrize(("query", "plain"), [
    ("play the next episode", True),
    ("Play the next song", True),
    ("play the previous track", True),
    ("Play the song Next to Me", False),
    ("Play Next to Me", False),
    ("skip this track", True),
])
def test_play_next_is_a_skip_but_a_song_named_next_is_not(query, plain):
    assert _main().is_plain_media_command(query) is plain


@pytest.mark.parametrize(("query", "intent"), [
    ("Read John 3:16", "play_media"),
    ("Read chapter 1 of Moon Garden", "play_media"),
    ("Read my email", None),
    ("read me my messages", None),
    ("Read the news", None),
    ("read this article", None),
])
def test_read_is_media_only_for_books_and_scripture(query, intent):
    engine = IntentEngine.__new__(IntentEngine)
    engine.is_active = False
    engine.model = None
    engine.intent_embeddings = []
    got, _conf = engine.classify(query)
    if intent:
        assert got == intent
    else:
        assert got != "play_media"


def test_a_room_said_is_where_it_plays(monkeypatch):
    main = _main()
    monkeypatch.setattr(main.engine, "device_meta", {"light.k": {"area": "kitchen", "area_aliases": ()}}, raising=False)
    assert main.extract_media_place("play some music in the kitchen", []) == ("some music", "kitchen")
    assert main.extract_media_place("Play Moon Garden on the Den TV", []) == ("Moon Garden", "Den TV")
    assert main.extract_media_place("Play Singing in the Rain", []) == ("Singing in the Rain", None)


# --------------------------------------------------------------------------- #
# Through the chat handler, as a voice satellite sends it                     #
# --------------------------------------------------------------------------- #

def test_a_named_play_searches_every_source_on_the_satellites_speaker(monkeypatch):
    from services.gateway.tests.test_fast_media import SAT_SPEAKER, TV, VOICE, _ask

    _, executed = _ask(monkeypatch, "Play Moon Garden.", "play_media", voice=VOICE, cache=TV)
    [(path, payload)] = executed
    assert path == "/media/play" and payload["entity_id"] == SAT_SPEAKER
    assert payload["query"] == "Moon Garden" and payload["search_everywhere"] is True


def test_a_genre_still_goes_to_the_music_genre_search(monkeypatch):
    from services.gateway.tests.test_fast_media import TV, VOICE, _ask

    _, executed = _ask(monkeypatch, "Play some worship music.", "play_media", voice=VOICE, cache=TV)
    [(_, payload)] = executed
    assert payload["media_content_type"] == "genre" and payload["search_everywhere"] is False


def test_the_answer_to_a_question_plays_the_option_named(monkeypatch):
    from services.gateway.tests.test_fast_media import TV, VOICE, _ask

    fast_media.remember_choice("sam", CHOICE)
    said, executed = _ask(monkeypatch, "The audiobook.", "unknown", voice=VOICE, cache=TV, reply="Playing it.")
    assert said == "Playing it."
    [(path, payload)] = executed
    assert path == "/media/play"
    assert payload["pick"] == {"ident": "b"} and payload["entity_id"] == "media_player.den"
    assert fast_media.pending_choice("sam") is None


def test_read_scripture_is_read_not_searched_as_an_audiobook(monkeypatch):
    from services.gateway.tests.test_fast_media import TV, VOICE, _ask

    _said, executed = _ask(monkeypatch, "Read John 3:16", "play_media", voice=VOICE, cache=TV, reply="John 3:16 text")
    [(path, payload)] = executed
    assert path == "/bible" and payload["ref"] == "John 3:16"
