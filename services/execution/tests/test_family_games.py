import os
import sys

sys.path.insert(0, os.path.abspath("."))

import pytest

from services.execution.handlers.family_games import (
    MEMORY_WORDS,
    TRIVIA_QUESTIONS,
    GameRegistry,
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

    again = apply_trivia_answer(state, "Kiddo", "Nope")
    assert again["correct"] is False
    assert again["stars"] == 1
    assert state.player("Kiddo").attempts == 2


def test_trivia_leaderboard_is_sorted_by_stars():
    state = GameRegistry().start_trivia("room", question=TRIVIA_QUESTIONS[0])
    apply_trivia_answer(state, "A", "phoenix")
    apply_trivia_answer(state, "B", "phoenix")
    apply_trivia_answer(state, "B", "phoenix")

    board = apply_trivia_answer(state, "C", "phoenix")["leaderboard"]
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
    assert len(state.revealed) == 1
    assert "Memory" in memory_prompt(state)


def test_memory_matching_a_pair_scores_and_keeps_it_up():
    state = GameRegistry().start_memory("room", pairs=2)
    first = state.board[0]
    partner = state.board.index(first, 1)

    result = apply_memory_flip(state, "Kiddo", [first, state.board[partner]])
    assert result["matched"] is True
    assert result["stars"] == 1
    assert partner in state.revealed


def test_memory_miss_flips_the_wrong_cards_back():
    state = GameRegistry().start_memory("room", pairs=2)
    revealed_before = set(state.revealed)
    # Pick two cards from *different* pairs by content. The board is shuffled
    # (family_games.py random.shuffle), so board[1] and board[2] are sometimes
    # the same pair and the flip legitimately matches — this test used to fail
    # roughly one run in four.
    first = state.board[0]
    partner = state.board.index(first, 1)
    other = next(i for i, name in enumerate(state.board) if i not in (0, partner))
    wrong = [first, state.board[other]]

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
    pairs = []
    for name in state.board:
        if name not in seen:
            seen.add(name)
            pairs.append(name)
    for name in pairs:
        apply_memory_flip(state, "A", [name, state.board[state.board.index(name, 1)]])
    assert state.over is True


def test_memory_rejects_unknown_cards_and_three_at_a_time():
    state = GameRegistry().start_memory("room", pairs=2)
    assert "not in the deck" in apply_memory_flip(state, "A", ["unicorn"])["message"]
    assert "two cards" in apply_memory_flip(state, "A", state.board[:3])["message"]


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
