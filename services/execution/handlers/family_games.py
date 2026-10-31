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

# Trivia sessions: a room plays a fixed number of questions, easy first, and
# the dial moves with the room - rounds it answers correctly raise it, a round
# that closes without a correct answer lowers it. A round also expires so one
# quiet player cannot stall the whole session.
SESSION_LENGTH = 10
TRIVIA_ROUND_SECONDS = 120
# How long after a new question an answer to the one before it is still taken
# as late, not as a guess: players see the next question seconds apart, and a
# kid typing the last answer should not lose their turn on the new one.
TRIVIA_LATE_ANSWER_SECONDS = 20

# Kept small and warm on purpose: gentle content, no trick questions, and
# enough local colour to play at home. Extend by adding entries.
TRIVIA_QUESTIONS: list[dict[str, Any]] = [
    {
        "question": "What is the capital of Arizona?",
        "answers": ["Phoenix", "phoenix"],
        "hint": "It is named after a bird that rises from ashes.",
        "difficulty": 1,
    },
    {
        "question": "How many days are in a leap year?",
        "answers": ["366", "three hundred sixty six"],
        "hint": "One more than a normal year.",
        "difficulty": 1,
    },
    {
        "question": "What do you call a baby dog?",
        "answers": ["puppy", "pup", "a puppy"],
        "hint": "It rhymes with 'cuppy'.",
        "difficulty": 1,
    },
    {
        "question": "Which planet is known as the Red Planet?",
        "answers": ["mars", "Mars"],
        "hint": "Named after the god of war.",
        "difficulty": 1,
    },
    {
        "question": "What colour do you get when you mix blue and yellow?",
        "answers": ["green", "Green"],
        "hint": "The colour of grass.",
        "difficulty": 1,
    },
    {
        "question": "How many minutes are in an hour?",
        "answers": ["60", "sixty"],
        "hint": "The same number of seconds in a minute.",
        "difficulty": 1,
    },
    {
        "question": "What is the first book of the Bible?",
        "answers": ["Genesis", "genesis"],
        "hint": "It means 'origin'.",
        "difficulty": 1,
    },
    {
        "question": "Which ocean is the largest?",
        "answers": ["pacific", "the pacific", "Pacific Ocean", "the pacific ocean"],
        "hint": "It is the biggest of the five.",
        "difficulty": 1,
    },
    {
        "question": "How many sides does a hexagon have?",
        "answers": ["6", "six"],
        "hint": "The name says it: hex = six.",
        "difficulty": 1,
    },
    {
        "question": "What is the freezing point of water in Celsius?",
        "answers": ["0", "zero", "0 degrees", "0 c"],
        "hint": "It is the number at the bottom of a thermometer.",
        "difficulty": 1,
    },
    {
        "question": "How many continents are there on Earth?",
        "answers": ["7", "seven"],
        "hint": "One for each colour of the rainbow.",
        "difficulty": 2,
    },
    {
        "question": "What do bees make?",
        "answers": ["honey"],
        "hint": "It goes in tea.",
        "difficulty": 2,
    },
    {
        "question": "Which month gets an extra day in a leap year?",
        "answers": ["february", "feb"],
        "hint": "The shortest month.",
        "difficulty": 2,
    },
    {
        "question": "What is the largest planet in our solar system?",
        "answers": ["jupiter"],
        "hint": "It has a giant red spot.",
        "difficulty": 2,
    },
    {
        "question": "How many legs does a spider have?",
        "answers": ["8", "eight"],
        "hint": "Two fewer than ten.",
        "difficulty": 2,
    },
    {
        "question": "Which animal is called the King of the Jungle?",
        "answers": ["lion", "the lion"],
        "hint": "He has a big mane.",
        "difficulty": 2,
    },
    {
        "question": "What do you call ice pellets that fall from clouds?",
        "answers": ["hail", "hailstones"],
        "hint": "Not snow, not rain.",
        "difficulty": 2,
    },
    {
        "question": "What is the capital of France?",
        "answers": ["paris"],
        "hint": "The Eiffel Tower lives there.",
        "difficulty": 3,
    },
    {
        "question": "How many players from one team are on the field in a soccer game?",
        "answers": ["11", "eleven"],
        "hint": "One goalkeeper plus ten.",
        "difficulty": 3,
    },
    {
        "question": "Who painted the Mona Lisa?",
        "answers": ["leonardo da vinci", "da vinci", "leonardo"],
        "hint": "He was also an inventor.",
        "difficulty": 3,
    },
    {
        "question": "What is the hardest natural substance?",
        "answers": ["diamond"],
        "hint": "It sparkles on rings.",
        "difficulty": 3,
    },
    {
        "question": "What is the longest river in Africa?",
        "answers": ["the nile", "nile"],
        "hint": "It flows through Egypt.",
        "difficulty": 3,
    },
]

BIBLE_TRIVIA_QUESTIONS: list[dict[str, Any]] = [
    {
        "question": "Who built the ark?",
        "answers": ["Noah", "noah"],
        "hint": "He also sent out a dove.",
        "difficulty": 1,
    },
    {
        "question": "What sea did Moses lead the Israelites across?",
        "answers": ["Red Sea", "the red sea", "red"],
        "hint": "Its name is a colour.",
        "difficulty": 1,
    },
    {
        "question": "How many disciples did Jesus choose?",
        "answers": ["12", "twelve"],
        "hint": "The same number as the tribes of Israel.",
        "difficulty": 2,
    },
    {
        "question": "What is the first book of the New Testament?",
        "answers": ["Matthew", "matthew"],
        "hint": "It opens with a genealogy of Jesus.",
        "difficulty": 2,
    },
    {
        "question": "Who was swallowed by a great fish?",
        "answers": ["Jonah", "jonah"],
        "hint": "He ran from Nineveh.",
        "difficulty": 1,
    },
    {
        "question": "In which town was Jesus born?",
        "answers": ["Bethlehem", "bethlehem"],
        "hint": "The city of David.",
        "difficulty": 1,
    },
    {
        "question": "Which disciple betrayed Jesus?",
        "answers": ["Judas", "judas", "Judas Iscariot"],
        "hint": "His surname was Iscariot.",
        "difficulty": 1,
    },
    {
        "question": "What did David use to face Goliath?",
        "answers": ["sling", "a sling", "sling and stone", "sling and a stone", "stone"],
        "hint": "A shepherd's weapon.",
        "difficulty": 1,
    },
    {
        "question": "What is the longest book of the Bible?",
        "answers": ["Psalms", "psalms", "Psalm"],
        "hint": "It is a book of songs and prayers.",
        "difficulty": 2,
    },
    {
        "question": "Who was the first man?",
        "answers": ["Adam", "adam"],
        "hint": "Eve was made from his rib.",
        "difficulty": 1,
    },
    {
        "question": "Who was thrown into the lions' den?",
        "answers": ["Daniel", "daniel"],
        "hint": "He kept praying even when it was against the law.",
        "difficulty": 2,
    },
    {
        "question": "Who led the Israelites out of Egypt?",
        "answers": ["Moses", "moses"],
        "hint": "He talked with God at the burning bush.",
        "difficulty": 2,
    },
    {
        "question": "Who was the first king of Israel?",
        "answers": ["Saul", "saul", "King Saul"],
        "hint": "He came before King David.",
        "difficulty": 2,
    },
    {
        "question": "What did God make on the first day?",
        "answers": ["light"],
        "hint": "Let there be...",
        "difficulty": 2,
    },
    {
        "question": "How many books are in the Bible?",
        "answers": ["66", "sixty six"],
        "hint": "39 in the Old Testament plus 27 in the New.",
        "difficulty": 3,
    },
    {
        "question": "Who wrote many of the letters in the New Testament?",
        "answers": ["Paul", "paul", "the apostle paul", "saint paul"],
        "hint": "He used to be called Saul.",
        "difficulty": 3,
    },
    {
        "question": "What is the shortest verse in the Bible?",
        "answers": ["jesus wept", "jesus cried"],
        "hint": "It is in the book of John.",
        "difficulty": 3,
    },
]

# The starter set above plus the reviewed bank (bible_trivia_bank.py): 17
# questions meant every second ten-question game repeated the last one.
from services.execution.handlers.bible_trivia_bank import bank as _bible_bank  # noqa: E402

BIBLE_TRIVIA_QUESTIONS += [
    q for q in _bible_bank() if q["question"] not in {b["question"] for b in BIBLE_TRIVIA_QUESTIONS}
]

MEMORY_WORDS = [
    "spaceship", "pancake", "rainbow", "treasure", "dinosaur", "moonwalk",
    "cupcake", "volcano", "penguin", "telescope", "blanket", "garden",
]


def normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", "", str(text or "").lower()).strip()


# Words an answer may add or drop without changing it: "the Red Sea" is "Red
# Sea", "King Saul" is "Saul", "Mount Ararat" is "Ararat".
_FILLER_WORDS = frozenset({
    "a", "an", "the", "of", "and", "to", "in", "on", "it", "was", "is", "its", "he", "she",
    "king", "queen", "saint", "st", "apostle", "prophet", "mount", "mt", "book", "city",
})


def _answer_words(text: str) -> list[str]:
    """Content words, singular: "Psalm" and "Psalms" are one answer."""
    words = [w for w in normalize(text).split() if w not in _FILLER_WORDS]
    return [w[:-1] if len(w) > 3 and w.endswith("s") else w for w in words]


def _edit_distance(a: str, b: str) -> int:
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        current = [i]
        for j, cb in enumerate(b, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (ca != cb)))
        previous = current
    return previous[-1]


def _same_word(given: str, wanted: str) -> bool:
    """Equal, or one slip in a long name ("Zaccheus", "Gethsemene"). Short
    names stay exact so that Judah never passes for Judas or Saul for Paul."""
    if given == wanted:
        return True
    if len(wanted) >= 10:
        return _edit_distance(given, wanted) <= 2
    if len(wanted) >= 6:
        return _edit_distance(given, wanted) <= 1
    return False


def is_correct(answer: str, accepted: list[str]) -> bool:
    """Every content word of one accepted answer appears in the reply.

    "it was Moses" and "the red sea" count; a fragment does not -- "Jesus" is
    not "Jesus wept", and "the" alone matches nothing. Short forms a kid may
    give ("red") are listed as accepted answers of their own.
    """
    given = _answer_words(answer)
    if not given:
        return False
    for option in accepted:
        wanted = _answer_words(option)
        if wanted and all(any(_same_word(g, w) for g in given) for w in wanted):
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

    # Trivia session state (empty for memory games). `served` lists the text of
    # every question already asked this session; `dial` is the current
    # difficulty (it steps up every three correct answers); `roster` maps
    # normalized aliases to a canonical key and `roster_names` back to a
    # display name (empty roster = an open room).
    pool: list[dict[str, Any]] = field(default_factory=list)
    served: list[str] = field(default_factory=list)
    dial: int = 1
    session_len: int = 0
    question_number: int = 0
    roster: dict[str, str] = field(default_factory=dict)
    roster_names: dict[str, str] = field(default_factory=dict)
    answered: set[str] = field(default_factory=set)
    last_correct: bool | None = None
    correct_count: int = 0
    question_started_at: float = 0.0
    previous_answers: list[str] = field(default_factory=list)

    def player(self, name: str) -> Player:
        key = normalize(name) or "anon"
        if key not in self.players:
            self.players[key] = Player(name=str(name or "anon").strip() or "anon")
        return self.players[key]

    def canonical(self, who: str) -> str | None:
        """Map a speaker to their session key. None = not in the roster.

        Without a roster (a direct or hand-started game) anyone who answers
        joins the game, which is how the unit tests drive single questions.
        """
        if not self.roster:
            return normalize(who) or "anon"
        key = normalize(who)
        if key in self.roster:
            return self.roster[key]
        return None

    def session_player(self, key: str, fallback: str = "") -> Player:
        name = self.roster_names.get(key) or str(fallback or "").strip() or key
        if key not in self.players:
            self.players[key] = Player(name=name)
        return self.players[key]

    @property
    def roster_keys(self) -> set[str]:
        return set(self.roster.values())

    @property
    def leaderboard(self) -> list[Player]:
        return sorted(self.players.values(), key=lambda p: (-p.stars, -p.correct, p.name))


def draw_trivia_question(state: GameState) -> None:
    """Ask the next unserved question nearest the current difficulty dial."""
    remaining = [q for q in state.pool if q["question"] not in state.served]
    if not remaining:
        return
    near: list[dict[str, Any]] = []
    for distance in range(4):
        tiers = {state.dial - distance, state.dial + distance}
        near = [q for q in remaining if int(q.get("difficulty", 1)) in tiers]
        if near:
            break
    picked = random.choice(near)  # game content, not security
    state.previous_answers = list(state.answers)
    state.question = picked["question"]
    state.answers = list(picked["answers"])
    state.served.append(picked["question"])
    state.question_number = len(state.served)
    state.answered = set()
    state.last_correct = None
    state.question_started_at = time.time()


def advance_trivia(state: GameState) -> dict[str, Any]:
    """Close the current question: move the dial, then ask the next one.

    Returns ``{"finished": True, ...}`` after the last question instead.
    """
    # The dial follows overall performance: every three correct answers the
    # room moves up a level; missed questions never push it back down, so a
    # struggling kid never sees the game slide away from them.
    state.dial = min(3, 1 + state.correct_count // 3)
    if len(state.served) >= state.session_len:
        state.over = True
        state.question = ""
        state.answers = []
        return {
            "finished": True,
            "leaderboard": [(p.name, p.stars) for p in state.leaderboard],
        }
    draw_trivia_question(state)
    return {
        "finished": False,
        "question": state.question,
        "number": state.question_number,
        "leaderboard": [(p.name, p.stars) for p in state.leaderboard],
    }


class GameRegistry:
    """In-memory games keyed by room token. One game per room at a time."""

    def __init__(self) -> None:
        self._games: dict[str, GameState] = {}

    def get(self, token: str) -> GameState | None:
        return self._games.get(token)

    def start_trivia(
        self,
        token: str,
        question: dict[str, Any] | None = None,
        questions: list[dict[str, Any]] | None = None,
        roster: dict[str, str] | None = None,
        roster_names: dict[str, str] | None = None,
        session_length: int | None = None,
    ) -> GameState:
        """Start a session. `roster` locks the players - only aliases listed in
        it (normalized alias -> canonical key) can answer. Without a roster the
        room stays open and anyone who answers joins, which direct unit tests
        and hand-started games use."""
        pool = list(questions or TRIVIA_QUESTIONS)
        state = GameState(
            kind="trivia",
            token=token,
            pool=pool,
            roster=dict(roster or {}),
            roster_names=dict(roster_names or {}),
            session_len=min(session_length or SESSION_LENGTH, len(pool)),
        )
        if question is not None:
            # A single focused question (legacy start and unit tests).
            state.question = question["question"]
            state.answers = list(question["answers"])
            state.served = [state.question]
            state.question_number = 1
            state.session_len = 1
            state.question_started_at = time.time()
        else:
            draw_trivia_question(state)
        self._games[token] = state
        return state

    def start_memory(self, token: str, pairs: int = 4) -> GameState:
        count = max(2, min(int(pairs), 6))
        # Pick the words first, then duplicate them: sampling 2n distinct words
        # would produce a deck with no pairs at all.
        chosen = random.sample(MEMORY_WORDS, count)
        board = chosen + chosen
        random.shuffle(board)
        state = GameState(
            kind="memory",
            token=token,
            board=board,
            # Every card starts face down. A first card shown face up could
            # never be matched (a flip takes two face-down cards), so the game
            # could not finish.
            revealed=[],
        )
        self._games[token] = state
        return state

    def drop(self, token: str) -> None:
        self._games.pop(token, None)

    def reset(self) -> None:
        self._games.clear()


registry = GameRegistry()


def trivia_prompt(state: GameState, hint: bool = False) -> str:
    if state.session_len > 1 and state.question_number:
        title = f"🧠 Trivia - question {state.question_number} of {state.session_len}"
    else:
        title = "🧠 Trivia time"
    lines = [title, state.question]
    if hint:
        hint_text = next(
            (
                q["hint"]
                for q in (*TRIVIA_QUESTIONS, *BIBLE_TRIVIA_QUESTIONS)
                if q["question"] == state.question
            ),
            "think!",
        )
        lines.append(f"(hint: {hint_text})")
    lines.append("Reply with your answer. Everyone plays at once.")
    return "\n".join(lines)


def _memory_board_line(state: GameState) -> str:
    """The board as the room sees it: the word where a card is face up, its
    number where it is still hidden. Without this a player cannot know which
    card to turn, which is what made the game unplayable in chat."""
    return "  ".join(
        f"{i + 1}:{word}" if i in state.revealed else f"{i + 1}:?"
        for i, word in enumerate(state.board)
    )


def memory_board_lines(state: GameState) -> list[str]:
    """The board as the room sees it: a face-down card shows its number, a
    card that is up shows its word. Without the numbers there is no way to say
    which card to flip - which is the whole game."""
    cells = [
        f"{i + 1}. {word}" if i in state.revealed else f"{i + 1}. ?"
        for i, word in enumerate(state.board)
    ]
    return ["   ".join(cells[i : i + 4]) for i in range(0, len(cells), 4)]


def memory_prompt(state: GameState) -> str:
    face_down = len(state.board) - len(state.revealed)
    return "\n".join(
        [
            "🃏 Memory: find the pairs",
            f"{len(state.board)} cards, {face_down} still face down.",
            _memory_board_line(state),
            "Say two card numbers to flip them, e.g. '3 5'.",
        ]
    )


def apply_trivia_answer(state: GameState, who: str, answer: str, star_value: int = 1) -> dict[str, Any]:
    """Score one answer. A round closes on the first correct answer, when every
    enlisted player has answered wrong, or when the question has expired."""
    if state.over:
        # A finished session: stragglers are ignored, and nothing may touch the
        # cleared question state below.
        return {
            "ignored": True,
            "correct": False,
            "player": None,
            "stars": None,
            "round_complete": None,
            "leaderboard": [(p.name, p.stars) for p in state.leaderboard],
        }
    key = state.canonical(who)
    if key is None:
        # Not in the roster that started this session: ignore silently.
        return {
            "ignored": True,
            "correct": False,
            "player": None,
            "stars": None,
            "round_complete": None,
            "leaderboard": [(p.name, p.stars) for p in state.leaderboard],
        }
    if key in state.answered:
        player = state.players.get(key)
        return {
            "duplicate": True,
            "correct": False,
            "player": player.name if player else key,
            "stars": player.stars if player else 0,
            "round_complete": None,
            "leaderboard": [(p.name, p.stars) for p in state.leaderboard],
        }
    correct = is_correct(answer, state.answers)
    if (
        not correct
        and state.previous_answers
        and time.time() - state.question_started_at < TRIVIA_LATE_ANSWER_SECONDS
        and is_correct(answer, state.previous_answers)
    ):
        # Meant for the question that just closed: it neither scores nor
        # uses up this player's one guess on the new question.
        player = state.session_player(key, fallback=who)
        return {
            "late": True,
            "correct": False,
            "player": player.name,
            "stars": player.stars,
            "round_complete": None,
            "leaderboard": [(p.name, p.stars) for p in state.leaderboard],
        }
    player = state.session_player(key, fallback=who)
    state.answered.add(key)
    player.attempts += 1
    complete: str | None = None
    if correct:
        player.correct += 1
        player.stars += star_value
        state.last_correct = True
        state.correct_count += 1
        complete = "correct"
    else:
        state.last_correct = False
        waiting = state.roster_keys if state.roster else set(state.players)
        if waiting and waiting <= state.answered and (state.roster or len(waiting) > 1):
            complete = "all_wrong"
        elif (
            state.question_started_at
            and time.time() - state.question_started_at > TRIVIA_ROUND_SECONDS
        ):
            complete = "timeout"
    return {
        "correct": correct,
        "player": player.name,
        "stars": player.stars,
        "round_complete": complete,
        "leaderboard": [(p.name, p.stars) for p in state.leaderboard],
    }


def _memory_pick(state: GameState, token: str) -> int | None:
    """The card a token names. Players type the number printed on the card
    ("3", "card 3"), so a token with no card number in it is ignored."""
    digits = re.sub(r"[^0-9]", "", str(token or ""))
    if not digits:
        return None
    index = int(digits) - 1
    return index if 0 <= index < len(state.board) else None


def apply_memory_flip(state: GameState, who: str, words: list[str]) -> dict[str, Any]:
    """Flip two numbered cards. A pair is kept face up, a miss flips back."""
    player = state.player(who)
    picks: list[int] = []
    for token in words:
        index = _memory_pick(state, token)
        if index is not None and index not in picks:
            picks.append(index)
        if len(picks) == 2:
            break
    if not picks:
        return {
            "flipped": [],
            "matched": False,
            "stars": player.stars,
            "message": f'Call two cards by number, 1 to {len(state.board)} - e.g. "2 7".',
        }
    if len(picks) == 1:
        # A peek, not a turn: the card is named and flips back. Keeping it face
        # up left it out of every later pair, so its partner could never be
        # matched and the game could not finish.
        index = picks[0]
        return {
            "flipped": [state.board[index]],
            "matched": False,
            "stars": player.stars,
            "message": f'Card {index + 1} is {state.board[index]}. Call two cards to flip a pair - e.g. "{index + 1} 4".',
        }

    first, second = picks
    if first in state.revealed or second in state.revealed:
        return {
            "flipped": [],
            "matched": False,
            "stars": player.stars,
            "message": "One of those is already face up. Pick two cards that are face down.",
        }
    state.revealed.extend((first, second))
    flipped_words = [state.board[first], state.board[second]]
    if state.board[first] == state.board[second]:
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

    # Miss: hide the two wrong cards again.
    state.revealed = [i for i in state.revealed if i not in picks]
    return {
        "flipped": flipped_words,
        "matched": False,
        "stars": player.stars,
        "message": f"Not a pair: {flipped_words[0]} and {flipped_words[1]} flip back.",
    }


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
