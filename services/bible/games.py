# services/bible/games.py
"""Bible games: questions built from the installed text, scored by the server.

Every question is generated from the corpus itself -- the verse text, the book
order, the chapter counts -- so the answer key is the Bible on this server and
never a model's guess (docs/BIBLE_STUDY.md: "a game that hallucinates a wrong
answer breaks trust; the server is the validator").

A game is a pure function of ``(mode, seed, version)``. That is what makes the
daily quiz identical for every member of the family (its seed is the date), and
it is how an attempt is scored: the server regenerates the questions rather
than trusting an answer key the client could have edited. The client only ever
sees the prompts and the options.
"""
from __future__ import annotations

import hashlib
import json
import random
import re
import secrets
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from sqlmodel import Session

from . import corpus
from .books import BOOK_BY_OSIS
from .models import BibleVerse

_VERSES_PATH = Path(__file__).parent / "games" / "verses.json"

#: The games on offer. ``kinds`` cycle in order through the question list.
MODES: dict[str, dict] = {
    "daily": {
        "name": "Daily Quiz",
        "blurb": "Ten questions, the same for the whole family today. Your first try banks a star per right answer.",
        "kinds": ["which_book", "fill_blank", "book_after", "chapter_count", "which_book", "fill_blank"],
        "count": 10,
        "daily": True,
    },
    "verse_race": {
        "name": "Verse Race",
        "blurb": "Sixty seconds. Name the book or fill the gap as fast as you can.",
        "kinds": ["which_book", "fill_blank"],
        "count": 15,
        "seconds": 60,
    },
    "which_book": {
        "name": "Which Book?",
        "blurb": "Read a well-known verse and pick the book it comes from.",
        "kinds": ["which_book"],
        "count": 8,
    },
    "fill_blank": {
        "name": "Fill the Gap",
        "blurb": "A word is missing from a well-known verse. Pick the right one.",
        "kinds": ["fill_blank"],
        "count": 8,
    },
    "next_verse": {
        "name": "What Comes Next?",
        "blurb": "Read a well-known verse and pick the one that follows it.",
        "kinds": ["next_verse"],
        "count": 6,
    },
    "books": {
        "name": "Books of the Bible",
        "blurb": "What comes next, and how many chapters? Learn the library.",
        "kinds": ["book_after", "chapter_count"],
        "count": 8,
    },
}

BLANK = "_____"
_SEED_RE = re.compile(r"^[A-Za-z0-9-]{1,40}$")
_WORD_RE = re.compile(r"[A-Za-z][A-Za-z']*")
_STOPWORDS = frozenset(
    ["a", "about", "above", "after", "again", "against", "all", "also", "am", "an", "and", "any", "are", "as", "at", "be", "because", "been", "before", "being", "below", "between", "both", "but", "by", "can", "did", "do", "does", "doing", "down", "during", "each", "even", "every", "few", "for", "from", "further", "had", "has", "have", "having", "he", "her", "here", "hers", "herself", "him", "himself", "his", "how", "i", "if", "in", "into", "is", "it", "its", "itself", "let", "me", "more", "most", "my", "myself", "no", "nor", "not", "now", "of", "off", "on", "once", "only", "or", "other", "our", "ours", "ourselves", "out", "over", "own", "same", "say", "said", "shall", "she", "should", "so", "some", "such", "than", "that", "the", "thee", "their", "theirs", "them", "themselves", "then", "there", "these", "they", "thine", "this", "those", "thou", "thy", "through", "to", "too", "under", "until", "unto", "up", "upon", "us", "very", "was", "we", "were", "what", "when", "where", "which", "while", "who", "whom", "why", "will", "with", "would", "ye", "yea", "you", "your", "yours", "yourself", "yourselves", "hath", "doth", "unto", "shalt", "wilt", "art", "also"]
)


class GameError(ValueError):
    """The game cannot be built: an unknown mode, a bad seed, or too little text."""


@dataclass
class Question:
    id: int
    kind: str
    prompt: str
    options: list[str]
    answer: str
    text: str = ""
    reference: str = ""
    #: Shown with the question (a fill-the-gap verse names its reference); a
    #: which-book question keeps it back, since the reference is the answer.
    show_reference: bool = False

    def public(self) -> dict:
        out = {"id": self.id, "kind": self.kind, "prompt": self.prompt, "options": self.options}
        if self.text:
            out["text"] = self.text
        if self.show_reference and self.reference:
            out["reference"] = self.reference
        return out


@dataclass
class _Context:
    rng: random.Random
    verses: list[dict]
    books: list[dict]
    used_verses: set[int] = field(default_factory=set)
    used_books: set[str] = field(default_factory=set)
    #: reference -> the text of the verse after it, for the next-verse game.
    followers: dict[str, str] = field(default_factory=dict)


def today() -> str:
    return date.today().isoformat()


def resolve_seed(mode: str, seed: str | None) -> str:
    """The daily quiz is keyed by the date; practice games by a fresh seed."""
    if mode not in MODES:
        raise GameError(f"Unknown game {mode!r}. Choose one of: {', '.join(MODES)}.")
    if MODES[mode].get("daily"):
        return seed or today()
    if seed is None or seed == "":
        return secrets.token_hex(6)
    if not _SEED_RE.match(seed):
        raise GameError("A game seed is letters, digits and dashes, at most 40 of them.")
    return seed


def build(session: Session, mode: str, seed: str, version: str) -> list[Question]:
    """The questions for one game. Same arguments, same questions, always."""
    spec = MODES.get(mode)
    if spec is None:
        raise GameError(f"Unknown game {mode!r}. Choose one of: {', '.join(MODES)}.")
    digest = hashlib.sha256(f"{mode}|{seed}|{version}".encode()).digest()
    ctx = _Context(
        rng=random.Random(int.from_bytes(digest[:8], "big")),
        verses=_known_verses(session, version),
        books=corpus.list_books(session, version),
    )
    if "next_verse" in spec["kinds"]:
        ctx.followers = _followers(session, version, ctx.verses)
    if len(ctx.books) < 4:
        raise GameError(f"Translation {version!r} has too few books installed for a game.")
    questions: list[Question] = []
    kinds = spec["kinds"]
    for index in range(spec["count"]):
        make = _GENERATORS[kinds[index % len(kinds)]]
        question = make(ctx, index)
        if question is None:
            raise GameError(
                f"Translation {version!r} does not hold enough of the well-known verses this game "
                "draws from. Install a complete translation in Admin > Bible."
            )
        questions.append(question)
    return questions


def score(questions: list[Question], answers: list[str | None]) -> list[dict]:
    """Mark each answer against the regenerated question."""
    if len(answers) != len(questions):
        raise GameError(f"Expected {len(questions)} answers, got {len(answers)}.")
    results = []
    for question, chosen in zip(questions, answers, strict=True):
        right = chosen is not None and _same(chosen, question.answer)
        results.append(
            {
                "id": question.id,
                "prompt": question.prompt,
                "correct": right,
                "chosen": chosen,
                "answer": question.answer,
                "reference": question.reference,
                "text": question.text if question.kind != "fill_blank" else question.text.replace(BLANK, question.answer, 1),
            }
        )
    return results


def catalogue() -> list[dict]:
    return [
        {
            "mode": mode,
            "name": spec["name"],
            "blurb": spec["blurb"],
            "count": spec["count"],
            "daily": bool(spec.get("daily")),
            "seconds": spec.get("seconds"),
        }
        for mode, spec in MODES.items()
    ]


# question generators


def _which_book(ctx: _Context, index: int) -> Question | None:
    verse = _take_verse(ctx)
    if verse is None:
        return None
    others = _other_books(ctx, verse["osis"], 3, same_testament=True)
    options = [verse["book_name"], *(b["name"] for b in others)]
    ctx.rng.shuffle(options)
    return Question(
        id=index,
        kind="which_book",
        prompt="Which book is this verse from?",
        text=verse["text"],
        options=options,
        answer=verse["book_name"],
        reference=verse["reference"],
    )


def _fill_blank(ctx: _Context, index: int) -> Question | None:
    for _ in range(len(ctx.verses)):
        verse = _take_verse(ctx)
        if verse is None:
            return None
        words = _content_words(verse["text"])
        if not words:
            continue
        answer = ctx.rng.choice(words)
        pool = sorted(
            {
                w
                for other in ctx.verses
                if other is not verse
                for w in _content_words(other["text"])
                if w.lower() != answer.lower() and abs(len(w) - len(answer)) <= 3
            }
        )
        if len(pool) < 3:
            continue
        distractors = ctx.rng.sample(pool, 3)
        options = [_match_case(w, answer) for w in [answer, *distractors]]
        if len({o.lower() for o in options}) < 4:
            continue
        ctx.rng.shuffle(options)
        # Letter boundaries, the same notion of a word the extractor used, so
        # the word picked is always the word blanked.
        text, blanked = re.subn(rf"(?<![A-Za-z']){re.escape(answer)}(?![A-Za-z'])", BLANK, verse["text"], count=1)
        if not blanked:
            continue
        return Question(
            id=index,
            kind="fill_blank",
            prompt="Which word fills the gap?",
            text=text,
            options=options,
            answer=answer,
            reference=verse["reference"],
            show_reference=True,
        )
    return None


def _book_after(ctx: _Context, index: int) -> Question | None:
    books = ctx.books
    candidates = [i for i in range(len(books) - 1) if books[i]["osis"] not in ctx.used_books]
    if not candidates:
        candidates = list(range(len(books) - 1))
    i = ctx.rng.choice(candidates)
    ctx.used_books.add(books[i]["osis"])
    current, answer = books[i], books[i + 1]
    nearby = [b for j, b in enumerate(books) if j not in (i, i + 1) and abs(j - i) <= 6]
    distractors = ctx.rng.sample(nearby, min(3, len(nearby)))
    options = [answer["name"], *(b["name"] for b in distractors)]
    ctx.rng.shuffle(options)
    return Question(
        id=index,
        kind="book_after",
        prompt=f"Which book comes right after {current['name']}?",
        options=options,
        answer=answer["name"],
        reference=f"{current['name']}, then {answer['name']}",
    )


def _chapter_count(ctx: _Context, index: int) -> Question | None:
    books = [b for b in ctx.books if b["osis"] not in ctx.used_books] or ctx.books
    book = ctx.rng.choice(books)
    ctx.used_books.add(book["osis"])
    right = int(book["chapters"])
    spread = sorted({right + d for d in (-10, -5, -3, -2, -1, 1, 2, 3, 5, 10) if right + d >= 1} - {right})
    distractors = ctx.rng.sample(spread, 3)
    options = [str(n) for n in sorted([right, *distractors])]
    return Question(
        id=index,
        kind="chapter_count",
        prompt=f"How many chapters are in {book['name']}?",
        options=options,
        answer=str(right),
        reference=f"{book['name']} has {right} chapter{'s' if right != 1 else ''}",
    )


def _next_verse(ctx: _Context, index: int) -> Question | None:
    """A verse and the one after it; the other options follow other verses.

    Every option is a real verse from the installed text, so a wrong choice is
    never a made-up sentence, only the wrong place in the Bible.
    """
    for _ in range(len(ctx.verses)):
        verse = _take_verse(ctx)
        if verse is None:
            return None
        answer = ctx.followers.get(verse["reference"])
        if not answer:
            continue
        others = sorted({t for ref, t in ctx.followers.items() if ref != verse["reference"] and t != answer})
        if len(others) < 3:
            return None
        options = [answer, *ctx.rng.sample(others, 3)]
        ctx.rng.shuffle(options)
        return Question(
            id=index,
            kind="next_verse",
            prompt="Which verse comes next?",
            text=verse["text"],
            options=options,
            answer=answer,
            reference=verse["reference"],
            show_reference=True,
        )
    return None


_GENERATORS = {
    "next_verse": _next_verse,
    "which_book": _which_book,
    "fill_blank": _fill_blank,
    "book_after": _book_after,
    "chapter_count": _chapter_count,
}


# helpers


def _known_verses(session: Session, version: str) -> list[dict]:
    """The curated verses, with their text in this translation.

    A verse the translation does not carry is skipped rather than invented.
    """
    refs = json.loads(_VERSES_PATH.read_text(encoding="utf-8"))["verses"]
    out = []
    for osis, chapter, verse in refs:
        row = session.get(BibleVerse, {"version_code": version, "osis": osis, "chapter": chapter, "verse": verse})
        if row is None or len(row.text.strip()) < 20:
            continue
        name = BOOK_BY_OSIS.get(osis, {}).get("name", osis)
        out.append(
            {
                "osis": osis,
                "chapter": chapter,
                "verse": verse,
                "book_name": name,
                "reference": f"{name} {chapter}:{verse}",
                "text": row.text.strip(),
            }
        )
    return out


def _followers(session: Session, version: str, verses: list[dict]) -> dict[str, str]:
    """The verse after each curated verse, in the same chapter, from the corpus."""
    out = {}
    for v in verses:
        nxt = session.get(
            BibleVerse, {"version_code": version, "osis": v["osis"], "chapter": v["chapter"], "verse": v["verse"] + 1}
        )
        if nxt is not None and len(nxt.text.strip()) >= 20:
            out[v["reference"]] = nxt.text.strip()
    return out


def _take_verse(ctx: _Context) -> dict | None:
    unused = [i for i in range(len(ctx.verses)) if i not in ctx.used_verses]
    if not unused:
        return None
    i = ctx.rng.choice(unused)
    ctx.used_verses.add(i)
    return ctx.verses[i]


def _other_books(ctx: _Context, osis: str, n: int, *, same_testament: bool) -> list[dict]:
    order = BOOK_BY_OSIS.get(osis, {}).get("order", 0)
    old = order <= 39
    pool = [
        b
        for b in ctx.books
        if b["osis"] != osis and (not same_testament or (BOOK_BY_OSIS.get(b["osis"], {}).get("order", 0) <= 39) == old)
    ]
    if len(pool) < n:
        pool = [b for b in ctx.books if b["osis"] != osis]
    return ctx.rng.sample(pool, n)


def _content_words(text: str) -> list[str]:
    seen: dict[str, str] = {}
    for word in _WORD_RE.findall(text):
        clean = word.strip("'")
        if len(clean) >= 4 and clean.lower() not in _STOPWORDS and "'" not in clean:
            seen.setdefault(clean.lower(), clean)
    return list(seen.values())


def _match_case(word: str, like: str) -> str:
    return word[:1].upper() + word[1:].lower() if like[:1].isupper() else word.lower()


def _same(a: str, b: str) -> bool:
    return a.strip().casefold() == b.strip().casefold()
