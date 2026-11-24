"""What a spoken "play ..." searches for (fast_media.parse_music_request and the fast path).

Measured by voice on 2026-11-23: "Play some worship music." searched Music Assistant for
"some worship music" and "Playin' music." for "Playin' music.", and both failed. Every
name here is made up.
"""
from __future__ import annotations

import pytest

from services.gateway import fast_media
from services.gateway import main as gateway_main
from services.gateway.tests.test_fast_media import SAT_SPEAKER, TV, VOICE, _ask


@pytest.mark.parametrize("said, kind, query, fallback", [
    # The four utterances heard live.
    ("Play some worship music.", "genre", "worship music", "worship"),
    ("Playin' music.", "library", None, None),
    ("Play music.", "library", None, None),
    ("Please play some worship music", "genre", "worship music", "worship"),
    # Other verbs and filler speech recognition writes.
    ("Playing music", "library", None, None),
    ("Can you put on a little instrumental music for me?", "genre", "instrumental music", "instrumental"),
    ("start playing some hymns", "genre", "hymns", None),
    ("play us some kids songs please", "genre", "kids songs", "kids"),
    ("Could you play something relaxing", "genre", "relaxing", None),
    ("play me a song", "library", None, None),
    # "music" stays when the genre needs it, and the genre alone is the relaxed try.
    ("Play Christian music", "genre", "Christian music", "Christian"),
    # Artists, albums and tracks stay as they were said.
    ("Play Example Band", "named", "Example Band", None),
    ("Play Always Holding On by Example Band", "named", "Always Holding On by Example Band", None),
    ("play music by Example Band", "named", "Example Band", None),
    ("Play Example Band songs", "named", "Example Band", "Example Band songs"),
    ("Play A Made Up Band", "named", "A Made Up Band", None),
])
def test_what_a_music_request_searches_for(said, kind, query, fallback):
    assert fast_media.parse_music_request(said) == fast_media.MusicAsk(kind, query, fallback)


def test_the_device_is_still_split_off_after_any_play_verb():
    assert gateway_main.extract_media_request("Playin' music on the kitchen speaker.") == ("music", "kitchen speaker")
    assert gateway_main.extract_media_request("Put on Example Band") == ("Example Band", None)


@pytest.mark.parametrize("said", ["Playin' music.", "Play music."])
def test_music_that_names_nothing_is_a_library_pick(monkeypatch, said):
    _, executed = _ask(monkeypatch, said, "play_media", voice=VOICE, cache=TV)
    [(path, payload)] = executed
    assert path == "/media/play" and payload["entity_id"] == SAT_SPEAKER
    assert payload["query"] is None and payload["media_type"] == "music"


@pytest.mark.parametrize("said", ["Play some worship music.", "Please play some worship music"])
def test_a_genre_is_searched_as_a_genre_with_one_relaxed_try(monkeypatch, said):
    _, executed = _ask(monkeypatch, said, "play_media", voice=VOICE, cache=TV)
    [(_, payload)] = executed
    assert payload["entity_id"] == SAT_SPEAKER
    assert payload["query"] == "worship music"
    assert payload["media_content_type"] == "genre"
    assert payload["fallback_query"] == "worship"


def test_an_artist_request_is_unchanged(monkeypatch):
    _, executed = _ask(monkeypatch, "Play Example Band", "play_media", voice=VOICE, cache=TV)
    [(_, payload)] = executed
    assert payload["query"] == "Example Band"
    assert payload["media_content_type"] == "artist" and payload["fallback_query"] is None
