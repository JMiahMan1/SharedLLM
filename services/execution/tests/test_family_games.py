import os
import sys

sys.path.insert(0, os.path.abspath("."))

import pytest

from services.execution.handlers.family_games import (
    TRIVIA_QUESTIONS,
    GameRegistry,
    advance_trivia,
    apply_memory_flip,
    apply_trivia_answer,
    game_card,
    is_correct,
    memory_prompt,
    trivia_prompt,
)


@pytest.fixture(autouse=True)
def _clean_registry():
    reg = GameRegistry()
    reg.reset()
    yield reg


def test_is_correct_is_forgiving_about_articles_and_case():
    assert is_correct("Phoenix", ["phoenix"])
    assert is_correct("the phoenix", ["phoenix"])
    assert is_correct("6.", ["6", "six"])
    assert not is_correct("", ["phoenix"])
    assert not is_correct("chicago", ["phoenix"])


def test_trivia_awards_a_star_per_correct_answer():
    state = GameRegistry().start_trivia("room", question=TRIVIA_QUESTIONS[0])

    result = apply_trivia_answer(state, "Kiddo", "phoenix")
    assert result["correct"] is True
    assert result["stars"] == 1
    assert result["round_complete"] == "correct"

    # One guess each: a repeat from the same player is ignored, not scored.
    again = apply_trivia_answer(state, "Kiddo", "Nope")
    assert again["correct"] is False
    assert again.get("duplicate") is True
    assert again["stars"] == 1
    assert state.player("Kiddo").attempts == 1


def test_trivia_leaderboard_is_sorted_by_stars():
    state = GameRegistry().start_trivia("room", questions=TRIVIA_QUESTIONS, session_length=3)
    apply_trivia_answer(state, "A", state.answers[0])
    advance_trivia(state)
    apply_trivia_answer(state, "B", state.answers[0])
    advance_trivia(state)

    # B wins two questions to A's one; the board sorts by stars.
    board = apply_trivia_answer(state, "B", state.answers[0])["leaderboard"]
    assert board[0][0] == "B"
    assert board[0][1] == 2


def test_trivia_prompt_includes_the_question():
    state = GameRegistry().start_trivia("room", question=TRIVIA_QUESTIONS[0])
    prompt = trivia_prompt(state, hint=True)
    assert TRIVIA_QUESTIONS[0]["question"] in prompt
    assert "hint:" in prompt


def test_memory_starts_with_cards_face_down():
    state = GameRegistry().start_memory("room", pairs=2)
    assert len(state.board) == 4
    assert state.revealed == []
    assert "Memory" in memory_prompt(state)


def _numbers(*indexes: int) -> list[str]:
    """Cards are called by the number printed on them (1-based)."""
    return [str(i + 1) for i in indexes]


def test_memory_matching_a_pair_scores_and_keeps_it_up():
    state = GameRegistry().start_memory("room", pairs=2)
    first = state.board[0]
    partner = state.board.index(first, 1)

    result = apply_memory_flip(state, "Kiddo", _numbers(0, partner))
    assert result["matched"] is True
    assert result["stars"] == 1
    assert partner in state.revealed


def test_memory_miss_flips_the_wrong_cards_back():
    state = GameRegistry().start_memory("room", pairs=2)
    revealed_before = set(state.revealed)
    # Pick two cards from *different* pairs by content. The board is shuffled
    # (family_games.py random.shuffle), so board[1] and board[2] are sometimes
    # the same pair and the flip legitimately matches; this test used to fail
    # roughly one run in four.
    first = state.board[0]
    partner = state.board.index(first, 1)
    other = next(i for i, name in enumerate(state.board) if i not in (0, partner))
    wrong = _numbers(0, other)

    result = apply_memory_flip(state, "Kiddo", wrong)
    assert result["matched"] is False
    assert result["stars"] == 0
    assert set(state.revealed) == revealed_before


def test_memory_finishes_when_every_card_is_up():
    state = GameRegistry().start_memory("room", pairs=2)
    # Walk the board in *pair* order rather than index order: the board is
    # shuffled, so consecutive indices are not consecutive pairs and the game
    # could never complete.
    seen: set[str] = set()
    for index, name in enumerate(state.board):
        if name in seen:
            continue
        seen.add(name)
        apply_memory_flip(state, "A", _numbers(index, state.board.index(name, index + 1)))
    assert state.over is True


def test_memory_a_single_card_is_a_peek_that_flips_back():
    state = GameRegistry().start_memory("room", pairs=2)
    result = apply_memory_flip(state, "A", ["3"])
    assert result["flipped"] == [state.board[2]]
    assert state.revealed == []


def test_memory_rejects_unknown_cards_and_three_at_a_time():
    state = GameRegistry().start_memory("room", pairs=2)
    # A word, or a number off the board, is not a card.
    assert "by number" in apply_memory_flip(state, "A", ["unicorn"])["message"]
    assert "by number" in apply_memory_flip(state, "A", ["9"])["message"]
    # Only the first two cards called are flipped.
    assert len(apply_memory_flip(state, "A", ["1", "2", "3"])["flipped"]) == 2


def test_game_card_carries_the_leaderboard():
    state = GameRegistry().start_trivia("room", question=TRIVIA_QUESTIONS[0])
    apply_trivia_answer(state, "Kiddo", "phoenix")
    card = game_card(state, "Trivia winner", "Good work")
    assert card["kind"] == "game"
    assert card["stats"][0]["label"] == "Kiddo"
    assert "⭐" in card["stats"][0]["value"]


def test_registry_keeps_one_game_per_room():
    reg = GameRegistry()
    reg.start_trivia("room-1", question=TRIVIA_QUESTIONS[0])
    assert reg.get("room-1") is not None
    assert reg.get("room-2") is None
    reg.drop("room-1")
    assert reg.get("room-1") is None


def _tiered_pool():
    return [
        {"question": f"T{tier}Q{n}", "answers": [f"ans{tier}{n}"], "hint": "hint", "difficulty": tier}
        for tier in (1, 2, 3)
        for n in range(6)
    ]


def test_trivia_session_serves_ten_unique_questions_and_rises():
    state = GameRegistry().start_trivia(
        "room",
        questions=_tiered_pool(),
        roster={"dad": "dad", "kiddo": "kiddo"},
        roster_names={"dad": "Dad", "kiddo": "Kiddo"},
    )
    assert state.session_len == 10
    assert state.question.startswith("T1")

    seen: list[str] = []
    finished = False
    for _ in range(10):
        seen.append(state.question)
        # Someone who was not in the room when the session started is ignored.
        assert apply_trivia_answer(state, "stranger", "ans11")["ignored"] is True
        assert state.answered == set()
        result = apply_trivia_answer(state, "Kiddo", state.answers[0])
        assert result["round_complete"] == "correct"
        finished = advance_trivia(state)["finished"]

    assert finished is True
    assert state.over is True
    assert len(set(seen)) == 10  # no question repeats within a session
    tiers = [int(q[1]) for q in seen]
    assert tiers == sorted(tiers)  # the dial rises while they keep answering


def test_trivia_roster_aliases_map_to_one_player():
    state = GameRegistry().start_trivia(
        "room",
        question=TRIVIA_QUESTIONS[0],
        roster={"dad": "dad", "dad summers": "dad", "kiddo": "kiddo"},
    )
    apply_trivia_answer(state, "dad", "wrong")
    duplicate = apply_trivia_answer(state, "Dad Summers", "phoenix")
    assert duplicate.get("duplicate") is True
    assert duplicate["stars"] == 0


def test_trivia_all_wrong_closes_the_round():
    state = GameRegistry().start_trivia(
        "room", questions=_tiered_pool(), roster={"dad": "dad", "kiddo": "kiddo"}
    )
    first = apply_trivia_answer(state, "Dad", "definitely wrong")
    assert first["round_complete"] is None
    second = apply_trivia_answer(state, "Kiddo", "definitely wrong")
    assert second["round_complete"] == "all_wrong"


def test_trivia_round_expires_when_a_player_stays_quiet():
    state = GameRegistry().start_trivia(
        "room", questions=_tiered_pool(), roster={"dad": "dad", "kiddo": "kiddo"}
    )
    state.question_started_at -= 999
    result = apply_trivia_answer(state, "Dad", "definitely wrong")
    assert result["round_complete"] == "timeout"


def test_the_bible_bank_is_deep_and_every_listed_answer_counts():
    from services.execution.handlers import family_games as games

    qs = games.BIBLE_TRIVIA_QUESTIONS
    assert len(qs) >= 150
    assert len({q["question"] for q in qs}) == len(qs)
    assert sum(1 for q in qs if q["difficulty"] == 3) >= 50
    for q in qs:
        assert q["hint"] and q["difficulty"] in (1, 2, 3)
        for accepted in q["answers"]:
            assert games.is_correct(accepted, q["answers"]), (q["question"], accepted)


@pytest.mark.parametrize(
    ("given", "accepted", "expected"),
    [
        ("Psalm", ["Psalms"], True),  # Jeremiah's answer, refused before
        ("it was Moses", ["Moses"], True),
        ("King Saul", ["Saul"], True),
        ("Zaccheus", ["Zacchaeus"], True),
        ("Gethsemene", ["Gethsemane"], True),
        ("red", ["Red Sea", "red"], True),
        ("Jesus", ["jesus wept"], False),  # a fragment is not the answer
        ("the", ["the red sea"], False),
        ("Judah", ["Judas"], False),  # short names stay exact
        ("Paul", ["Saul"], False),
        ("Elijah", ["Elisha"], False),
        ("6", ["66"], False),
    ],
)
def test_answers_are_forgiving_but_not_loose(given, accepted, expected):
    from services.execution.handlers.family_games import is_correct

    assert is_correct(given, accepted) is expected
