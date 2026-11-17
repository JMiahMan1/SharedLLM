# services/bible/search.py
"""Verse search: a full-text index, a small query language and ranked results.

The reader used to search with ``LIKE '%words%'``: one translation, the exact
letters typed, the first rows in alphabetical book order. This module replaces
that with two SQLite FTS5 indexes over ``bibleverse``:

* ``verse_fts`` folds word forms (Porter stemming), so "love" also finds
  "loved", "loves" and "loving", and ranks results with BM25.
* ``verse_fts_exact`` keeps words as written, for quoted phrases and the
  "exact" match mode, where a reader asking for "living water" does not want
  "living waters".

Both are external-content tables kept in step with ``bibleverse`` by triggers,
so an import or re-import updates them in the same transaction as the verses.
An index that does not match the verse count (a new install, or a database
from before the index existed) is rebuilt once at startup in a worker thread;
until that finishes, and on a database that is not SQLite, search falls back to
the old substring scan rather than answering wrongly or not at all.

The query language (all typed in one box)::

    grace faith            both words (any form)
    "living water"         the exact phrase
    faith OR works         either word
    -law                   leave out verses with this word
    rejoic*                words starting with this
    faith NEAR/5 works     the two words within five words of each other
    in:John in:Rom-Gal     books, a range of books, or a section (in:gospels, in:nt)
    ver:esv ver:all        which translation(s)
    G26 H2617 strongs:G26  verses where that Greek or Hebrew word is
    has:note has:highlight has:bookmark   your own marked verses

User text never reaches FTS5's own syntax: every word becomes a quoted FTS
string, so a stray ``(`` or ``:`` cannot raise a syntax error or widen a query.
"""
from __future__ import annotations

import logging
import re
import threading
from dataclasses import dataclass, field

from sqlalchemy import text
from sqlmodel import Session, select

from services.bible.books import BOOK_BY_OSIS, BOOK_ORDER, BOOKS, resolve_book

log = logging.getLogger(__name__)

STEMMED = "verse_fts"
EXACT = "verse_fts_exact"
_TABLES = {
    STEMMED: "porter unicode61 remove_diacritics 2",
    EXACT: "unicode61 remove_diacritics 2",
}
#: Highlight markers; control characters cannot occur in imported verse text.
_MARK_OPEN = "\x02"
_MARK_CLOSE = "\x03"
DEFAULT_NEAR = 10
MAX_NEAR = 50

# ---------------------------------------------------------------------------
# Sections of the canon, for in:<section>

_ORDER = [b["osis"] for b in BOOKS]


def _span(first: str, last: str) -> tuple[str, ...]:
    return tuple(_ORDER[_ORDER.index(first) : _ORDER.index(last) + 1])


SECTIONS: dict[str, tuple[str, ...]] = {
    "ot": _span("Gen", "Mal"),
    "nt": _span("Matt", "Rev"),
    "law": _span("Gen", "Deut"),
    "history": _span("Josh", "Esth"),
    "poetry": _span("Job", "Song"),
    "prophets": _span("Isa", "Mal"),
    "majorprophets": _span("Isa", "Dan"),
    "minorprophets": _span("Hos", "Mal"),
    "gospels": _span("Matt", "John"),
    "pauline": _span("Rom", "Phlm"),
    "epistles": _span("Rom", "Jude"),
    "generalepistles": _span("Heb", "Jude"),
}
_SECTION_ALIASES = {
    "oldtestament": "ot",
    "newtestament": "nt",
    "torah": "law",
    "pentateuch": "law",
    "wisdom": "poetry",
    "major": "majorprophets",
    "minor": "minorprophets",
    "paul": "pauline",
    "letters": "epistles",
    "general": "generalepistles",
}
SECTION_LABELS: dict[str, str] = {
    "ot": "Old Testament",
    "nt": "New Testament",
    "law": "The Law",
    "history": "History",
    "poetry": "Poetry & Wisdom",
    "prophets": "Prophets",
    "majorprophets": "Major Prophets",
    "minorprophets": "Minor Prophets",
    "gospels": "Gospels",
    "pauline": "Paul's Letters",
    "epistles": "Letters",
    "generalepistles": "General Letters",
}


def _fold(token: str) -> str:
    return re.sub(r"[\s._\-]", "", token.lower())


def resolve_scope(token: str) -> tuple[str, tuple[str, ...]] | None:
    """``in:`` value to (label, books): a section, one book, or a book range."""
    raw = token.strip().strip('"')
    if not raw:
        return None
    folded = _fold(raw)
    key = _SECTION_ALIASES.get(folded, folded)
    if key in SECTIONS:
        return SECTION_LABELS[key], SECTIONS[key]
    single = resolve_book(raw)
    if single:
        return BOOK_BY_OSIS[single]["name"], (single,)
    # A range: "Rom-Gal", "1 Cor - 2 Cor". The split must not break "1-John"
    # style names, so try every hyphen and keep the first that resolves.
    for match in re.finditer(r"\s*[-\u2013]\s*", raw):
        first, last = resolve_book(raw[: match.start()]), resolve_book(raw[match.end() :])
        if first and last:
            if BOOK_ORDER[first] > BOOK_ORDER[last]:
                first, last = last, first
            label = f"{BOOK_BY_OSIS[first]['name']} to {BOOK_BY_OSIS[last]['name']}"
            return label, _span(first, last)
    return None


# ---------------------------------------------------------------------------
# Query parsing


class QueryError(ValueError):
    """The search text cannot be turned into a search; the message says why."""


@dataclass
class Term:
    words: str
    #: Typed in quotes: must match as written (the exact index), not any word form.
    quoted: bool = False
    prefix: bool = False


@dataclass
class Near:
    left: Term
    right: Term
    distance: int


@dataclass
class ParsedQuery:
    #: AND of clauses; each clause is an OR of alternatives.
    clauses: list[list[Term | Near]] = field(default_factory=list)
    excluded: list[Term] = field(default_factory=list)
    scopes: list[tuple[str, tuple[str, ...]]] = field(default_factory=list)
    versions: list[str] = field(default_factory=list)
    all_versions: bool = False
    strongs: list[str] = field(default_factory=list)
    has: list[str] = field(default_factory=list)
    sort: str | None = None

    @property
    def has_text(self) -> bool:
        return bool(self.clauses)

    def books(self) -> tuple[str, ...] | None:
        if not self.scopes:
            return None
        chosen = {osis for _label, books in self.scopes for osis in books}
        return tuple(o for o in _ORDER if o in chosen)

    def describe(self) -> dict:
        def term(t: Term | Near) -> str:
            if isinstance(t, Near):
                return f"{term(t.left)} near {term(t.right)} ({t.distance})"
            shown = f'"{t.words}"' if t.quoted else t.words
            return shown + ("*" if t.prefix else "")

        return {
            "all": [" or ".join(term(t) for t in clause) for clause in self.clauses],
            "without": [term(t) for t in self.excluded],
            "scopes": [label for label, _ in self.scopes],
            "versions": "all" if self.all_versions else self.versions,
            "strongs": self.strongs,
            "has": self.has,
        }


_TOKEN = re.compile(
    r"""
    (?P<neg>-)?
    (?:
        (?P<key>[A-Za-z]+):(?P<val>"[^"]*"?|\S+)
      | "(?P<phrase>[^"]*)"?
      | (?P<word>[^\s"]+)
    )
    """,
    re.VERBOSE,
)
_STRONGS = re.compile(r"^([GHgh])0*(\d{1,5})[a-z]?$")
_NEAR = re.compile(r"^NEAR(?:/(\d+))?$")
_HAS = {"note": "note", "notes": "note", "highlight": "highlight", "highlights": "highlight",
        "bookmark": "bookmark", "bookmarks": "bookmark", "mark": "any", "marks": "any"}
_MODES = ("all", "any", "exact", "meaning")


def _strongs(value: str) -> str | None:
    match = _STRONGS.match(value.strip())
    if not match:
        return None
    return f"{match.group(1).upper()}{int(match.group(2))}"


def _words(value: str) -> str:
    """The text of a term as the tokenizer would see it, for display and checks."""
    return " ".join(re.findall(r"\w+", value, flags=re.UNICODE))


def parse(query: str, *, mode: str = "all") -> ParsedQuery:
    """Turn the search box text into a :class:`ParsedQuery`.

    ``mode`` is the match setting beside the box: ``all`` words (the default),
    ``any`` word, or the ``exact`` text typed, as one phrase.
    """
    if mode not in _MODES:
        raise QueryError(f"match must be one of {', '.join(_MODES)}")
    parsed = ParsedQuery()
    pending_or = False
    pending_near: int | None = None

    def attach(term: Term) -> None:
        nonlocal pending_or, pending_near
        last_clause = parsed.clauses[-1] if parsed.clauses else None
        if pending_near is not None and last_clause and isinstance(last_clause[-1], Term):
            last_clause[-1] = Near(last_clause[-1], term, pending_near)
        elif pending_or and last_clause is not None:
            last_clause.append(term)
        else:
            parsed.clauses.append([term])
        pending_or = False
        pending_near = None

    def add(term: Term, negative: bool) -> None:
        if negative:
            parsed.excluded.append(term)
        else:
            attach(term)

    for match in _TOKEN.finditer(query):
        negative = bool(match.group("neg"))
        key = (match.group("key") or "").lower()
        if key:
            value = match.group("val").strip('"')
            if key in ("in", "book", "books"):
                scope = resolve_scope(value)
                if not scope:
                    raise QueryError(f"{value!r} is not a book or section of the Bible")
                parsed.scopes.append(scope)
            elif key in ("ver", "version", "translation"):
                for code in value.lower().split(","):
                    if code == "all":
                        parsed.all_versions = True
                    elif code:
                        parsed.versions.append(code)
            elif key in ("strongs", "strong"):
                number = _strongs(value)
                if not number:
                    raise QueryError(f"{value!r} is not a Strong's number (like G26 or H2617)")
                parsed.strongs.append(number)
            elif key == "has":
                kind = _HAS.get(value.lower())
                if not kind:
                    raise QueryError("has: takes note, highlight or bookmark")
                parsed.has.append(kind)
            elif key == "sort":
                if value.lower() not in ("rank", "canon", "bible", "relevance"):
                    raise QueryError("sort: takes rank or bible")
                parsed.sort = "canon" if value.lower() in ("canon", "bible") else "rank"
            else:
                # Not a filter we know ("Lord:" or a time like 3:16): search the words.
                words = _words(f"{key} {value}")
                if words:
                    add(Term(words), negative)
            continue
        phrase = match.group("phrase")
        if phrase is not None:
            words = _words(phrase)
            if words:
                add(Term(words, quoted=True), negative)
            continue
        word = match.group("word")
        if word == "OR" and not negative:
            pending_or = bool(parsed.clauses)
            continue
        near = _NEAR.match(word)
        if near and not negative:
            if parsed.clauses:
                pending_near = max(1, min(int(near.group(1) or DEFAULT_NEAR), MAX_NEAR))
            continue
        if not negative and re.match(r"^[GHgh]\d", word):
            number = _strongs(word)
            if number:
                parsed.strongs.append(number)
                continue
        words = _words(word)
        if not words:
            continue
        # "Lord's" is one typed word but two tokens; FTS reads it as a phrase.
        add(Term(words, prefix=word.endswith("*") and " " not in words), negative)

    if mode in ("any", "meaning") and len(parsed.clauses) > 1:
        parsed.clauses = [[t for clause in parsed.clauses for t in clause]]
    elif mode == "exact" and parsed.clauses:
        plain = [t for clause in parsed.clauses for t in clause]
        if all(isinstance(t, Term) for t in plain):
            parsed.clauses = [[Term(" ".join(t.words for t in plain), quoted=True)]]  # type: ignore[union-attr]

    if parsed.excluded and not (parsed.clauses or parsed.strongs or parsed.has):
        raise QueryError("Add a word to search for, not only words to leave out")
    if not (parsed.clauses or parsed.strongs or parsed.has):
        raise QueryError("Type a word, a phrase or a Strong's number to search for")
    return parsed


def _fts_string(term: Term) -> str:
    return '"' + term.words.replace('"', '""') + '"' + ("*" if term.prefix else "")


#: Older English verb forms the Porter stemmer does not fold ("loveth" is not
#: "love" to it). The KJV and ASV are full of them, so a plain word also
#: searches its -eth/-est forms and these irregular ones.
_ARCHAIC: dict[str, tuple[str, ...]] = {
    "have": ("hath", "hast"),
    "has": ("hath", "hast"),
    "do": ("doth", "doeth", "dost", "doest"),
    "does": ("doth", "doeth"),
    "say": ("saith", "sayest"),
    "says": ("saith",),
    "go": ("goeth",),
    "goes": ("goeth",),
    "shall": ("shalt",),
    "will": ("wilt",),
    "are": ("art",),
    "you": ("ye", "thee", "thou"),
    "your": ("thy", "thine"),
}


def archaic_forms(word: str) -> list[str]:
    """The -eth/-est (and irregular) forms of a modern word, for older translations."""
    word = word.lower()
    if " " in word or len(word) < 2 or not word.isalpha():
        return []
    bases = {word}
    if word.endswith("ies") and len(word) > 4:
        bases.add(word[:-3] + "y")
    elif word.endswith("es") and len(word) > 4:
        bases.update({word[:-2], word[:-1]})
    elif word.endswith("s") and not word.endswith("ss") and len(word) > 3:
        bases.add(word[:-1])
    if word.endswith("ed") and len(word) > 4:
        bases.update({word[:-2], word[:-1]})
    if word.endswith("ing") and len(word) > 5:
        bases.update({word[:-3], word[:-3] + "e"})
    forms: list[str] = list(_ARCHAIC.get(word, ()))
    for base in sorted(bases):
        if len(base) < 2:
            continue
        if base.endswith("e"):
            forms += [base + "th", base + "st"]
        elif base.endswith("y") and len(base) > 2 and base[-2] not in "aeiou":
            forms += [base[:-1] + "ieth", base[:-1] + "iest"]
        else:
            forms += [base + "eth", base + "est"]
    return [f for f in dict.fromkeys(forms) if f != word]


def _fts_term(term: Term) -> str:
    if term.quoted or term.prefix or " " in term.words:
        return _fts_string(term)
    variants = [term.words, *archaic_forms(term.words)]
    if len(variants) == 1:
        return _fts_string(term)
    return "(" + " OR ".join(_fts_string(Term(v)) for v in variants) + ")"


def _fts_expr(item: Term | Near) -> str:
    if isinstance(item, Near):
        return f"NEAR({_fts_string(item.left)} {_fts_string(item.right)}, {item.distance})"
    return _fts_term(item)


def match_expressions(parsed: ParsedQuery) -> tuple[str | None, str | None]:
    """(stemmed MATCH, exact MATCH) for a parsed query.

    The stemmed expression carries every clause, so results rank and highlight
    on all of them. A clause made only of quoted phrases is also required in
    the exact index, which is what makes a quoted phrase mean its words as
    written. (A clause mixing a phrase with a loose word cannot be: the exact
    index would then demand the phrase.)
    """
    if not parsed.clauses:
        return None, None
    parts = ["(" + " OR ".join(_fts_expr(t) for t in clause) + ")" for clause in parsed.clauses]
    stemmed = " AND ".join(parts)
    if parsed.excluded:
        stemmed = f"({stemmed}) NOT (" + " OR ".join(_fts_term(t) for t in parsed.excluded) + ")"
    exact_parts = [
        "(" + " OR ".join(_fts_string(t) for t in clause) + ")"  # type: ignore[arg-type]
        for clause in parsed.clauses
        if all(isinstance(t, Term) and t.quoted and not t.prefix for t in clause)
    ]
    return stemmed, (" AND ".join(exact_parts) or None)


# ---------------------------------------------------------------------------
# The index

_ready: set[str] = set()
_building: set[str] = set()
_lock = threading.Lock()


def _engine_key(bind) -> str:
    return str(bind.url)


def available(bind) -> bool:
    return bind.dialect.name == "sqlite"


def _create(conn) -> None:
    for table, tokenizer in _TABLES.items():
        conn.execute(text(
            f"CREATE VIRTUAL TABLE IF NOT EXISTS {table} USING fts5("
            f"text, content='bibleverse', content_rowid='rowid', tokenize='{tokenizer}')"
        ))
        conn.execute(text(
            f"CREATE TRIGGER IF NOT EXISTS {table}_ai AFTER INSERT ON bibleverse BEGIN "
            f"INSERT INTO {table}(rowid, text) VALUES (new.rowid, new.text); END"
        ))
        conn.execute(text(
            f"CREATE TRIGGER IF NOT EXISTS {table}_ad AFTER DELETE ON bibleverse BEGIN "
            f"INSERT INTO {table}({table}, rowid, text) VALUES ('delete', old.rowid, old.text); END"
        ))
        conn.execute(text(
            f"CREATE TRIGGER IF NOT EXISTS {table}_au AFTER UPDATE ON bibleverse BEGIN "
            f"INSERT INTO {table}({table}, rowid, text) VALUES ('delete', old.rowid, old.text); "
            f"INSERT INTO {table}(rowid, text) VALUES (new.rowid, new.text); END"
        ))


def _stale_tables(conn) -> list[str]:
    verses = conn.execute(text("SELECT count(*) FROM bibleverse")).scalar() or 0
    stale = []
    for table in _TABLES:
        indexed = conn.execute(text(f"SELECT count(*) FROM {table}_docsize")).scalar() or 0
        if indexed != verses:
            stale.append(table)
    return stale


def prepare(engine) -> list[str]:
    """Create the indexes and triggers; return the tables that need a rebuild."""
    if not available(engine):
        return []
    with engine.begin() as conn:
        _create(conn)
        stale = _stale_tables(conn)
    if not stale:
        with _lock:
            _ready.add(_engine_key(engine))
    return stale


def rebuild(engine, tables: list[str] | None = None) -> None:
    """Rebuild the named indexes (all by default) from ``bibleverse``."""
    key = _engine_key(engine)
    with _lock:
        if key in _building:
            return
        _building.add(key)
    try:
        with engine.begin() as conn:
            _create(conn)
            for table in tables or list(_TABLES):
                conn.execute(text(f"INSERT INTO {table}({table}) VALUES ('rebuild')"))
        with _lock:
            _ready.add(key)
        log.info("[Bible] search index rebuilt (%s)", ", ".join(tables or _TABLES))
    finally:
        with _lock:
            _building.discard(key)


def ensure(session: Session) -> bool:
    """Is the full-text index usable for this session's database?

    The first search on a database the startup path never saw (the tests' own
    engines) builds it inline; the service's own database is prepared at
    startup and rebuilt in the background, and reports not-ready meanwhile.
    """
    bind = session.get_bind()
    if not available(bind):
        return False
    key = _engine_key(bind)
    with _lock:
        if key in _ready:
            return True
        if key in _building:
            return False
    stale = prepare(bind)
    if stale:
        rebuild(bind, stale)
    with _lock:
        return key in _ready


def forget(engine) -> None:
    """Drop the readiness flag (for tests that recreate a database at one path)."""
    with _lock:
        _ready.discard(_engine_key(engine))


# ---------------------------------------------------------------------------
# Searching

_CANON_CASE = "CASE v.osis " + " ".join(f"WHEN '{o}' THEN {BOOK_ORDER[o]}" for o in _ORDER) + " ELSE 99 END"


def _highlights(marked: str) -> tuple[str, list[list[int]]]:
    """Strip the highlight markers, returning the text and [start, end) spans."""
    plain: list[str] = []
    spans: list[list[int]] = []
    start = None
    length = 0
    for ch in marked:
        if ch == _MARK_OPEN:
            start = length
        elif ch == _MARK_CLOSE:
            if start is not None and length > start:
                spans.append([start, length])
            start = None
        else:
            plain.append(ch)
            length += 1
    return "".join(plain), spans


def _word_spans(body: str, words: list[str]) -> list[list[int]]:
    spans: list[list[int]] = []
    for word in {w.strip().lower() for w in words if w and w.strip()}:
        for match in re.finditer(rf"\b{re.escape(word)}\b", body, flags=re.IGNORECASE):
            spans.append([match.start(), match.end()])
    return sorted(spans)


def _positions_strongs(session: Session, numbers: list[str]) -> tuple[set[tuple[str, int, int]], list[str]]:
    from services.bible import concordance

    found: set[tuple[str, int, int]] | None = None
    words: list[str] = []
    for number in numbers:
        hits = concordance.verses(session, number, limit=100_000)
        here = {(v["osis"], v["chapter"], v["verse"]) for v in hits["verses"]}
        for v in hits["verses"]:
            words.extend(w.strip() for w in str(v.get("word") or "").split(","))
        found = here if found is None else found & here
    return found or set(), words


def _positions_marks(session: Session, username: str, kinds: list[str]) -> set[tuple[str, int, int]]:
    from services.bible.models import VerseMark

    rows = session.exec(select(VerseMark).where(VerseMark.username == username)).all()
    found: set[tuple[str, int, int]] = set()
    for kind in kinds:
        here: set[tuple[str, int, int]] = set()
        for row in rows:
            is_note = bool(row.note_path or row.note_preview) or row.kind == "note"
            if kind == "note" and not is_note:
                continue
            if kind in ("highlight", "bookmark") and row.kind != kind:
                continue
            last = row.verse_end or row.verse_start
            for verse in range(row.verse_start, max(last, row.verse_start) + 1):
                here.add((row.osis, row.chapter, verse))
        found = here if not found else found & here
    return found


def _display(
    conn,
    chosen: list[tuple[tuple[str, int, int], str, list[str]]],
    *,
    stemmed: str | None,
    strongs_words: list[str] | None = None,
    col: str = "v.",
    fallback_version: str | None = None,
) -> list[dict]:
    """The result rows for a page: each verse's text in its chosen translation,
    with the matched words marked. A verse the word expression does not match
    (a meaning-only hit) gets its plain text; one missing from the chosen
    translation falls back to ``fallback_version``."""
    shown: dict[tuple[str, tuple[str, int, int]], tuple[str, list[list[int]]]] = {}

    def where(items) -> tuple[str, dict[str, object]]:
        clauses = []
        lookup: dict[str, object] = {}
        for i, ((osis, chapter, verse), code) in enumerate(items):
            clauses.append(
                f"({col}version_code = :c{i} AND {col}osis = :o{i} AND {col}chapter = :h{i} AND {col}verse = :v{i})"
            )
            lookup.update({f"c{i}": code, f"o{i}": osis, f"h{i}": chapter, f"v{i}": verse})
        return " OR ".join(clauses), lookup

    if chosen and stemmed:
        clause, lookup = where([(pos, code) for pos, code, _m in chosen])
        got = conn.execute(
            text(
                f"SELECT v.version_code, v.osis, v.chapter, v.verse, "
                f"highlight({STEMMED}, 0, char(2), char(3)) FROM {STEMMED} "
                f"JOIN bibleverse v ON v.rowid = {STEMMED}.rowid WHERE {STEMMED} MATCH :stemmed AND ({clause})"
            ),
            {**lookup, "stemmed": stemmed},
        ).all()
        for code, osis, chapter, verse, marked in got:
            shown[(code, (osis, chapter, verse))] = _highlights(marked)
    for attempt in ("chosen", "fallback"):
        wanted = [
            (pos, code if attempt == "chosen" else fallback_version)
            for pos, code, _m in chosen
            if (code, pos) not in shown and not any((c, pos) in shown for c in (code, fallback_version))
        ]
        wanted = [(pos, code) for pos, code in wanted if code]
        if not wanted:
            continue
        clause, lookup = where(wanted)
        got = conn.execute(
            text(f"SELECT v.version_code, v.osis, v.chapter, v.verse, v.text FROM bibleverse v WHERE {clause}"),
            lookup,
        ).all()
        for code, osis, chapter, verse, body in got:
            shown[(code, (osis, chapter, verse))] = (body, _word_spans(body, strongs_words or []))

    results = []
    for position, code, matched in chosen:
        used = code if (code, position) in shown else fallback_version
        body, spans = shown.get((used or code, position), ("", []))
        osis, chapter, verse = position
        name = BOOK_BY_OSIS.get(osis, {}).get("name", osis)
        results.append({
            "version": used or code,
            "osis": osis,
            "book_name": name,
            "chapter": chapter,
            "verse": verse,
            "reference": f"{name} {chapter}:{verse}",
            "text": body,
            "highlights": spans,
            "versions": matched,
        })
    return results


def run(
    session: Session,
    query: str,
    *,
    version: str,
    installed: list[str],
    mode: str = "all",
    sort: str | None = None,
    limit: int = 50,
    offset: int = 0,
    book: str | None = None,
    username: str | None = None,
    rag_url: str = "",
    secret: str = "",
) -> dict:
    """Search the corpus. ``version`` is the reader's translation; ``installed``
    is every installed code (``ver:`` is checked against it). ``mode`` "meaning"
    needs ``rag_url`` and ``secret`` to embed the question."""
    parsed = parse(query, mode=mode)
    if book:
        scope = resolve_scope(book)
        if not scope:
            raise QueryError(f"Unknown book {book!r}.")
        parsed.scopes.append(scope)
    for code in parsed.versions:
        if code not in installed:
            raise QueryError(f"{code!r} is not an installed translation ({', '.join(installed)})")
    if parsed.all_versions:
        versions = list(installed)
    elif parsed.versions:
        versions = list(dict.fromkeys(parsed.versions))
    else:
        versions = [version]
    primary = version if version in versions else versions[0]
    if parsed.has and not username:
        raise QueryError("has: searches your own notes and highlights; sign in to use it")

    if mode == "meaning":
        return _run_meaning(
            session, parsed, version=primary, limit=limit, offset=offset, username=username,
            rag_url=rag_url, secret=secret,
        )

    stemmed, exact = match_expressions(parsed)
    use_fts = ensure(session)
    if stemmed and not use_fts:
        return _fallback(session, parsed, versions=versions, primary=primary, limit=limit, offset=offset)

    positions: set[tuple[str, int, int]] | None = None
    strongs_words: list[str] = []
    if parsed.strongs:
        positions, strongs_words = _positions_strongs(session, parsed.strongs)
    if parsed.has:
        mine = _positions_marks(session, username or "", parsed.has)
        positions = mine if positions is None else positions & mine
    books = parsed.books()
    if positions is not None and books is not None:
        allowed = set(books)
        positions = {p for p in positions if p[0] in allowed}

    params: dict[str, object] = {}
    where = []
    # With words to match, the full-text index must drive the query. A leading
    # "+" stops SQLite using bibleverse's (version_code, osis, ...) key for
    # these filters: with one translation it otherwise walks all 31,000 of its
    # verses and probes the index for each (1.5 s instead of 1 ms on the live
    # data). Without words (Strong's, has:) the key is the right way in.
    col = "+v." if stemmed else "v."
    where.append(f"{col}version_code IN (" + ", ".join(f":ver{i}" for i in range(len(versions))) + ")")
    params.update({f"ver{i}": code for i, code in enumerate(versions)})
    if books is not None:
        where.append(f"{col}osis IN (" + ", ".join(f":bk{i}" for i in range(len(books))) + ")")
        params.update({f"bk{i}": osis for i, osis in enumerate(books)})

    conn = session.connection()
    if positions is not None:
        if not positions:
            return _empty(parsed, versions, primary, use_fts)
        conn.execute(text("CREATE TEMP TABLE IF NOT EXISTS search_positions (osis TEXT, chapter INT, verse INT)"))
        conn.execute(text("DELETE FROM search_positions"))
        conn.execute(
            text("INSERT INTO search_positions VALUES (:o, :c, :v)"),
            [{"o": o, "c": c, "v": v} for o, c, v in positions],
        )
        join_positions = (
            " JOIN search_positions p ON p.osis = v.osis AND p.chapter = v.chapter AND p.verse = v.verse"
        )
    else:
        join_positions = ""

    if stemmed:
        source = f"{STEMMED} JOIN bibleverse v ON v.rowid = {STEMMED}.rowid{join_positions}"
        where.insert(0, f"{STEMMED} MATCH :stemmed")
        params["stemmed"] = stemmed
        if exact:
            where.append(f"v.rowid IN (SELECT rowid FROM {EXACT} WHERE {EXACT} MATCH :exact)")
            params["exact"] = exact
        score = f"bm25({STEMMED})"
    else:
        source = f"bibleverse v{join_positions}"
        score = "0.0"
    rows_sql = (
        f"SELECT v.osis AS osis, v.chapter AS chapter, v.verse AS verse, v.version_code AS ver, "
        f"{score} AS score FROM {source} WHERE {' AND '.join(where)}"
    )

    by_book_rows = conn.execute(
        text(f"WITH hits AS MATERIALIZED ({rows_sql}) "
             "SELECT osis, count(*) FROM (SELECT DISTINCT osis, chapter, verse FROM hits) GROUP BY osis"),
        params,
    ).all()
    counts = {osis: int(n) for osis, n in by_book_rows}
    total = sum(counts.values())
    order = sort or parsed.sort or ("rank" if stemmed else "canon")
    canon = _CANON_CASE.replace("v.osis", "osis")
    order_sql = f"{canon}, chapter, verse" if order == "canon" else f"score, {canon}, chapter, verse"
    page = conn.execute(
        text(
            f"WITH hits AS MATERIALIZED ({rows_sql}) "
            f"SELECT osis, chapter, verse, min(score) AS score, group_concat(ver) AS vers FROM hits "
            f"GROUP BY osis, chapter, verse ORDER BY {order_sql} LIMIT :limit OFFSET :offset"
        ),
        {**params, "limit": limit, "offset": offset},
    ).all()

    preference = [primary] + [c for c in versions if c != primary]
    chosen: list[tuple[tuple[str, int, int], str, list[str]]] = []
    for osis, chapter, verse, _score, vers in page:
        matched = sorted(set(str(vers).split(",")), key=lambda c: preference.index(c) if c in preference else 99)
        chosen.append(((osis, chapter, verse), matched[0], matched))

    results = _display(conn, chosen, stemmed=stemmed, strongs_words=strongs_words, col=col)

    return {
        "version": primary,
        "versions": versions,
        "index": "fts",
        "sort": order,
        "total": total,
        "count": len(results),
        "offset": offset,
        "by_book": [
            {"osis": o, "name": BOOK_BY_OSIS[o]["name"], "count": counts[o]}
            for o in _ORDER if o in counts
        ],
        "parsed": parsed.describe(),
        "results": results,
    }


def _run_meaning(
    session: Session,
    parsed: ParsedQuery,
    *,
    version: str,
    limit: int,
    offset: int,
    username: str | None,
    rag_url: str,
    secret: str,
) -> dict:
    """Closest in meaning, blended with the word index (see ``meaning.py``).

    The question is embedded as typed (less the filters); the word half uses
    only its content words, as any-word, in the reader's translation.
    """
    from services.bible import meaning

    question = " ".join(
        (t.left.words + " " + t.right.words) if isinstance(t, Near) else t.words
        for clause in parsed.clauses for t in clause
    ).strip()
    if not question:
        raise QueryError("Type what you are looking for, for example: verses about worry")
    if not rag_url:
        raise meaning.MeaningUnavailable("Meaning search is not configured (no RAG service URL).")
    books = parsed.books()
    ranked, matrix = meaning.closest(session, question, rag_url=rag_url, secret=secret, books=books)
    by_meaning = [pos for pos, _score in ranked]

    words = meaning.content_words(question)
    stemmed = None
    by_words: list[tuple[str, int, int]] = []
    if words and ensure(session):
        stemmed = "(" + " OR ".join(_fts_term(Term(w)) for w in dict.fromkeys(words)) + ")"
        params: dict[str, object] = {"stemmed": stemmed, "ver": version}
        scope = ""
        if books is not None:
            scope = " AND +v.osis IN (" + ", ".join(f":bk{i}" for i in range(len(books))) + ")"
            params.update({f"bk{i}": o for i, o in enumerate(books)})
        rows = session.connection().execute(
            text(
                f"SELECT v.osis, v.chapter, v.verse FROM {STEMMED} JOIN bibleverse v ON v.rowid = {STEMMED}.rowid "
                f"WHERE {STEMMED} MATCH :stemmed AND +v.version_code = :ver{scope} "
                f"ORDER BY bm25({STEMMED}) LIMIT 100"
            ),
            params,
        ).all()
        by_words = [(r[0], int(r[1]), int(r[2])) for r in rows]

    fused = meaning.fuse(by_meaning, by_words)
    allowed: set[tuple[str, int, int]] | None = None
    if parsed.strongs:
        allowed, _ = _positions_strongs(session, parsed.strongs)
    if parsed.has:
        mine = _positions_marks(session, username or "", parsed.has)
        allowed = mine if allowed is None else allowed & mine
    if allowed is not None:
        fused = [p for p in fused if p in allowed]
    fused = fused[: meaning.MAX_RESULTS]

    counts: dict[str, int] = {}
    for osis, _c, _v in fused:
        counts[osis] = counts.get(osis, 0) + 1
    chosen = [(pos, version, [version]) for pos in fused[offset : offset + limit]]
    results = _display(session.connection(), chosen, stemmed=stemmed, fallback_version=matrix.version)
    for row in results:
        row["versions"] = [row["version"]]
    return {
        "version": version,
        "versions": [version],
        "index": "meaning",
        "mode": "meaning",
        "sort": "rank",
        "total": len(fused),
        "count": len(results),
        "offset": offset,
        "by_book": [{"osis": o, "name": BOOK_BY_OSIS[o]["name"], "count": counts[o]} for o in _ORDER if o in counts],
        "parsed": parsed.describe(),
        "meaning_version": matrix.version,
        "meaning_coverage": meaning.coverage(session, matrix.version),
        "results": results,
    }


def _empty(parsed: ParsedQuery, versions: list[str], primary: str, use_fts: bool) -> dict:
    return {
        "version": primary, "versions": versions, "index": "fts" if use_fts else "like",
        "sort": parsed.sort or "canon", "total": 0, "count": 0, "offset": 0, "by_book": [],
        "parsed": parsed.describe(), "results": [],
    }


def _fallback(session: Session, parsed: ParsedQuery, *, versions, primary, limit, offset) -> dict:
    """Substring search while the index is being built, or on a non-SQLite database.

    It understands the plain words and phrases of the query (all must appear)
    and the book and translation filters; the rest needs the index, and the
    response says ``index: "like"`` so the screen can say results are partial.
    """
    from services.bible.corpus import sanitize_like
    from services.bible.models import BibleVerse

    stmt = select(BibleVerse).where(BibleVerse.version_code.in_(versions))  # type: ignore[attr-defined]
    for clause in parsed.clauses:
        first = clause[0]
        words = first.left.words if isinstance(first, Near) else first.words
        stmt = stmt.where(BibleVerse.text.like(f"%{sanitize_like(words)}%", escape="\\"))  # type: ignore[attr-defined]
    books = parsed.books()
    if books is not None:
        stmt = stmt.where(BibleVerse.osis.in_(books))  # type: ignore[attr-defined]
    rows = list(session.exec(stmt.limit(5000)).all())
    rows.sort(key=lambda r: (BOOK_ORDER.get(r.osis, 99), r.chapter, r.verse, r.version_code != primary))
    seen: dict[tuple[str, int, int], list] = {}
    for row in rows:
        seen.setdefault((row.osis, row.chapter, row.verse), []).append(row)
    counts: dict[str, int] = {}
    for osis, _c, _v in seen:
        counts[osis] = counts.get(osis, 0) + 1
    results = []
    for (osis, chapter, verse), group in list(seen.items())[offset : offset + limit]:
        row = group[0]
        name = BOOK_BY_OSIS.get(osis, {}).get("name", osis)
        results.append({
            "version": row.version_code, "osis": osis, "book_name": name, "chapter": chapter,
            "verse": verse, "reference": f"{name} {chapter}:{verse}", "text": row.text,
            "highlights": [], "versions": [r.version_code for r in group],
        })
    return {
        "version": primary, "versions": versions, "index": "like", "sort": "canon",
        "total": len(seen), "count": len(results), "offset": offset,
        "by_book": [{"osis": o, "name": BOOK_BY_OSIS[o]["name"], "count": counts[o]} for o in _ORDER if o in counts],
        "parsed": parsed.describe(), "results": results,
    }
