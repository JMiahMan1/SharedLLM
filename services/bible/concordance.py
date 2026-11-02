"""Strong's dictionaries read out of an exhaustive concordance.

A Strong's concordance closes with two dictionaries, Hebrew-Aramaic and
Greek, numbered 1 to 8674 and 1 to 5624. Each entry, as *The Strongest
Strong's* (Zondervan, 2001) prints it, reads::

    2744 καυχάομαι, kauchaomai, v. GK: 3016 [-> 2620, 2745, 2746]. to boast,
    brag about; to rejoice in, glory in:- glory [20], boast [6], rejoice [4]

number, the word, its transliteration, part of speech, the Goodrick-Kohlenberger
number, related entries, the definition, then ``:-`` and every way the KJV
renders it with how often. The family's copy is a scan with an OCR text layer,
so the original script comes out garbled ("KGtToiKeoj") and brackets come out
as ``|`` or ``)``. Only what OCR keeps reliably is kept: the number, the
transliteration, the part of speech, the definition and the renderings. The
word itself is taken from the interlinear, which carries it intact.

Entries are found by their numbers running in order, which is what makes a
noisy page readable: a line opening with the next number starts the next
entry, and anything else continues the current one.
"""

from __future__ import annotations

import json
import re
from bisect import bisect_left
from dataclasses import dataclass, field
from itertools import pairwise

SECTIONS = (
    ("H", re.compile(r"Hebrew-Aramaic Dictionary", re.IGNORECASE), 8674),
    ("G", re.compile(r"[GC]reek Dictionary", re.IGNORECASE), 5624),
)
_ENTRY_START = re.compile(r"^(\d{1,4})\s+(\S.*)$")
_GK = re.compile(r"\bGK:\s*([\d][\d\s&,]*)")
# "[-> 2596+3624]." / ")-+ 2545)." / "[cf. 3045|." -- the related entries.
_RELATED = re.compile(r"^\s*[\[\(\|)\]{}]?\s*(?:[-\u2014]\s*[>\xbb+*\u2666]|cf\.)[^\]\)|}]{0,600}[\]\)|}]\.?\s*")
_USAGE_SPLIT = re.compile(r":\s*-\s*|:\u2014\s*|;-\s*")
_USAGE = re.compile(r"([^,\[\(\|]+?)\s*[\[\(\|]\s*([0-9lIOo]{1,4})\s*[\]\)\|jJ1]")
_POS = re.compile(r"^(?:n|v|a|adv|pp|p|pron|conj|prep|interj|particle|pt|inj|adj|n\.pr)[\w.\[\]|]*", re.IGNORECASE)
_PAREN = re.compile(r"\([^)]*\)")
_WS = re.compile(r"\s+")


@dataclass
class Entry:
    strongs: str  # "H1843", "G2744"
    transliteration: str = ""
    part_of_speech: str = ""
    aramaic: bool = False
    gk: str = ""
    definition: str = ""
    usage: list[tuple[str, int]] = field(default_factory=list)
    text: str = ""

    def as_dict(self) -> dict:
        return {
            "strongs": self.strongs,
            "transliteration": self.transliteration,
            "part_of_speech": self.part_of_speech,
            "aramaic": self.aramaic,
            "gk": self.gk,
            "definition": self.definition,
            "usage": [{"word": w, "count": c} for w, c in self.usage],
            "usage_total": sum(c for _w, c in self.usage),
        }


def _count(raw: str) -> int:
    return int(raw.translate(str.maketrans("lIOo", "1100")))


def _clean(text: str) -> str:
    return _WS.sub(" ", text).strip(" ,;.")


def parse_entry(prefix: str, number: int, text: str) -> Entry:
    entry = Entry(strongs=f"{prefix}{number}", text=text)
    gk = _GK.search(text)
    head = text[: gk.start()] if gk else text.split(":-", 1)[0][:80]
    entry.aramaic = "(Aram.)" in head or "(Aram)" in head
    # OCR often reads the comma before the part of speech as a period ("basileus. n.").
    head = re.sub(r"\.\s+(?=(?:n|v|a|adv|pp|n\.pr)[.\s,]|(?:n|v|a|adv)$)", ", ", head.strip())
    parts = [p.strip() for p in _PAREN.sub(" ", head).split(",") if p.strip()]
    if len(parts) >= 2:
        pos = parts[-1].strip(" .")
        entry.part_of_speech = pos if _POS.match(pos) else ""
        before = parts[-2] if entry.part_of_speech else parts[-1]
        tokens = before.split()
        entry.transliteration = tokens[-1].strip(" .;:") if tokens else ""
    if gk:
        entry.gk = _clean(gk[1].replace(" ", "")).replace("&", ", ")
        rest = _strip_related(text[gk.end():])
    else:
        rest = text[len(head):]
    pieces = _USAGE_SPLIT.split(rest, maxsplit=1)
    entry.definition = _clean(pieces[0])
    if len(pieces) == 2:
        entry.usage = [
            (_clean(w), _count(c)) for w, c in _USAGE.findall(pieces[1]) if re.search(r"[A-Za-z]", w)
        ]
    return entry


# OCR reads a closing bracket as "]", ")", "|", "}" or, after a number, "j".
_RELATED_END = re.compile(r"[\]\)|}]|(?<=[\d\s])j(?=[.\s])")


def _strip_related(text: str) -> str:
    """Drop the related entries that open the definition ("[-> 2596+3624].")."""
    text = _RELATED.sub("", text, count=1)
    end = _RELATED_END.search(text[:600])
    if end:
        prefix = text[: end.start()]
        # Numbers and arrows, not words: "\\u2014> 25]" or "-\u2666 433; cf. 410]".
        if re.search(r"\d", prefix) and not re.search(r"[A-Za-z]{3,}", prefix.replace("cf", "")):
            text = text[end.end():]
    return text.lstrip(" .,]|)")


def _section_lines(pages: list[str], header: re.Pattern[str]) -> list[str]:
    """The dictionary's lines: every page from its first running head to its last.

    A page whose head OCR misread ("Dictionarv") is still inside the run, so
    the run is taken by position rather than page by page.
    """
    marked = [index for index, page in enumerate(pages) if header.search(page[:200])]
    if not marked:
        return []
    lines: list[str] = []
    for page in pages[marked[0] : marked[-1] + 1]:
        for line in page.split("\n"):
            if header.search(line):
                # The running head can share a line with the guide word ("2818 κληρονόμος").
                continue
            lines.append(line.strip())
    return lines


def parse(pages: list[str]) -> dict[str, Entry]:
    """Every dictionary entry, keyed by Strong's number ("H1843")."""
    entries: dict[str, Entry] = {}
    for prefix, header, last in SECTIONS:
        lines = _section_lines(pages, header)
        starts = [(i, int(m[1])) for i, line in enumerate(lines) if (m := _ENTRY_START.match(line))]
        accepted = _numbered(starts, last)
        collected: list[tuple[int, list[str]]] = []
        for index, line in enumerate(lines):
            if index in accepted:
                collected.append((accepted[index], [_ENTRY_START.match(line)[2]]))
            elif collected and line:
                collected[-1][1].append(line)
        for number, parts in collected:
            entries[f"{prefix}{number}"] = parse_entry(prefix, number, _join(parts))
    return entries


def _numbered(starts: list[tuple[int, int]], last: int) -> dict[int, int]:
    """Which lines open an entry, and its number.

    The entries are the longest run of increasing numbers, so a misread
    number ("975" for 915) cannot pull the sequence away from the hundred
    entries around it. A misread line in a gap of exactly its size is then
    given the number the gap says it must have.
    """
    candidates = [(i, n) for i, n in starts if 1 <= n <= last]
    tails: list[int] = []  # numbers
    tail_at: list[int] = []  # index into candidates
    previous: list[int] = [-1] * len(candidates)
    for k, (_i, number) in enumerate(candidates):
        slot = bisect_left(tails, number)
        if slot:
            previous[k] = tail_at[slot - 1]
        if slot == len(tails):
            tails.append(number)
            tail_at.append(k)
        else:
            tails[slot], tail_at[slot] = number, k
    chain: list[int] = []
    k = tail_at[-1] if tail_at else -1
    while k != -1:
        chain.append(k)
        k = previous[k]
    chain.reverse()
    accepted = {candidates[k][0]: candidates[k][1] for k in chain}
    for left, right in pairwise(chain):
        between = range(left + 1, right)
        if len(between) == candidates[right][1] - candidates[left][1] - 1:
            for offset, k in enumerate(between, start=1):
                accepted[candidates[k][0]] = candidates[left][1] + offset
    return accepted


def _join(lines: list[str]) -> str:
    """One entry's lines as one text; a word broken at a line end is mended."""
    text = ""
    for line in lines:
        broken = re.search(r"[a-z]-$", text) and line[:1].islower()
        text = text[:-1] + line if broken else f"{text} {line}".strip()
    return _WS.sub(" ", text)


def looks_like_concordance(pages: list[str]) -> bool:
    """Whether these pages carry both Strong's dictionaries."""
    return all(any(header.search(page[:200]) for page in pages) for _p, header, _l in SECTIONS)


# -- the concordance proper: every verse each word is in -----------------------

_CONCORDANCE_HEAD = re.compile(r"Exhaustive Concordance|The Strongest Strong", re.IGNORECASE)
_HEADWORD = re.compile(r"^([A-Z][A-Z'\u2019\-]+(?: [A-Z][A-Z'\u2019\-]+)*)\s*\((\d+)\)")
_LINE = re.compile(
    r"^(?:(?P<book>(?:[1-3IlL] ?)?[A-Za-z]{1,4})\s+)?(?P<chapter>\d{1,3}):\s?(?P<verse>\d{1,3})\s+"
    r"(?P<context>.+?)\s+(?P<numbers>(?:[\dIlOo]{1,4}(?:\.\d+)?)(?:\s*\+\s*[\dIlOo]{1,4}(?:\.\d+)?)*|NIH|NIG)\s*$"
)
_NAMED_BOOK = re.compile(r"^((?:[1-3IlL] ?)?[A-Za-z]{1,4})\s+\d{1,3}:")
# OCR's readings of the concordance's book abbreviations.
_BOOK_OCR = {"zee": "Zech", "mai": "Mal", "phm": "Phlm", "un": "1John", "lob": "Job", "pnp": "Phil", "ml": "Mal"}


@dataclass(frozen=True)
class Occurrence:
    strongs: str
    osis: str
    chapter: int
    verse: int
    word: str
    context: str


def _concordance_book(token: str) -> str | None:
    from services.bible.books import resolve_book

    token = token.replace(" ", "")  # "1 Jn"
    if not token:
        return None
    lowered = token.lower()
    if lowered in _BOOK_OCR:
        return _BOOK_OCR[lowered]
    if token[0] in "IlL" and len(token) > 2 and token[1].isalpha():
        return resolve_book("1" + token[1:]) or resolve_book(token)
    return resolve_book(token)


def _renders(entry: Entry | None, word: str) -> bool:
    """Whether the dictionary says the KJV renders this entry with ``word``.

    A number OCR misread lands on an entry that never renders the headword
    ("BELIEVED ... 2" is not H2, "father"), so the check throws it out.
    """
    if entry is None:
        return False
    if not entry.usage:  # renderings OCR lost: the definition has to name it
        return word.lower() in entry.definition.lower()
    wanted = word.lower().replace("\u2019", "'")
    return any(wanted in re.split(r"[^a-z']+", rendering.lower().replace("\u2019", "'")) for rendering, _n in entry.usage)


def occurrences(pages: list[str], entries: dict[str, Entry] | None = None) -> list[Occurrence]:
    """Every (Strong's number, verse) the concordance prints, with the line it quotes.

    Only lines that read whole -- a reference, the quotation and the number --
    are kept, and with ``entries`` only those whose headword the dictionary
    lists among that number's renderings. A page whose columns OCR separated
    gives fewer occurrences, never wrong ones.
    """
    from services.bible.books import BOOK_BY_OSIS, BOOKS

    marked = [i for i, page in enumerate(pages) if _CONCORDANCE_HEAD.search(page[:200])]
    if not marked:
        return []
    new_testament = {entry["osis"] for entry in BOOKS[39:]}
    found: list[Occurrence] = []
    word, book = "", None  # an entry runs on across a page break
    for page in pages[marked[0] : marked[-1] + 1]:
        for raw in page.split("\n"):
            line = raw.strip()
            if not line or _CONCORDANCE_HEAD.search(line):
                continue
            head = _HEADWORD.match(line)
            if head:
                word, book = head[1].title(), None
                continue
            # A line that names a book changes the book even when the rest of
            # it cannot be read; otherwise the lines after it would be credited
            # to the book before.
            named = _NAMED_BOOK.match(line)
            if named:
                book = _concordance_book(named[1])
            match = _LINE.match(line)
            if not match or not word:
                continue
            if not book or match["numbers"] in ("NIH", "NIG"):
                continue
            chapter, verse = int(match["chapter"]), int(match["verse"])
            if not 1 <= chapter <= BOOK_BY_OSIS[book]["chapters"] or not 1 <= verse <= 176:
                continue
            prefix, last = ("G", 5624) if book in new_testament else ("H", 8674)
            for number in re.split(r"\s*\+\s*", match["numbers"]):
                value = _count(number.split(".")[0])
                if 1 <= value <= last and (entries is None or _renders(entries.get(f"{prefix}{value}"), word)):
                    found.append(Occurrence(f"{prefix}{value}", book, chapter, verse, word, match["context"]))
    return found


# -- installing and reading ----------------------------------------------------

TAGS = frozenset({"concordance", "strong's", "strongs", "strong's concordance", "jarvis:concordance"})
#: Pages pdftotext reads per run; one run over a whole scanned concordance
#: needs more memory than the service has.
_CHUNK = 40
_STRONGS = re.compile(r"^\s*([GgHh])0*(\d{1,4})[a-zA-Z]?\s*$")


def is_concordance_tag(tag: str) -> bool:
    return tag.strip().lower().replace("\u2019", "'") in TAGS


def normalise(strongs: str) -> str | None:
    """``H0430``, ``h430``, ``H430a`` -> ``H430``; None for anything else."""
    match = _STRONGS.match(strongs or "")
    return f"{match[1].upper()}{int(match[2])}" if match else None


def pdf_pages(path: str) -> list[str]:
    """Every page's text, read the way the content stream orders it (``pdftotext -raw``)."""
    import subprocess

    pages: list[str] = []
    first = 1
    while True:
        result = subprocess.run(
            ["pdftotext", "-q", "-raw", "-f", str(first), "-l", str(first + _CHUNK - 1), path, "-"],
            capture_output=True, text=True, check=False, timeout=600,
        )
        if result.returncode != 0 and not result.stdout:
            break
        chunk = result.stdout.split("\f")
        if chunk and not chunk[-1].strip():
            chunk = chunk[:-1]
        if not chunk:
            break
        pages.extend(chunk)
        if len(chunk) < _CHUNK:
            break
        first += _CHUNK
    return pages


def install(session, pages: list[str], *, source: str) -> dict:
    """Replace the installed concordance with the one these pages hold."""
    from sqlalchemy import delete, insert

    from services.bible.models import ConcordanceLine, LexiconEntry

    if not looks_like_concordance(pages):
        raise ValueError(
            "This file has no Hebrew-Aramaic and Greek dictionaries a Strong's concordance closes with."
        )
    entries = parse(pages)
    found = occurrences(pages, entries)
    session.exec(delete(ConcordanceLine))
    session.exec(delete(LexiconEntry))
    rows = [
        {
            **{k: v for k, v in entry.as_dict().items() if k in {"strongs", "transliteration", "part_of_speech", "gk", "definition", "usage_total"}},
            "language": "greek" if entry.strongs[0] == "G" else "aramaic" if entry.aramaic else "hebrew",
            "usage": json.dumps(entry.as_dict()["usage"], ensure_ascii=False),
            "source": source,
        }
        for entry in entries.values()
    ]
    for start in range(0, len(rows), 2000):
        session.execute(insert(LexiconEntry), rows[start : start + 2000])
    lines = [
        {"strongs": o.strongs, "osis": o.osis, "chapter": o.chapter, "verse": o.verse, "word": o.word, "context": o.context}
        for o in found
    ]
    for start in range(0, len(lines), 5000):
        session.execute(insert(ConcordanceLine), lines[start : start + 5000])
    session.commit()
    return {
        "entries": len(rows),
        "hebrew": sum(1 for r in rows if r["strongs"][0] == "H"),
        "greek": sum(1 for r in rows if r["strongs"][0] == "G"),
        "occurrences": len(lines),
    }


def status(session) -> dict:
    from sqlmodel import func, select

    from services.bible.models import ConcordanceLine, LexiconEntry

    entries = session.exec(select(func.count()).select_from(LexiconEntry)).one()
    source = session.exec(select(LexiconEntry.source).limit(1)).first() or ""
    return {
        "installed": bool(entries),
        "entries": entries,
        "occurrences": session.exec(select(func.count()).select_from(ConcordanceLine)).one() if entries else 0,
        "source": source,
    }


def _interlinear_words(session, strongs: str, *, limit: int | None = None) -> list[tuple[object, dict]]:
    """(verse row, word) for every interlinear word tagged with this number."""
    from sqlmodel import or_, select

    from services.bible.models import InterlinearVerse

    letter, number = strongs[0], int(strongs[1:])
    patterns = {f'%"s": "{letter}{number}%', f'%"s": "{letter}{number:04d}%'}
    query = select(InterlinearVerse).where(or_(*[InterlinearVerse.words.like(p) for p in patterns]))
    found: list[tuple[object, dict]] = []
    for row in session.exec(query.order_by(InterlinearVerse.id)).all():
        for word in json.loads(row.words):
            if normalise(str(word.get("s", ""))) == strongs:
                found.append((row, word))
                if limit and len(found) >= limit:
                    return found
    return found


def entry(session, strongs: str) -> dict | None:
    """One entry as the word study shows it, with the original word when the interlinear has it."""
    from collections import Counter

    from services.bible.models import LexiconEntry

    key = normalise(strongs)
    row = session.get(LexiconEntry, key) if key else None
    if row is None:
        return None
    words = _interlinear_words(session, key, limit=200)
    original = Counter((w.get("l") or w.get("w") or "", w.get("t") or "") for _r, w in words)
    lemma, translit = original.most_common(1)[0][0] if original else ("", "")
    glosses = Counter(w.get("d") or "" for _r, w in words if w.get("d"))
    return {
        "strongs": row.strongs,
        "language": row.language,
        "lemma": lemma,
        "transliteration": row.transliteration or translit,
        "part_of_speech": row.part_of_speech,
        "gk": row.gk,
        "definition": row.definition,
        "gloss": glosses.most_common(1)[0][0] if glosses else "",
        "usage": json.loads(row.usage or "[]"),
        "usage_total": row.usage_total,
        "source": row.source,
    }


def verses(session, strongs: str, *, limit: int = 50, offset: int = 0) -> dict:
    """Where the word is: every verse from the interlinear when one is installed,
    else the verses the concordance's text could be read for."""
    from sqlmodel import func, select

    from services.bible.books import BOOK_BY_OSIS, BOOK_ORDER
    from services.bible.models import ConcordanceLine

    key = normalise(strongs)
    if not key:
        return {"source": "none", "total": 0, "verses": []}
    words = _interlinear_words(session, key)
    if words:
        seen: dict[tuple[str, int, int], list[str]] = {}
        for row, word in words:
            seen.setdefault((row.book, row.chapter, row.verse), []).append(word.get("e") or word.get("t") or "")
        ordered = sorted(seen, key=lambda k: (BOOK_ORDER.get(k[0], 99), k[1], k[2]))
        page = ordered[offset : offset + limit]
        return {
            "source": "interlinear",
            "total": len(ordered),
            "complete": True,
            "verses": [
                {"reference": f"{BOOK_BY_OSIS[b]['name']} {c}:{v}", "osis": b, "chapter": c, "verse": v,
                 "word": ", ".join(dict.fromkeys(w for w in seen[(b, c, v)] if w)), "context": ""}
                for b, c, v in page
            ],
        }
    total = session.exec(select(func.count()).select_from(ConcordanceLine).where(ConcordanceLine.strongs == key)).one()
    rows = session.exec(select(ConcordanceLine).where(ConcordanceLine.strongs == key)).all()
    rows.sort(key=lambda r: (BOOK_ORDER.get(r.osis, 99), r.chapter, r.verse))
    return {
        "source": "concordance",
        "total": total,
        "complete": False,
        "verses": [
            {"reference": f"{BOOK_BY_OSIS[r.osis]['name']} {r.chapter}:{r.verse}", "osis": r.osis,
             "chapter": r.chapter, "verse": r.verse, "word": r.word, "context": r.context}
            for r in rows[offset : offset + limit]
        ],
    }


def verse_words(session, osis: str, chapter: int, verse: int) -> dict:
    """The words of one verse with their Strong's numbers: the interlinear's
    when it has the verse, else the concordance's KJV headwords."""
    from sqlmodel import select

    from services.bible.models import ConcordanceLine, InterlinearVerse, LexiconEntry

    words: list[dict] = []
    row = session.exec(
        select(InterlinearVerse).where(
            InterlinearVerse.book == osis, InterlinearVerse.chapter == chapter, InterlinearVerse.verse == verse
        )
    ).first()
    if row is not None:
        source = "interlinear"
        for word in json.loads(row.words):
            key = normalise(str(word.get("s", "")))
            if key:
                words.append({"word": word.get("e") or "", "original": word.get("w") or "",
                              "transliteration": word.get("t") or "", "strongs": key})
    else:
        source = "concordance"
        lines = session.exec(
            select(ConcordanceLine).where(
                ConcordanceLine.osis == osis, ConcordanceLine.chapter == chapter, ConcordanceLine.verse == verse
            )
        ).all()
        for line in lines:
            words.append({"word": line.word, "original": "", "transliteration": "", "strongs": line.strongs,
                          "context": line.context})
        # In the order the words come in the verse, as near as the quoted line tells.
        words.sort(key=lambda w: (len(w.get("context", "")), w["word"]))
    keys = {w["strongs"] for w in words}
    entries = {e.strongs: e for e in session.exec(select(LexiconEntry).where(LexiconEntry.strongs.in_(keys))).all()} if keys else {}
    for word in words:
        found = entries.get(word["strongs"])
        word["definition"] = found.definition if found else ""
        word["transliteration"] = word["transliteration"] or (found.transliteration if found else "")
        word["in_lexicon"] = found is not None
    return {"source": source, "words": words}


def search(session, query: str, *, limit: int = 30) -> list[dict]:
    """Entries by number ("G26"), transliteration ("agape") or English rendering ("love")."""
    from sqlmodel import or_, select

    from services.bible.models import LexiconEntry

    text = query.strip()
    if not text:
        return []
    key = normalise(text)
    if key:
        found = session.get(LexiconEntry, key)
        return [{"strongs": found.strongs, "transliteration": found.transliteration, "definition": found.definition,
                 "usage_total": found.usage_total}] if found else []
    like = f"%{text.lower()}%"
    rows = session.exec(
        select(LexiconEntry)
        .where(or_(LexiconEntry.transliteration.ilike(like), LexiconEntry.usage.ilike(f'%"word": "{text.lower()}%'),
                   LexiconEntry.usage.ilike(f'%"word": "{text}%')))
        .order_by(LexiconEntry.usage_total.desc())
        .limit(300)
    ).all()
    wanted = text.lower()

    def rendered(row) -> int:
        # How often the KJV renders this entry with the searched word: "love"
        # is agape and agapao before thelo, which it renders "love" once.
        return sum(u["count"] for u in json.loads(row.usage or "[]") if u["word"].lower().strip("*") == wanted)

    rows = sorted(rows, key=lambda r: (-rendered(r), r.transliteration.lower() != wanted, -r.usage_total))[:limit]
    return [{"strongs": r.strongs, "transliteration": r.transliteration, "definition": r.definition,
             "usage_total": r.usage_total} for r in rows]
