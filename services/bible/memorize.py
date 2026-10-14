# services/bible/memorize.py
"""Scripture memory: a recall ladder with spaced review.

Each verse climbs a ladder of harder recall (docs/BIBLE_STUDY.md, "Quizzes,
memorization, games"):

====  ===============  ==============================================
level  stage            what the reader does
====  ===============  ==============================================
0     ``read``         reads it and types it with the text in view
1     ``cloze``        fills in the missing words
2     ``first_letters`` types it from the first letter of each word
3     ``recall``       types it from the reference alone
4     ``recall``       the same, at a longer interval
5     mastered         reviewed every 90 days to keep it
====  ===============  ==============================================

A pass moves up one level and pushes the next review out along
``INTERVALS``; a miss drops a level and brings the verse back tomorrow. The
typed answer is graded on the server against the corpus, word by word, so
small slips (punctuation, case, "Lord" for "LORD") never fail a recall and a
missing phrase always does.
"""
from __future__ import annotations

import difflib
import json
import re
from datetime import date, timedelta
from pathlib import Path

from sqlmodel import Session

from . import corpus
from .refs import ReferenceError, VerseSpan, parse_one

_SETS_PATH = Path(__file__).parent / "games" / "memory_sets.json"

MASTERED = 5
#: Days until the next review after reaching each level.
INTERVALS = {1: 1, 2: 3, 3: 7, 4: 14, 5: 30}
MASTERED_INTERVAL = 90
STAGES = {0: "read", 1: "cloze", 2: "first_letters", 3: "recall", 4: "recall", 5: "recall"}
#: Share of words that must match for a recall to pass.
PASS_SCORE = 0.9
#: The ladder is for a verse or a few, not a chapter.
MAX_VERSES = 6

_WORD = re.compile(r"[A-Za-z0-9]+(?:'[A-Za-z]+)?")


class MemoryError_(ValueError):
    """A reference that cannot be memorised (too long, spans chapters, not in the text)."""


def load_sets() -> list[dict]:
    return json.loads(_SETS_PATH.read_text(encoding="utf-8"))["sets"]


def find_set(set_id: str) -> dict | None:
    return next((s for s in load_sets() if s["id"] == set_id), None)


def resolve(session: Session, ref: str, version: str) -> tuple[VerseSpan, str, str]:
    """``(span, display reference, text)`` for a reference, from the corpus."""
    try:
        span = parse_one(ref)
    except ReferenceError as exc:
        raise MemoryError_(str(exc)) from exc
    if span.whole_book or span.resolved_chapter_end != span.chapter_start:
        raise MemoryError_("Memorise a verse or a few verses from one chapter, not a whole chapter or book.")
    if span.verse_end is None and span.verse_start == 1 and ":" not in ref:
        raise MemoryError_("Name the verse to memorise, for example John 3:16.")
    verses = corpus.fetch_span(session, version, span)
    if len(verses) > MAX_VERSES:
        raise MemoryError_(f"That is {len(verses)} verses; memorise at most {MAX_VERSES} at a time.")
    text = " ".join(v["text"].strip() for v in verses)
    return span, span.display(), text


def words(text: str) -> list[str]:
    return _WORD.findall(text)


def grade(expected: str, typed: str) -> dict:
    """Word-level match of what was typed against the verse.

    Returns the score (0-1), whether it passes, and the expected words each
    marked right or missed, so the reader sees exactly what slipped.
    """
    want = [w.casefold() for w in words(expected)]
    got = [w.casefold() for w in words(typed)]
    shown = words(expected)
    matcher = difflib.SequenceMatcher(a=want, b=got, autojunk=False)
    hit = [False] * len(want)
    for block in matcher.get_matching_blocks():
        for i in range(block.a, block.a + block.size):
            hit[i] = True
    matched = sum(hit)
    # Extra words the reader added count against them as well as missing ones.
    extra = max(0, len(got) - matched)
    score = matched / (len(want) + extra) if want else 0.0
    return {
        "score": round(score, 3),
        "passed": score >= PASS_SCORE,
        "words": [{"word": w, "ok": ok} for w, ok in zip(shown, hit)],
    }


def advance(level: int, passed: bool, today: date | None = None) -> tuple[int, str]:
    """The new level and next review date after one recall.

    Reaching mastery rests the verse 30 days; each review of a verse already
    mastered rests it 90.
    """
    today = today or date.today()
    if not passed:
        return max(0, level - 1), (today + timedelta(days=1)).isoformat()
    days = MASTERED_INTERVAL if level >= MASTERED else INTERVALS[min(MASTERED, level + 1)]
    return min(MASTERED, level + 1), (today + timedelta(days=days)).isoformat()


def cloze(text: str, level_seed: int) -> list[dict]:
    """Every third content word hidden, deterministically per verse."""
    out = []
    for i, w in enumerate(words(text)):
        hide = len(w) > 2 and (i + level_seed) % 3 == 0
        out.append({"word": w, "hidden": hide})
    return out


def first_letters(text: str) -> str:
    return " ".join(w[0] + ("" if len(w) == 1 else "_" * min(3, len(w) - 1)) for w in words(text))
