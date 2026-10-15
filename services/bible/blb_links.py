# services/bible/blb_links.py
"""Deep links into Blue Letter Bible, in the URL scheme blb.org actually serves.

Verified against blueletterbible.org (2026-10):

* A verse is ``/{version}/{book}/{chapter}/{verse}/`` with BLB's own
  three-letter book codes (``jhn``, ``sng``, ``phl``) -- not OSIS. Lower-cased
  OSIS (``/john/3/16``) happened to redirect for some books and not others.
* The interlinear (Strong's concordance) tab is the verse page plus
  ``t_conc_{id}``, where ``id`` is the chapter's position in the whole Bible
  times 1000 plus the verse: Gen 1:1 is 1001, Ps 119:176 is 597176, John 3:16
  is 1000016, Rev 22:21 is 1189021. The old ``/tools/interlinear/...`` path is
  a 404.
"""
from __future__ import annotations

from services.bible.books import BOOKS

#: BLB's book codes, keyed by our OSIS id. Every one was fetched as
#: ``/kjv/{code}/1/1/`` and answered with that book's chapter 1.
BLB_BOOK: dict[str, str] = {
    "Gen": "gen", "Exod": "exo", "Lev": "lev", "Num": "num", "Deut": "deu",
    "Josh": "jos", "Judg": "jdg", "Ruth": "rth", "1Sam": "1sa", "2Sam": "2sa",
    "1Kgs": "1ki", "2Kgs": "2ki", "1Chr": "1ch", "2Chr": "2ch", "Ezra": "ezr",
    "Neh": "neh", "Esth": "est", "Job": "job", "Ps": "psa", "Prov": "pro",
    "Eccl": "ecc", "Song": "sng", "Isa": "isa", "Jer": "jer", "Lam": "lam",
    "Ezek": "eze", "Dan": "dan", "Hos": "hos", "Joel": "joe", "Amos": "amo",
    "Obad": "oba", "Jonah": "jon", "Mic": "mic", "Nah": "nah", "Hab": "hab",
    "Zeph": "zep", "Hag": "hag", "Zech": "zec", "Mal": "mal",
    "Matt": "mat", "Mark": "mar", "Luke": "luk", "John": "jhn", "Acts": "act",
    "Rom": "rom", "1Cor": "1co", "2Cor": "2co", "Gal": "gal", "Eph": "eph",
    "Phil": "phl", "Col": "col", "1Thess": "1th", "2Thess": "2th", "1Tim": "1ti",
    "2Tim": "2ti", "Titus": "tit", "Phlm": "phm", "Heb": "heb", "Jas": "jas",
    "1Pet": "1pe", "2Pet": "2pe", "1John": "1jo", "2John": "2jo", "3John": "3jo",
    "Jude": "jde", "Rev": "rev",
}

#: Translations blb.org serves under its own slug (checked 2026-10). Anything
#: else opens in the KJV, whose Strong's tagging the interlinear is built on.
BLB_VERSIONS = frozenset(
    {"kjv", "nkjv", "esv", "niv", "nlt", "csb", "nasb20", "nasb95", "lsb", "net", "rsv", "asv", "web", "amp", "ylt"}
)

_CHAPTERS_BEFORE: dict[str, int] = {}
_total = 0
for _b in BOOKS:
    _CHAPTERS_BEFORE[_b["osis"]] = _total
    _total += _b["chapters"]


def blb_version(version: str | None) -> str:
    v = (version or "").strip().lower()
    return v if v in BLB_VERSIONS else "kjv"


def concordance_id(osis: str, chapter: int, verse: int) -> int:
    """BLB's verse id: the chapter's place in the Bible * 1000 + verse."""
    return (_CHAPTERS_BEFORE[osis] + chapter) * 1000 + verse


def verse_path(osis: str, chapter: int | None, verse: int | None, version: str | None = None) -> str:
    """``/kjv/jhn/3/16/``; a whole chapter or book links to its first verse."""
    return f"/{blb_version(version)}/{BLB_BOOK[osis]}/{chapter or 1}/{verse or 1}/"


def interlinear_path(osis: str, chapter: int | None, verse: int | None, version: str | None = None) -> str:
    """The verse page opened on its Interlinear/Concordance tab."""
    c, v = chapter or 1, verse or 1
    return f"{verse_path(osis, c, v, version)}t_conc_{concordance_id(osis, c, v)}"


def lexicon_path(strongs: str) -> str | None:
    """BLB's Strong's lexicon page: ``G0025`` -> ``/lexicon/g25/kjv/tr/0-1/``.

    STEPBible's disambiguation suffix (``H7225G``) is dropped, and its private
    H9000+ numbers (prefixes such as "and", "the") have no lexicon page.
    """
    import re

    m = re.match(r"^([GH])0*(\d+)", (strongs or "").strip().upper())
    if not m:
        return None
    letter, number = m.group(1).lower(), int(m.group(2))
    if letter == "h" and number >= 9000:
        return None
    return f"/lexicon/{letter}{number}/kjv/{'tr' if letter == 'g' else 'wlc'}/0-1/"
