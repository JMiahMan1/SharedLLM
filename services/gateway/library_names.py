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
    """The prompt line that replaces other books' passages.

    A book missing from the search index may still be on the shelf (a reindex can
    take hours), so the note never says it cannot be read: it names the tool that
    reads it straight from the library.
    """
    named = ", ".join(f'"{n}"' for n in names)
    return (
        f"- {named}: not in the searchable index yet, so no passage from it was retrieved "
        "here. That does not mean the library lacks it or that it cannot be read: "
        "CalibreRequest with action fetch_text, query (the title and author) and chapter "
        "(a number or title) reads it directly from the library. Do not answer from other "
        "books' passages.\n"
    )


# --- A chapter the question names -------------------------------------------------------

_UNITS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
    "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
    "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
}
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70,
         "eighty": 80, "ninety": 90}
_ORDINALS = {
    "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6, "seventh": 7,
    "eighth": 8, "ninth": 9, "tenth": 10, "eleventh": 11, "twelfth": 12, "thirteenth": 13,
    "fourteenth": 14, "fifteenth": 15, "sixteenth": 16, "seventeenth": 17, "eighteenth": 18,
    "nineteenth": 19, "twentieth": 20, "thirtieth": 30, "fortieth": 40, "fiftieth": 50,
}
#: Chapter numbers above this are not chapters ("chapter MIX" is a word, not 1009).
MAX_CHAPTER = 200
_ROMAN = re.compile(r"M{0,3}(?:CM|CD|D?C{0,3})(?:XC|XL|L?X{0,3})(?:IX|IV|V?I{0,3})", re.I)
_TENS_ALT = "|".join(_TENS)
_NUMBER_WORD = (
    rf"(?:(?:{_TENS_ALT})(?:[\s-]+(?:{'|'.join(_UNITS)}|{'|'.join(_ORDINALS)}))?"
    rf"|{'|'.join(sorted({**_UNITS, **_ORDINALS}, key=len, reverse=True))})"
)
#: "chapter 4", "chapter four", "chapter IV", "ch. 4", "chapter twenty-one".
_CHAPTER_AFTER = re.compile(
    rf"\b(?:chapter|chap\.|ch\.)\s*(?P<num>\d{{1,3}}(?:st|nd|rd|th)?|{_NUMBER_WORD}|[ivxlcdm]{{1,8}})\b",
    re.I,
)
#: "the fourth chapter", "the 4th chapter", "the twenty-first chapter".
_CHAPTER_BEFORE = re.compile(
    rf"\b(?P<num>\d{{1,3}}(?:st|nd|rd|th)|{_NUMBER_WORD})\s+chapter\b",
    re.I,
)
#: Where a title read after "chapter N of" ends.
_TITLE_END = re.compile(
    r"\s+by\s+|\s+(?:from|in|on)\s+(?:our|my|the|your)\s+(?:library|shelf|shelves|calibre|books?)\b"
    r"|\s+and\s+(?:then\s+)?(?:give|tell|summari[sz]e|explain|read|say|share)\b"
    r"|\s+(?:about|say|says|mean|means|cover|covers|please)\s*[?.!]*\s*$|\s+please\b"
    r"|[?!,;:]|\.(?:\s|$)|$",
    re.I,
)
_AFTER_CHAPTER_TITLE = re.compile(
    r"^\s+(?:of|in|from)\s+(?:the\s+(?:book|novel)\s+(?:called\s+|titled\s+)?)?", re.I
)
_BY_NAME = re.compile(r"\bby\s+([\w'.\u2019-]+(?:\s+[\w'.\u2019-]+){0,3})", re.I)


def chapter_number(token: str) -> int | None:
    """A chapter number in digits, words, an ordinal or roman numerals (4, four, fourth, IV)."""
    token = str(token or "").strip().strip(".:").lower()
    if not token:
        return None
    number: int | None = None
    if token.isdigit():
        number = int(token)
    elif m := re.fullmatch(r"(\d+)(?:st|nd|rd|th)", token):
        number = int(m.group(1))
    else:
        parts = [p for p in re.split(r"[\s-]+", token) if p]
        if len(parts) == 1 and parts[0] in _UNITS:
            number = _UNITS[parts[0]]
        elif len(parts) == 1 and parts[0] in _ORDINALS:
            number = _ORDINALS[parts[0]]
        elif len(parts) == 1 and parts[0] in _TENS:
            number = _TENS[parts[0]]
        elif len(parts) == 2 and parts[0] in _TENS:
            tail = _UNITS.get(parts[1]) or _ORDINALS.get(parts[1])
            number = _TENS[parts[0]] + tail if tail and tail < 10 else None
        elif len(parts) == 1 and _ROMAN.fullmatch(parts[0]):
            values = {"i": 1, "v": 5, "x": 10, "l": 50, "c": 100, "d": 500, "m": 1000}
            total = 0
            for i, ch in enumerate(parts[0]):
                value = values[ch]
                total += -value if i + 1 < len(parts[0]) and values[parts[0][i + 1]] > value else value
            number = total or None
    if number is None or not 1 <= number <= MAX_CHAPTER:
        return None
    return number


class ChapterRef:
    """A chapter of a book the question names: ``chapter`` of ``title`` by ``author``."""

    __slots__ = ("author", "chapter", "title")

    def __init__(self, chapter: int, title: str, author: str) -> None:
        self.chapter = chapter
        self.title = title
        self.author = author

    @property
    def book_query(self) -> str:
        """The words CalibreRequest finds the book by: its title and author."""
        return " ".join(p for p in (self.title, self.author) if p)

    def __repr__(self) -> str:  # for logs and test failures
        return f"ChapterRef({self.chapter}, {self.title!r}, {self.author!r})"


def _clean_title(text: str) -> str:
    text = re.sub(r"^(?:the\s+(?:book|novel)\s+(?:called\s+|titled\s+)?)", "", text.strip(), flags=re.I)
    return text.strip(" \"'\u201c\u201d.,;:!?")


def chapter_named(query: str) -> ChapterRef | None:
    """The chapter and book a question asks for ("chapter 4 of <Title> by <Author>"), if any.

    The number may be digits, words, an ordinal or roman numerals. A chapter with no
    book named (a Bible chapter, "the next chapter") is ``None``: there is nothing to
    fetch it from.
    """
    text = str(query or "")
    found: tuple[int, re.Match[str]] | None = None
    for pattern in (_CHAPTER_AFTER, _CHAPTER_BEFORE):
        for m in pattern.finditer(text):
            raw = m.group("num")
            # A lower-case one-letter roman numeral is an English word ("chapter i liked").
            if len(raw) == 1 and raw.isalpha() and raw.islower():
                continue
            number = chapter_number(raw)
            if number is not None:
                found = (number, m)
                break
        if found:
            break
    if found is None:
        return None
    number, match = found

    author = ""
    for m in _BY_NAME.finditer(text):
        name = _trim_name(m.group(1))
        if _significant(name):
            author = name
            break

    title = ""
    rest = text[match.end():]
    lead = _AFTER_CHAPTER_TITLE.match(rest)
    if lead:
        tail = rest[lead.end():]
        end = _TITLE_END.search(tail)
        title = _clean_title(tail[: end.start()] if end else tail)
    if not _significant(title):
        title = ""
        for name in named_works(text):
            if re.search(r"\bchapter\b", name, re.I) or (author and name_matches(name, author)):
                continue
            name = re.sub(
                rf"^(?:chapter\s+)?{re.escape(match.group('num'))}\s+(?:of|in|from)\s+", "", name, flags=re.I
            )
            name = _clean_title(name)
            if _significant(name):
                title = name
                break
    if not author and not _QUOTED.search(text) and (len(_significant(title)) < 2 or title.lower().startswith("of ")):
        # "chapter 3 of John" or "of Romans" is scripture, not a book on the shelf: a
        # one-word title with no author would match any book by a John.
        return None
    return ChapterRef(number, title, author)
