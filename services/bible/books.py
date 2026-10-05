# services/bible/books.py
"""Canonical 66-book table for the Bible service.

One table, one source of truth for book identity. ``osis`` is the canonical
id we store in SQLite and use in API paths and BLB deep links
(``blb.org/{osis}/{chapter}/{verse}``) because BLB's own URLs are keyed on
it. ``aliases`` carries the spellings people actually type -- "Jn", "1 Cor",
"Rev", "Ps" -- because a reference the parser cannot read is a reference the
reader cannot open.

Chapter counts are hard data (the number of chapters in each book never
changes), so they are validated against the corpus at import time instead of
being trusted at request time.
"""
from __future__ import annotations

# (osis, name, chapters, aliases)
_BOOKS: tuple[tuple[str, str, int, tuple[str, ...]], ...] = (
    ("Gen", "Genesis", 50, ("Ge", "Gn")),
    ("Exod", "Exodus", 40, ("Ex", "Exo")),
    ("Lev", "Leviticus", 27, ("Le", "Lv")),
    ("Num", "Numbers", 36, ("Nu", "Nm")),
    ("Deut", "Deuteronomy", 34, ("De", "Dt", "Deu")),
    ("Josh", "Joshua", 24, ("Jos", "Jsh")),
    ("Judg", "Judges", 21, ("Jdg", "Jg")),
    ("Ruth", "Ruth", 4, ("Rth", "Ru")),
    ("1Sam", "1 Samuel", 31, ("1 Sam", "1Sam", "1Sa", "First Samuel")),
    ("2Sam", "2 Samuel", 24, ("2 Sam", "2Sam", "2Sa", "Second Samuel")),
    ("1Kgs", "1 Kings", 22, ("1 Kgs", "1Ki", "1 Kings")),
    ("2Kgs", "2 Kings", 25, ("2 Kgs", "2Ki", "2 Kings")),
    ("1Chr", "1 Chronicles", 29, ("1 Chr", "1Ch", "1Chron")),
    ("2Chr", "2 Chronicles", 36, ("2 Chr", "2Ch", "2Chron")),
    ("Ezra", "Ezra", 10, ("Ezr")),
    ("Neh", "Nehemiah", 13, ("Ne")),
    ("Esth", "Esther", 10, ("Es", "Est")),
    ("Job", "Job", 42, ()),
    ("Ps", "Psalms", 150, ("Psa", "Psm", "Psalm")),
    ("Prov", "Proverbs", 31, ("Pr", "Pro")),
    ("Eccl", "Ecclesiastes", 12, ("Ec", "Ecc", "Qoh")),
    ("Song", "Song of Songs", 8, ("Canticles", "SS", "Cant")),
    ("Isa", "Isaiah", 66, ("Is")),
    ("Jer", "Jeremiah", 52, ("Je", "Jr")),
    ("Lam", "Lamentations", 5, ("La", "Lament")),
    ("Ezek", "Ezekiel", 48, ("Eze", "Ezk")),
    ("Dan", "Daniel", 12, ("Da", "Dn")),
    ("Hos", "Hosea", 14, ("Ho")),
    ("Joel", "Joel", 3, ("Joe", "Jl")),
    ("Amos", "Amos", 9, ("Am")),
    ("Obad", "Obadiah", 1, ("Ob", "Oba")),
    ("Jonah", "Jonah", 4, ("Jon", "Jnh")),
    ("Mic", "Micah", 7, ("Mi")),
    ("Nah", "Nahum", 3, ("Na")),
    ("Hab", "Habakkuk", 3, ("Hb", "Habk")),
    ("Zeph", "Zephaniah", 3, ("Zep", "Zp")),
    ("Hag", "Haggai", 2, ("Hg")),
    ("Zech", "Zechariah", 14, ("Zec", "Zc")),
    ("Mal", "Malachi", 4, ("Ml")),
    ("Matt", "Matthew", 28, ("Mt", "Mat")),
    ("Mark", "Mark", 16, ("Mk", "Mr", "Mrk")),
    ("Luke", "Luke", 24, ("Lk", "Luk")),
    ("John", "John", 21, ("Jn", "Jhn", "Joh")),
    ("Acts", "Acts", 28, ("Ac", "Act")),
    ("Rom", "Romans", 16, ("Ro", "Rm")),
    ("1Cor", "1 Corinthians", 16, ("1 Cor", "1Cor", "1Co")),
    ("2Cor", "2 Corinthians", 13, ("2 Cor", "2Cor", "2Co")),
    ("Gal", "Galatians", 6, ("Ga")),
    ("Eph", "Ephesians", 6, ("Ep")),
    ("Phil", "Philippians", 4, ("Php", "Phi")),
    ("Col", "Colossians", 4, ("Co")),
    ("1Thess", "1 Thessalonians", 5, ("1 Thess", "1Thess", "1Th")),
    ("2Thess", "2 Thessalonians", 3, ("2 Thess", "2Thess", "2Th")),
    ("1Tim", "1 Timothy", 6, ("1 Tim", "1Tim", "1Ti")),
    ("2Tim", "2 Timothy", 4, ("2 Tim", "2Tim", "2Ti")),
    ("Titus", "Titus", 3, ("Tit", "Ti")),
    ("Phlm", "Philemon", 1, ("Philem", "Pm", "Phile")),
    ("Heb", "Hebrews", 13, ("He")),
    ("Jas", "James", 5, ("Jam", "Jm")),
    ("1Pet", "1 Peter", 5, ("1 Pet", "1Pet", "1Pe")),
    ("2Pet", "2 Peter", 3, ("2 Pet", "2Pet", "2Pe")),
    ("1John", "1 John", 5, ("1 John", "1John", "1Jn")),
    ("2John", "2 John", 1, ("2 John", "2John", "2Jn")),
    ("3John", "3 John", 1, ("3 John", "3John", "3Jn")),
    ("Jude", "Jude", 1, ("Jud", "Jude")),
    ("Rev", "Revelation", 22, ("Re", "Rv", "Apocalypse")),
)

BOOKS = tuple(
    {"osis": osis, "name": name, "chapters": chapters, "order": index + 1}
    for index, (osis, name, chapters, _aliases) in enumerate(_BOOKS)
)

BOOK_BY_OSIS: dict[str, dict] = {b["osis"]: b for b in BOOKS}
BOOK_ORDER: dict[str, int] = {b["osis"]: b["order"] for b in BOOKS}


def _normalize(token: str) -> str:
    """Fold a book token to its lookup form: lowercase, no spaces or periods."""
    return token.strip().lower().replace(".", "").replace(" ", "")


_ALIAS_INDEX: dict[str, str] = {}
for _osis, _name, _chapters, _aliases in _BOOKS:
    for _token in (_osis, _name, *_aliases):
        _key = _normalize(_token)
        # First writer wins so an earlier, more canonical spelling is not
        # shadowed by a later synonym (e.g. "ps" stays Psalm->Ps).
        _ALIAS_INDEX.setdefault(_key, _osis)


def resolve_book(token: str) -> str | None:
    """Return the OSIS id for a book name/abbreviation, or None if unknown."""
    if not token:
        return None
    return _ALIAS_INDEX.get(_normalize(token))


def book(osis: str) -> dict:
    """Look up a canonical book row, raising KeyError with the bad id."""
    try:
        return BOOK_BY_OSIS[osis]
    except KeyError:
        raise KeyError(f"unknown book osis: {osis!r}") from None