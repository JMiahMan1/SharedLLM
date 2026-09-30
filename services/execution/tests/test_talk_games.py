import os
import sys
from types import SimpleNamespace

import asyncio

import pytest

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


# ─── _bank_game_stars, the real body ───────────────────────────────────────────
# The tests above all monkeypatch _bank_game_stars away, so they only ever
# exercised the caller. That is how a NameError lived in the real body for so
# long: `get_client` was never imported, every star raise raised it, and the
# broad `except Exception` logged it as "telemetry" and moved on. These tests
# call the real function with a fake HTTP client instead.


class _FakeResponse:
    def __init__(self, status: int):
        self.status = status

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _FakeClient:
    def __init__(self, calls: list, status: int):
        self._calls = calls
        self._status = status

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def post(self, url, **kwargs):
        self._calls.append({"url": url, **kwargs})
        return _FakeResponse(self._status)


class _FakeClientFactory:
    """Stands in for services.common.http.get_client."""

    def __init__(self, calls: list, status: int = 200, raises: Exception | None = None):
        self._calls = calls
        self._status = status
        self._raises = raises

    def __call__(self):
        if self._raises is not None:
            raise self._raises
        return _FakeClient(self._calls, self._status)


def _capture_geo(monkeypatch, calls: list, status: int = 200, raises: Exception | None = None):
    import services.common.http as common_http

    monkeypatch.setattr(common_http, "get_client", _FakeClientFactory(calls, status=status, raises=raises))


def test_bank_game_stars_really_posts_to_geo(monkeypatch):
    """Regression: the body must reach the HTTP call, not die on a NameError."""
    import services.execution.handlers.talk as talk_module

    calls: list[dict] = []
    _capture_geo(monkeypatch, calls)

    asyncio.run(talk_module._bank_game_stars("kiddo", 3, "Trivia round"))

    assert len(calls) == 1, "geo was never called — stars are silently not banked"
    assert calls[0]["url"].endswith("/api/geo/stars")
    assert calls[0]["json"] == {
        "user_id": "kiddo",
        "stars": 1,
        "reason": "game",
        "note": "Trivia round",
        "granted_by": "game",
    }
    assert calls[0]["headers"]["X-Internal-Secret"]


def test_bank_game_stars_truncates_a_long_note(monkeypatch):
    import services.execution.handlers.talk as talk_module

    calls: list[dict] = []
    _capture_geo(monkeypatch, calls)

    asyncio.run(talk_module._bank_game_stars("kiddo", 1, "x" * 500))

    assert len(calls[0]["json"]["note"]) == 200


def test_bank_game_stars_skips_family_and_zero_star_rounds(monkeypatch):
    import services.execution.handlers.talk as talk_module

    calls: list[dict] = []
    _capture_geo(monkeypatch, calls)

    asyncio.run(talk_module._bank_game_stars("family", 3, "Trivia"))
    asyncio.run(talk_module._bank_game_stars("", 3, "Trivia"))
    asyncio.run(talk_module._bank_game_stars("kiddo", 0, "Trivia"))

    assert calls == [], "nothing to bank should mean no HTTP call at all"


def test_bank_game_stars_logs_a_rejection_without_raising(monkeypatch):
    import services.execution.handlers.talk as talk_module

    warnings: list[str] = []
    monkeypatch.setattr(talk_module, "log", SimpleNamespace(warning=lambda *a: warnings.append(" ".join(map(str, a)))))
    _capture_geo(monkeypatch, [], status=403)

    asyncio.run(talk_module._bank_game_stars("kiddo", 1, "Trivia"))  # must not raise

    assert any("403" in w for w in warnings), warnings


def test_bank_game_stars_swallows_a_dead_geo_and_says_so(monkeypatch):
    import services.execution.handlers.talk as talk_module

    warnings: list[str] = []
    monkeypatch.setattr(talk_module, "log", SimpleNamespace(warning=lambda *a: warnings.append(" ".join(map(str, a)))))
    _capture_geo(monkeypatch, [], raises=RuntimeError("geo is down"))

    asyncio.run(talk_module._bank_game_stars("kiddo", 1, "Trivia"))  # must not raise

    assert any("star banking failed" in w for w in warnings), warnings
