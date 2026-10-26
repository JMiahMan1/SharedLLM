import asyncio
import os
import sys
from types import SimpleNamespace

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


@pytest.fixture(autouse=True)
def _quiet_activity(monkeypatch):
    """The activity recorder posts to telemetry; tests never reach the network."""
    from unittest.mock import AsyncMock

    monkeypatch.setattr("services.execution.activity.record", AsyncMock())


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
    assert any("question 1 of 10" in p["message"] for p in talk.posted)

    right = asyncio.run(
        talk.handle_talk(_request(game_command="answer", message="phoenix", card_detail="Kiddo"))
    )
    assert right.status in ("SUCCESS", "FAILURE")
    # Either the answer matched or the round moved on; never a crash.
    assert right.detail is not None or right.message


def test_bible_trivia_uses_bible_questions(talk, monkeypatch):
    monkeypatch.setenv("FAMILY_GAMES_ENABLED", "1")
    from services.execution.handlers import family_games as games

    start = asyncio.run(
        talk.handle_talk(_request(game_command="start", game_kind="bible"))
    )
    assert start.status == "SUCCESS"
    state = games.registry.get("room-1")
    assert state is not None
    assert state.question in {q["question"] for q in games.BIBLE_TRIVIA_QUESTIONS}
    assert any("question 1 of 10" in p["message"] for p in talk.posted)


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

    from unittest.mock import AsyncMock

    recorded = AsyncMock()
    monkeypatch.setattr("services.execution.activity.record", recorded)

    from services.execution.handlers import family_games as games

    state = games.registry.start_trivia("room-1")
    asyncio.run(
        talk.handle_talk(
            _request(
                game_command="answer",
                message=state.answers[0],
                # A decoy: the player is the verified caller now, so naming
                # somebody else in the body has to change nothing.
                card_detail="Somebody Else",
                user_context={"user": "Kiddo"},
            )
        )
    )

    assert banked and banked[0]["user"] == "Kiddo"
    assert banked[0]["stars"] == 1
    assert "Trivia" in banked[0]["reason"]
    recorded.assert_awaited_once()
    assert recorded.await_args.args[:2] == ("Kiddo", "game")
    assert recorded.await_args.kwargs["action"] == "win"


def test_star_banking_failure_never_breaks_a_game(talk, monkeypatch):
    monkeypatch.setenv("FAMILY_GAMES_ENABLED", "1")
    import asyncio

    async def boom(user, stars, reason):
        raise RuntimeError("geo is down")

    monkeypatch.setattr(talk, "_bank_game_stars", boom)

    from services.execution.handlers import family_games as games

    state = games.registry.start_trivia("room-1")
    result = asyncio.run(
        talk.handle_talk(
            _request(
                game_command="answer",
                message=state.answers[0],
                card_detail="Somebody Else",
                user_context={"user": "Kiddo"},
            )
        )
    )
    # The answer still counted in the game, whatever geo thinks.
    assert state.player("Kiddo").stars == 1
    assert result is not None


# _bank_game_stars, the real body
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

    assert len(calls) == 1, "geo was never called, so stars are silently not banked"
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


@pytest.mark.asyncio
async def test_a_chat_message_answers_the_running_trivia_game(talk, monkeypatch):
    monkeypatch.setenv("FAMILY_GAMES_ENABLED", "1")
    from services.execution.handlers import family_games as games

    await talk.handle_talk(_request(game_command="start", game_kind="trivia"))
    state = games.registry.get("room-1")
    assert state is not None
    answer = state.answers[0]

    result = await talk.handle_talk(_request(action="send", message=answer))

    assert result.status == "SUCCESS"
    if talk._GAME_TASKS:
        await asyncio.gather(*list(talk._GAME_TASKS))
    assert any("got it" in str(m.get("message", "")) for m in talk.posted)


@pytest.mark.asyncio
async def test_a_missed_round_reveals_its_own_answer_not_the_next_one(talk, monkeypatch):
    monkeypatch.setenv("FAMILY_GAMES_ENABLED", "1")
    from services.execution.handlers import family_games as games

    await talk.handle_talk(_request(game_command="start", game_kind="trivia"))
    state = games.registry.get("room-1")
    answer = state.answers[0]
    state.question_started_at = 1.0  # long expired: the next wrong guess closes the round

    await talk._apply_game_answer(None, "room-1", "Kiddo", "definitely not it")

    reveal = talk.posted[-1]["message"]
    assert reveal.startswith(f"The answer was {answer}.")
    assert "question 2 of" in reveal


@pytest.mark.asyncio
async def test_a_missed_last_question_still_ends_the_game(talk, monkeypatch):
    monkeypatch.setenv("FAMILY_GAMES_ENABLED", "1")
    from services.execution.handlers import family_games as games

    await talk.handle_talk(_request(game_command="start", game_kind="trivia"))
    state = games.registry.get("room-1")
    answer = state.answers[0]
    state.served = state.served + ["filler"] * (state.session_len - len(state.served))
    state.question_started_at = 1.0

    await talk._apply_game_answer(None, "room-1", "Kiddo", "definitely not it")

    final = talk.posted[-1]["message"]
    assert final.startswith(f"The answer was {answer}.")
    assert "🏆 Game over" in final
    assert state.over


@pytest.mark.asyncio
async def test_a_chat_message_is_left_alone_when_no_game_is_running(talk, monkeypatch):
    monkeypatch.setenv("FAMILY_GAMES_ENABLED", "1")

    result = await talk.handle_talk(_request(action="send", message="hello everyone"))

    assert result.status == "SUCCESS"
    assert result.service == "talk_send"
    assert talk.posted == [{"message": "hello everyone"}]


@pytest.mark.asyncio
async def test_a_late_answer_to_the_last_question_keeps_the_players_guess(talk, monkeypatch):
    monkeypatch.setenv("FAMILY_GAMES_ENABLED", "1")
    from services.execution.handlers import family_games as games

    await talk.handle_talk(_request(game_command="start", game_kind="trivia"))
    state = games.registry.get("room-1")
    first_answer = state.answers[0]
    await talk._apply_game_answer(None, "room-1", "Mom", first_answer)  # Mom wins round 1
    second_answer = state.answers[0]
    if games.is_correct(first_answer, state.answers):
        pytest.skip("the drawn questions share an answer")

    late = await talk._apply_game_answer(None, "room-1", "Kiddo", first_answer)

    assert late["result"].get("late") is True
    assert "last question's answer" in talk.posted[-1]["message"]
    assert "kiddo" not in state.answered
    right = await talk._apply_game_answer(None, "room-1", "Kiddo", second_answer)
    assert right["result"]["correct"] is True


@pytest.mark.asyncio
async def test_an_old_answer_long_after_the_question_changed_is_a_normal_guess(talk, monkeypatch):
    monkeypatch.setenv("FAMILY_GAMES_ENABLED", "1")
    from services.execution.handlers import family_games as games

    await talk.handle_talk(_request(game_command="start", game_kind="trivia"))
    state = games.registry.get("room-1")
    first_answer = state.answers[0]
    await talk._apply_game_answer(None, "room-1", "Mom", first_answer)
    if games.is_correct(first_answer, state.answers):
        pytest.skip("the drawn questions share an answer")
    state.question_started_at -= games.TRIVIA_LATE_ANSWER_SECONDS + 1

    stale = await talk._apply_game_answer(None, "room-1", "Kiddo", first_answer)

    assert not stale["result"].get("late")
    assert "kiddo" in state.answered


@pytest.mark.asyncio
async def test_a_win_lands_on_the_account_not_the_display_name(talk, monkeypatch):
    """The roster shows "Jeremiah"; the profile is the account "jeremiah"."""
    monkeypatch.setenv("FAMILY_GAMES_ENABLED", "1")
    from unittest.mock import AsyncMock

    from services.execution.handlers import family_games as games

    recorded = AsyncMock()
    monkeypatch.setattr("services.execution.activity.record", recorded)
    monkeypatch.setattr(talk, "_bank_game_stars", AsyncMock())
    state = games.registry.start_trivia("room-1")
    state.roster = {"jeremiah": "jeremiah"}
    state.roster_names = {"jeremiah": "Jeremiah"}

    await talk._apply_game_answer(None, "room-1", "jeremiah", state.answers[0])

    assert recorded.await_args.args[:2] == ("jeremiah", "game")
    assert recorded.await_args.kwargs["detail"] == {"player": "Jeremiah"}


@pytest.mark.asyncio
async def test_the_first_answer_to_arrive_wins_even_if_its_post_is_slower(talk, monkeypatch):
    """Jeremiah answered "Moses" first; Michele's later post finished first and took the round."""
    monkeypatch.setenv("FAMILY_GAMES_ENABLED", "1")
    from services.execution.handlers import family_games as games

    monkeypatch.setattr(
        talk, "resolve_personal_data_provider", lambda ctx: SimpleNamespace(username=ctx.user, password="x")
    )
    posted: list[tuple[str, str]] = []

    async def fake_request(provider, method, endpoint, **kwargs):
        if provider.username == "jeremiah" and (kwargs.get("data") or {}).get("message") == "Moses":
            await asyncio.sleep(0.2)  # his post to Nextcloud is the slow one
        posted.append((provider.username, (kwargs.get("data") or {}).get("message", "")))
        return True, {"id": len(posted)}, ""

    monkeypatch.setattr(talk, "_talk_request_with_retry", fake_request)
    state = games.registry.start_trivia("room-1")
    state.answers = ["Moses"]

    def send(user):
        from services.execution.schemas import TalkRequest

        return talk.handle_talk(TalkRequest(user_context={"user": user}, action="send", token="room-1", message="Moses"))

    await asyncio.gather(send("jeremiah"), send("michele"))
    await asyncio.gather(*list(talk._GAME_TASKS))

    verdicts = [m for _, m in posted if "got it" in m]
    assert len(verdicts) == 1 and verdicts[0].startswith("✅ jeremiah got it!")


@pytest.mark.asyncio
async def test_game_posts_are_marked_as_jarvis(talk, monkeypatch):
    monkeypatch.setenv("FAMILY_GAMES_ENABLED", "1")
    import json

    from services.execution.handlers import family_games as games

    await talk.handle_talk(_request(game_command="start", game_kind="trivia"))
    state = games.registry.get("room-1")
    await talk._apply_game_answer(None, "room-1", "Kiddo", state.answers[0])

    def envelope(message):
        body = message.split("```jarvis-envelope")[1]
        return json.loads(body)

    prompt, verdict = talk.posted[0]["message"], talk.posted[-1]["message"]
    assert envelope(prompt)["kind"] == "assistant"
    assert envelope(verdict)["kind"] == "game" and envelope(verdict)["from"] == "jarvis"
