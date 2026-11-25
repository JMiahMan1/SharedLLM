"""Requests heard wrong (over a TV speaker, or said quickly), and the reply log a voice test reads.

Titles are made up.
"""
from __future__ import annotations

import logging

import pytest

from services.gateway import fast_media, fast_read


@pytest.mark.parametrize(("said", "guesses"), [
    ("Late call to harbor.", ["call to harbor"]),
    ("Player one", ["one", "er one"]),
    ("Plate Moon Garden please", ["Moon Garden"]),
    ("Play Moon Garden", []),
    ("Late", []),
    ("Please turn on the lights", []),
    ("Lay down and rest a while because it is late in the evening now", []),
])
def test_play_heard_as_another_word_gives_guesses(said, guesses):
    assert fast_media.misheard_play(said) == guesses


def _main():
    from services.gateway import main
    return main


class _Creds:
    def model_dump(self):
        return {"user": "household"}


@pytest.mark.asyncio
async def test_a_misheard_play_is_played_only_when_the_library_clearly_has_it(monkeypatch):
    main = _main()
    asked: list[str] = []

    async def post(path, payload, timeout=120.0):
        asked.append(payload["query"])
        score = {"call to harbor": 0.95, "er one": 0.85}.get(payload["query"], 0.0)
        return {"status": "SUCCESS", "detail": {"action": "play" if score else "none", "score": score,
                                                "best": {"title": "x"} if score else None}}

    monkeypatch.setattr(main, "_execution_post", post)
    assert await main._misheard_play("Late call to harbor", _Creds()) == "Play call to harbor"
    assert await main._misheard_play("Player one", _Creds()) == "Play er one"
    assert asked == ["call to harbor", "one", "er one"]
    assert await main._misheard_play("Late for my meeting", _Creds()) is None
    assert await main._misheard_play("What time is it", _Creds()) is None


@pytest.mark.asyncio
async def test_a_weak_library_match_leaves_the_request_as_heard(monkeypatch):
    main = _main()

    async def post(path, payload, timeout=120.0):
        return {"status": "SUCCESS", "detail": {"action": "play", "score": 0.72, "best": {"title": "x"}}}

    monkeypatch.setattr(main, "_execution_post", post)
    assert await main._misheard_play("Late call to harbor", _Creds()) is None


@pytest.mark.parametrize(("said", "chapter", "title"), [
    ("Re-Chapter 1 of Moon Garden.", 1, "Moon Garden"),
    ("Red chapter two of Moon Garden", 2, "Moon Garden"),
])
def test_read_heard_as_re_or_red_before_a_chapter(said, chapter, title):
    ask = fast_read.parse(said)
    assert (ask.kind, ask.chapter, ask.title) == ("chapter", chapter, title)


@pytest.mark.parametrize("said", ["Red Dead Redemption", "Re-do the shopping list", "Read my messages"])
def test_other_re_words_are_not_reading(said):
    assert fast_read.parse(said) is None


def test_fast_replies_are_logged_in_one_short_line_and_personal_ones_are_not(caplog):
    main = _main()
    caplog.set_level(logging.INFO)
    main._log_reply("Playing Moon Garden,\nthe audiobook, on Den Speaker.", "play_media")
    main._log_reply("x" * 500, "read_aloud")
    main._log_reply("Sam is at the park.", "location_query")
    main._log_reply("Your week: ...", "guardian_recap")
    lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("[Reply]")]
    assert lines[0] == "[Reply] play_media: Playing Moon Garden, the audiobook, on Den Speaker."
    assert lines[1].startswith("[Reply] read_aloud: " + "x" * 200 + "... (500 characters)")
    assert len(lines) == 2
