"""
Family games over Talk.

A small, dependency-free game kit the Jarvis bot can run inside a conversation:
trivia and memory. Games are pure state machines so they can be unit tested
without a bot, a room, or a model.

Scoring is deliberately gentle: every correct answer is worth a star from a
small bank, so a kid playing badly still scores. A parent's job is to make the
other kids laugh, not to end up on a scoreboard.
"""

from __future__ import annotations

import os
import random
import re
import time
from dataclasses import dataclass, field
from typing import Any

FENCE = "```jarvis-envelope"

CARD_KINDS = ("game",)

# Kept small and warm on purpose: gentle content, no trick questions, and
# enough local colour to play at home. Extend by adding entries.
TRIVIA_QUESTIONS: list[dict[str, Any]] = [
    {
        "question": "What is the capital of Arizona?",
        "answers": ["Phoenix", "phoenix"],
        "hint": "It is named after a bird that rises from ashes.",
    },
    {
        "question": "How many days are in a leap year?",
        "answers": ["366", "three hundred sixty six"],
        "hint": "One more than a normal year.",
    },
    {
        "question": "What do you call a baby dog?",
        "answers": ["puppy", "pup", "a puppy"],
        "hint": "It rhymes with 'cuppy'.",
    },
    {
        "question": "Which planet is known as the Red Planet?",
        "answers": ["mars", "Mars"],
        "hint": "Named after the god of war.",
    },
    {
        "question": "What colour do you get when you mix blue and yellow?",
        "answers": ["green", "Green"],
        "hint": "The colour of grass.",
    },
    {
        "question": "How many minutes are in an hour?",
        "answers": ["60", "sixty"],
        "hint": "The same number of seconds in a minute.",
    },
    {
        "question": "What is the first book of the Bible?",
        "answers": ["Genesis", "genesis"],
        "hint": "It means 'origin'.",
    },
    {
        "question": "Which ocean is the largest?",
        "answers": ["pacific", "the pacific", "Pacific Ocean", "the pacific ocean"],
        "hint": "It is the biggest of the five.",
    },
    {
        "question": "How many sides does a hexagon have?",
        "answers": ["6", "six"],
        "hint": "The name says it: hex = six.",
    },
    {
        "question": "What is the freezing point of water in Celsius?",
        "answers": ["0", "zero", "0 degrees", "0 c"],
        "hint": "It is the number at the bottom of a thermometer.",
    },
]

MEMORY_WORDS = [
    "spaceship", "pancake", "rainbow", "treasure", "dinosaur", "moonwalk",
    "cupcake", "volcano", "penguin", "telescope", "blanket", "garden",
]


def normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", "", str(text or "").lower()).strip()


def is_correct(answer: str, accepted: list[str]) -> bool:
    given = normalize(answer)
    if not given:
        return False
    for option in accepted:
        wanted = normalize(option)
        if not wanted:
            continue
        if given == wanted or wanted in given.split() or given in wanted.split():
            return True
    return False


@dataclass
class Player:
    name: str
    stars: int = 0
    correct: int = 0
    attempts: int = 0


@dataclass
class GameState:
    """One running game in one room."""

    kind: str
    token: str
    question: str = ""
    answers: list[str] = field(default_factory=list)
    revealed: list[str] = field(default_factory=list)
    board: list[str] = field(default_factory=list)
    players: dict[str, Player] = field(default_factory=dict)
    round: int = 0
    started_at: float = field(default_factory=time.time)
    over: bool = False

    def player(self, name: str) -> Player:
        key = normalize(name) or "anon"
        if key not in self.players:
            self.players[key] = Player(name=str(name or "anon").strip() or "anon")
        return self.players[key]

    @property
    def leaderboard(self) -> list[Player]:
        return sorted(self.players.values(), key=lambda p: (-p.stars, -p.correct, p.name))


class GameRegistry:
    """In-memory games keyed by room token. One game per room at a time."""

    def __init__(self) -> None:
        self._games: dict[str, GameState] = {}

    def get(self, token: str) -> GameState | None:
        return self._games.get(token)

    def start_trivia(self, token: str, question: dict[str, Any] | None = None) -> GameState:
        picked = question or random.choice(TRIVIA_QUESTIONS)  # noqa: S311 - game content, not security
        state = GameState(
            kind="trivia",
            token=token,
            question=picked["question"],
            answers=list(picked["answers"]),
        )
        self._games[token] = state
        return state

    def start_memory(self, token: str, pairs: int = 4) -> GameState:
        count = max(2, min(int(pairs), 6))
        # Pick the words first, then duplicate them: sampling 2n distinct words
        # would produce a deck with no pairs at all.
        chosen = random.sample(MEMORY_WORDS, count)  # noqa: S311
        board = chosen + chosen
        random.shuffle(board)  # noqa: S311
        state = GameState(
            kind="memory",
            token=token,
            board=board,
            revealed=[0],
        )
        self._games[token] = state
        return state

    def drop(self, token: str) -> None:
        self._games.pop(token, None)

    def reset(self) -> None:
        self._games.clear()


registry = GameRegistry()


def trivia_prompt(state: GameState, hint: bool = False) -> str:
    lines = ["🧠 Trivia time", state.question]
    if hint:
        lines.append(f"(hint: {next((q['hint'] for q in TRIVIA_QUESTIONS if q['question'] == state.question), 'think!')})")
    lines.append("Reply with your answer — everyone plays at once.")
    return "\n".join(lines)


def memory_prompt(state: GameState) -> str:
    face_down = len(state.board) - len(state.revealed)
    return "\n".join(
        [
            "🃏 Memory: find the pairs",
            f"{len(state.board)} cards, {face_down} still face down.",
            "Say two words to flip them, e.g. 'spaceship moonwalk'.",
        ]
    )


def apply_trivia_answer(state: GameState, who: str, answer: str, star_value: int = 1) -> dict[str, Any]:
    player = state.player(who)
    player.attempts += 1
    correct = is_correct(answer, state.answers)
    if correct:
        player.correct += 1
        player.stars += star_value
    return {
        "correct": correct,
        "player": player.name,
        "stars": player.stars,
        "leaderboard": [(p.name, p.stars) for p in state.leaderboard],
    }


def apply_memory_flip(state: GameState, who: str, words: list[str]) -> dict[str, Any]:
    """Flip the named cards. A pair is kept face up, a miss flips back."""
    player = state.player(who)
    wanted = [normalize(w) for w in words]
    indexes = [i for i, word in enumerate(state.board) if normalize(word) in wanted]
    if not indexes:
        return {"flipped": [], "matched": False, "stars": player.stars, "message": "Those cards are not in the deck."}
    if len(indexes) > 2:
        return {"flipped": [], "matched": False, "stars": player.stars, "message": "Flip two cards at a time."}

    for i in indexes:
        if i not in state.revealed:
            state.revealed.append(i)

    flipped_words = [state.board[i] for i in indexes]
    if len(indexes) == 1:
        return {"flipped": flipped_words, "matched": False, "stars": player.stars, "message": f"{flipped_words[0]} — now pick its partner."}

    if state.board[indexes[0]] == state.board[indexes[1]]:
        player.stars += 1
        state.round += 1
        if len(state.revealed) == len(state.board):
            state.over = True
        return {
            "flipped": flipped_words,
            "matched": True,
            "stars": player.stars,
            "leaderboard": [(p.name, p.stars) for p in state.leaderboard],
            "message": f"Pair found! {flipped_words[0]} and {flipped_words[1]}.",
        }

    # Miss: hide the two wrong cards again, but keep the one that was already up.
    for i in indexes:
        if i not in state.revealed[: len(state.revealed) - 2]:
            state.revealed.remove(i)
    return {"flipped": flipped_words, "matched": False, "stars": player.stars, "message": "Not a pair — those flip back."}


def game_card(state: GameState, headline: str, detail: str | None = None) -> dict[str, Any]:
    """The envelope a game posts into the room."""
    card: dict[str, Any] = {"kind": "game", "title": headline[:120]}
    if detail:
        card["detail"] = detail[:200]
    if state.players:
        card["stats"] = [
            {"label": p.name, "value": f"{p.stars} ⭐"} for p in state.leaderboard[:5]
        ]
    return card


def encode_game_card(text: str, card: dict[str, Any]) -> str:
    return f"{text.strip()}\n\n{FENCE}\n{__import__('json').dumps(card)}\n{FENCE}"


def env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}
