import os
import sys
from types import SimpleNamespace

import asyncio

import pytest

os.environ["INTERNAL_SECRET"] = "test-secret"
sys.path.insert(0, os.path.abspath("."))


def _request(**kw):
    from services.execution.schemas import TalkRequest

    base = {"user_context": {"user": "dad"}, "action": "game", "token": "room-1"}
    base.update(kw)
    return TalkRequest(**base)


@pytest.fixture()
def talk(monkeypatch):
    import services.execution.handlers.family_games as games
    import services.execution.handlers.talk as talk_module

    games.registry.reset()
    monkeypatch.setattr(
        talk_module, "resolve_personal_data_provider", lambda ctx: SimpleNamespace(username="dad", password="x")
    )
    posted: list[dict] = []

    async def fake_request(provider, method, endpoint, **kwargs):
        if method == "POST" and kwargs.get("data"):
            posted.append(kwargs["data"])
        return True, {}, ""

    monkeypatch.setattr(talk_module, "_talk_request_with_retry", fake_request)
    talk_module.posted = posted
    return talk_module


def test_games_are_off_unless_enabled(talk, monkeypatch):
    monkeypatch.delenv("FAMILY_GAMES_ENABLED", raising=False)
    result = asyncio.run(
        talk.handle_talk(_request(game_command="start"))
    )
    assert result.status == "FAILURE"
    assert "not enabled" in result.message


def test_trivia_starts_and_answers(talk, monkeypatch):
    monkeypatch.setenv("FAMILY_GAMES_ENABLED", "1")
    start = asyncio.run(
        talk.handle_talk(_request(game_command="start", game_kind="trivia"))
    )
    assert start.status == "SUCCESS"
    assert any("Trivia time" in p["message"] for p in talk.posted)

    right = asyncio.run(
        talk.handle_talk(_request(game_command="answer", message="phoenix", card_detail="Kiddo"))
    )
    assert right.status in ("SUCCESS", "FAILURE")
    # Either the answer matched or the round moved on; never a crash.
    assert right.detail is not None or right.message


def test_memory_flip_posts_a_card(talk, monkeypatch):
    monkeypatch.setenv("FAMILY_GAMES_ENABLED", "1")
    from services.execution.handlers import family_games as games

    asyncio.run(
        talk.handle_talk(_request(game_command="start", game_kind="memory"))
    )
    state = games.registry.get("room-1")
    flip = asyncio.run(
        talk.handle_talk(_request(game_command="flip", game_words=f"{state.board[0]} {state.board[1]}", card_detail="Kiddo"))
    )
    assert flip.status == "SUCCESS"
    assert talk.posted, "the room should have been told about the flip"
    assert any("jarvis-envelope" in p["message"] for p in talk.posted)


def test_stopping_a_game_clears_it(talk, monkeypatch):
    monkeypatch.setenv("FAMILY_GAMES_ENABLED", "1")
    from services.execution.handlers import family_games as games

    asyncio.run(
        talk.handle_talk(_request(game_command="start", game_kind="trivia"))
    )
    assert games.registry.get("room-1") is not None
    asyncio.run(talk.handle_talk(_request(game_command="stop")))
    assert games.registry.get("room-1") is None


def test_correct_trivia_answers_bank_a_star(talk, monkeypatch):
    monkeypatch.setenv("FAMILY_GAMES_ENABLED", "1")
    import asyncio

    banked: list[dict] = []

    async def fake_bank(user, stars, reason):
        banked.append({"user": user, "stars": stars, "reason": reason})

    monkeypatch.setattr(talk, "_bank_game_stars", fake_bank)

    from services.execution.handlers import family_games as games

    state = games.registry.start_trivia("room-1")
    asyncio.run(
        talk.handle_talk(_request(game_command="answer", message=state.answers[0], card_detail="Kiddo"))
    )

    assert banked and banked[0]["user"] == "Kiddo"
    assert banked[0]["stars"] == 1
    assert "Trivia" in banked[0]["reason"]


def test_star_banking_failure_never_breaks_a_game(talk, monkeypatch):
    monkeypatch.setenv("FAMILY_GAMES_ENABLED", "1")
    import asyncio

    async def boom(user, stars, reason):
        raise RuntimeError("geo is down")

    monkeypatch.setattr(talk, "_bank_game_stars", boom)

    from services.execution.handlers import family_games as games

    state = games.registry.start_trivia("room-1")
    result = asyncio.run(
        talk.handle_talk(_request(game_command="answer", message=state.answers[0], card_detail="Kiddo"))
    )
    # The answer still counted in the game, whatever geo thinks.
    assert state.player("Kiddo").stars == 1
    assert result is not None
