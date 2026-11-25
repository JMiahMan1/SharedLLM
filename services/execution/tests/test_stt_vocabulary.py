"""The household's words for speech to text: built from the integrations, bounded, ordered, never invented.

Every name here is made up.
"""
from __future__ import annotations

import pytest

from services.execution import media_vocabulary, stt_vocabulary


@pytest.fixture(autouse=True)
def _fresh():
    stt_vocabulary.reset()
    media_vocabulary.reset()
    yield
    stt_vocabulary.reset()
    media_vocabulary.reset()


@pytest.mark.parametrize(("raw", "said"), [
    ("Moon Garden (Unabridged)", "Moon Garden"),
    ("Moon Garden: A Story of Seeds", "Moon Garden"),
    ("The Lantern Keepers #Volume 1", "The Lantern Keepers"),
    ("Song Title | Live Session", "Song Title"),
    ("  Example   Band ", "Example Band"),
    ("A" * 80, ""),
    ("1234", ""),
    (None, ""),
])
def test_a_name_is_cleaned_to_how_it_is_said(raw, said):
    assert stt_vocabulary.clean_name(raw) == said


def test_kinds_take_turns_so_one_big_library_cannot_crowd_out_the_rest():
    found = {
        "person": ["Robin", "Sam"],
        "artist": [f"Made Up Artist {n}" for n in range(100)],
        "title": ["Moon Garden", "Lantern Keepers"],
    }
    names = stt_vocabulary.interleave(found, max_names=8)
    assert names[:3] == ["Robin", "Made Up Artist 0", "Moon Garden"]
    assert "Lantern Keepers" in names and "Sam" in names
    assert len(names) == 8


def test_the_list_is_bounded_by_characters_and_has_no_repeats():
    found = {"artist": ["Example Band", "example band", "EXAMPLE-BAND"] + [f"Band Number {n}" for n in range(200)]}
    names = stt_vocabulary.interleave(found, max_chars=120)
    assert names.count("Example Band") == 1 and "example band" not in names
    assert len(", ".join(names)) <= 120


def test_rooms_and_players_are_kept_apart_and_a_player_named_as_a_room_is_said_once():
    vocab = stt_vocabulary.build({"room": ["Den", "Kitchen"], "device": ["Den", "Den Satellite"],
                                  "artist": ["Example Band"]})
    assert vocab.rooms == ["Den", "Kitchen"]
    assert vocab.devices == ["Den Satellite"]
    assert vocab.names == ["Example Band"]
    assert vocab.prompt() == "Example Band, Den, Kitchen, Den Satellite."


def test_an_empty_vocabulary_offers_no_prompt():
    assert stt_vocabulary.Vocabulary().prompt() == ""


async def test_every_source_is_read_and_a_failed_one_keeps_its_last_names():
    calls = {"n": 0}

    async def music(_ctx):
        calls["n"] += 1
        if calls["n"] > 1:
            raise RuntimeError("Music Assistant did not answer")
        return {"artist": ["Example Band"], "playlist": ["Sunday Mix"]}

    async def books(_ctx):
        return {"title": ["Moon Garden"]}

    sources = {"music": music, "audiobooks": books}
    found, outcome = await stt_vocabulary.collect(object(), sources)
    assert found["artist"] == ["Example Band"] and found["title"] == ["Moon Garden"]
    assert outcome["music"] == "1 artist, 1 playlist"

    found, outcome = await stt_vocabulary.collect(object(), sources)
    assert outcome["music"].startswith("skipped: Music Assistant did not answer")
    assert found["artist"] == ["Example Band"] and found["playlist"] == ["Sunday Mix"]


async def test_a_refresh_builds_the_vocabulary_and_teaches_the_misheard_name_correction(monkeypatch):
    async def ctx():
        return object()

    async def music(_ctx):
        return {"artist": ["Example Band", "Other Band"]}

    async def people(_ctx):
        return {"person": ["Robin"]}

    monkeypatch.setattr(stt_vocabulary, "_household_ctx", ctx)
    vocab = await stt_vocabulary.refresh({"music": music, "people": people})
    assert vocab.names == ["Robin", "Example Band", "Other Band"]
    assert stt_vocabulary.current() is vocab
    # The most played first: the correction prefers it too.
    assert media_vocabulary.names("artist") == ["Example Band", "Other Band"]


async def test_without_household_credentials_the_previous_vocabulary_stays(monkeypatch):
    async def none():
        return None

    monkeypatch.setattr(stt_vocabulary, "_household_ctx", none)
    before = stt_vocabulary.current()
    assert await stt_vocabulary.refresh({}) is before


async def test_people_are_first_names_and_never_the_shared_accounts(monkeypatch):
    import aiohttp

    class _Resp:
        status = 200

        def raise_for_status(self):
            return None

        async def json(self):
            return [
                {"username": "household", "display_name": "Household"},
                {"username": "default", "display_name": "Default"},
                {"username": "robin", "display_name": "Robin Example"},
                {"username": "sam", "display_name": ""},
            ]

    class _Get:
        async def __aenter__(self):
            return _Resp()

        async def __aexit__(self, *a):
            return False

    class _Session:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        def get(self, *a, **k):
            return _Get()

    monkeypatch.setattr(aiohttp, "ClientSession", _Session)
    assert await stt_vocabulary.people_names(None) == {"person": ["Robin", "sam"]}


async def test_players_are_named_without_what_kind_of_device_they_are(monkeypatch):
    from services.execution import capability_index, ha_client

    class _Idx:
        def media_targets(self):
            return ["media_player.den_echo_media_player", "media_player.den_tv_cast"]

        def area_names(self):
            return ["Den"]

    async def load(*_a, **_k):
        return _Idx()

    async def states(*_a, **_k):
        return [
            {"entity_id": "media_player.den_echo_media_player", "attributes": {"friendly_name": "Den Echo Media Player"}},
            {"entity_id": "media_player.den_tv_cast", "attributes": {"friendly_name": "Den TV Cast"}},
        ]

    monkeypatch.setattr(capability_index, "load", load)
    monkeypatch.setattr(ha_client, "get_states", states)

    class _Ctx:
        ha_url = "http://hub.example.test"
        ha_token = "tok"

    assert await stt_vocabulary.place_names(_Ctx()) == {"room": ["Den"], "device": ["Den Echo", "Den TV"]}


async def test_the_book_library_offers_only_its_newest_titles(monkeypatch):
    from services.execution import media_finder
    from services.execution.handlers import calibre as calibre_handler

    async def source(_ctx):
        return media_finder.Source("calibre", "the book library", True)

    class _Client:
        async def list_books(self, *, limit):
            return [{"book_id": n, "title": f"Book {n}"} for n in range(1, 100)], 99

    monkeypatch.setattr(media_finder, "calibre_source", source)
    monkeypatch.setattr(calibre_handler, "_client", lambda: _Client())
    titles = (await stt_vocabulary.book_names(object()))["book"]
    assert len(titles) == stt_vocabulary.MAX_BOOKS
    assert titles[0] == "Book 99"


def test_execution_serves_the_vocabulary():
    from fastapi.testclient import TestClient

    from services.execution import main

    stt_vocabulary._current = stt_vocabulary.build({"room": ["Den"], "artist": ["Example Band"]})
    r = TestClient(main.app).get("/execute/voice/vocabulary", headers={"X-Internal-Secret": main.INTERNAL_SECRET})
    assert r.status_code == 200
    assert r.json()["names"] == ["Example Band"] and r.json()["rooms"] == ["Den"]


def test_what_was_just_played_goes_to_the_front_of_the_vocabulary():
    stt_vocabulary._current = stt_vocabulary.build({"artist": ["Example Band", "Other Band"]})
    stt_vocabulary.note_used("Moon Garden (Unabridged)", "A. Writer")
    stt_vocabulary.note_used("Other Band")
    assert stt_vocabulary.served()["names"] == ["Other Band", "Moon Garden", "A. Writer", "Example Band"]
    assert stt_vocabulary.current().names == ["Example Band", "Other Band"]  # the build itself is unchanged
