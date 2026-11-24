"""Which book or author a question names, and whether a library passage is from it.

Asked for notes on "Wild at Heart" by John Eldredge, a library that does not hold
that book still returns its nearest passages, and the model was told to answer
from them: every answer was about John R. Macduff's "Memories of Bethany". When a
question names a work, only passages whose title or author is that work may be
used; otherwise the prompt says the book is not in the indexed library.

Nothing here knows any title. Names are read from the question's shape (quotes,
"<Title> by <Author>", "chapter 4 of <Title>", "written by <Name>"), and compared
to each hit's own ``title``/``author`` metadata, case-insensitively, ignoring
punctuation, and allowing the small misspellings speech recognition makes
("Eldridge" for "Eldredge") through a similarity ratio per word.
"""
from __future__ import annotations

import math
import re
from difflib import SequenceMatcher

#: Words that join the words of a title or a name but do not identify it.
_CONNECTORS = frozenset({
    "a", "an", "and", "at", "in", "of", "on", "the", "to", "for", "with", "from", "or", "is",
})
#: Words that end a name read from free text ("by John Eldredge chapter 4").
_NAME_STOP = _CONNECTORS | frozenset({
    "chapter", "chapters", "page", "pages", "part", "section", "verse", "please", "about",
    "what", "which", "who", "how", "why", "when", "where", "does", "did", "say", "says",
    "said", "notes", "note", "summary", "summarize", "summarise", "me", "us", "it", "this",
    "that", "book", "books", "novel", "jarvis",
})
#: How alike two words must be to count as the same word (Eldridge/Eldredge is 0.875).
WORD_SIMILARITY = 0.8

_QUOTED = re.compile(r"[\"\u201c\u201d]([^\"\u201c\u201d]{2,80})[\"\u201c\u201d]")
_CAP = r"[A-Z][\w'\u2019-]*\.?"
_TITLE_SPAN = re.compile(
    rf"{_CAP}(?:\s+(?:(?:a|an|and|at|in|of|on|the|to|for|with|from|or)\s+)*{_CAP})*"
)
_AUTHOR_AFTER_BY = re.compile(rf"\bby\s+({_CAP}(?:\s+{_CAP}){{0,3}})")
_WRITTEN_BY = re.compile(r"\b(?:written|authored|wrote)\s+by\s+([\w'.\u2019-]+(?:\s+[\w'.\u2019-]+){0,3})", re.I)
#: "book(s) by Macduff": a single name is enough after a word that says it is an author.
_BOOK_BY = re.compile(rf"\b(?:books?|novels?|works?|anything|something)\s+by\s+({_CAP}(?:\s+{_CAP}){{0,3}})")
#: The text right before a title: "chapter 4 of", "notes on", "the book".
_TITLE_CUE = re.compile(
    r"(?:\bbook|\bnovel|\btitled|\bcalled|\bnamed|\bchapter\s+\w+\s+(?:of|in|from)"
    r"|\bnotes\s+(?:on|from|for|about)|\bsummary\s+of|\bsummari[sz]e|\bread(?:ing)?)"
    r"(?:\s+the\s+book)?\s+$",
    re.I,
)
#: Speech recognition may lower-case everything: "notes on wild at heart by john eldredge".
_LOWER_TITLE_BY = re.compile(
    r"\b(?:notes\s+(?:on|from|for|about)|summary\s+of|summari[sz]e|chapter\s+\w+\s+(?:of|in|from)"
    r"|book|novel|read(?:ing)?)\s+(?P<title>[a-z][\w'\u2019 -]{1,60}?)\s+by\s+"
    r"(?P<author>[a-z][\w'.\u2019-]*(?:\s+[a-z][\w'.\u2019-]*){0,3})",
    re.I,
)


def _words(text: str) -> list[str]:
    """Lower-case words with punctuation removed ("R." -> "r", "Can't" -> "cant")."""
    cleaned = re.sub(r"['\u2019]", "", str(text or "").lower())
    return re.findall(r"[a-z0-9]+", cleaned)


def _significant(text: str) -> list[str]:
    return [w for w in _words(text) if w not in _CONNECTORS and len(w) > 1]


def _trim_name(text: str) -> str:
    """Cut a name read from free text at the first word that is not part of it."""
    kept: list[str] = []
    for raw in text.split():
        if raw.lower().strip(".,;:!?") in _NAME_STOP:
            break
        kept.append(raw)
        if raw[-1:] in ",;:!?":
            break
    return " ".join(kept).strip(" .,;:!?")


def named_works(query: str) -> list[str]:
    """The titles and authors a question names, in the order it names them."""
    text = str(query or "")
    found: list[str] = []

    def add(name: str) -> None:
        name = name.strip(" .,;:!?")
        if _significant(name) and name.lower() not in (f.lower() for f in found):
            found.append(name)

    for m in _QUOTED.finditer(text):
        add(m.group(1))
    for m in _TITLE_SPAN.finditer(text):
        span = m.group(0)
        if len(_words(span)) < 2:
            continue
        after = text[m.end():]
        if re.match(r"\s+by\s+[A-Za-z]", after) or _TITLE_CUE.search(text[:m.start()]):
            add(span)
    for m in _AUTHOR_AFTER_BY.finditer(text):
        name = _trim_name(m.group(1))
        if len(_significant(name)) >= 2:
            add(name)
    for pattern in (_WRITTEN_BY, _BOOK_BY):
        for m in pattern.finditer(text):
            add(_trim_name(m.group(1)))
    for m in _LOWER_TITLE_BY.finditer(text):
        add(m.group("title"))
        add(_trim_name(m.group("author")))
    return found


def _word_matches(word: str, candidates: list[str]) -> bool:
    for other in candidates:
        if word == other:
            return True
        if min(len(word), len(other)) >= 4 and SequenceMatcher(None, word, other).ratio() >= WORD_SIMILARITY:
            return True
    return False


def name_matches(name: str, target: str) -> bool:
    """Whether ``name`` (from the question) names ``target`` (a hit's title or author).

    Three in four of the name's significant words must appear in the target, each
    allowing a misspelling: "Eldridge" finds "John Eldredge", "Macduff" finds
    "John R. Macduff", and "John Eldredge" does not find "John R. Macduff".
    """
    wanted = _significant(name)
    have = _significant(target)
    if not wanted or not have:
        return False
    hits = sum(1 for w in wanted if _word_matches(w, have))
    return hits >= max(1, math.ceil(0.75 * len(wanted)))


def hit_is_from(meta: dict, names: list[str]) -> bool:
    """Whether a library hit's title or author is one of the works the question names."""
    if not isinstance(meta, dict):
        return False
    fields = [meta.get("title"), meta.get("author"), meta.get("authors"), meta.get("series")]
    targets = [str(f) for f in fields if isinstance(f, str) and f.strip()]
    return any(name_matches(n, t) for n in names for t in targets)


def not_indexed_note(names: list[str]) -> str:
    """The prompt line that replaces other books' passages."""
    named = ", ".join(f'"{n}"' for n in names)
    return (
        f"- {named}: not in the indexed library. No passage from it was found, so do not "
        "answer from other books; say it is not in the library and offer what you know "
        "about it in general, clearly marked as not from the library.\n"
    )
