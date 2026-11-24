"""Fill in a book before it is added: what the file says, what the catalogues
know, and which of the library's tags fit.

Three sources, best first, each field taken from the first that has it:

1. The file itself: an EPUB's OPF (title, authors, subjects, description,
   ISBN, publisher, date, language, cover) or a PDF's document info.
2. Google Books, then Open Library, searched by ISBN when there is one and by
   title and author otherwise (both free, no key; a failure is skipped).
3. Tags: the library's own tags that match the book's subjects, plus the
   topics the app's features read (fasting, prayer, bible-study...) when the
   title, subjects or description call for them.

Nothing is saved here. The person sees every field filled in, changes what
they like, and only then adds the book.
"""
from __future__ import annotations

import base64
import io
import logging
import os
import re
from typing import Any
from urllib.parse import quote, urlparse

import aiohttp

from services.storage.calibre_writer import clean_isbn, epub_details

log = logging.getLogger(__name__)

TIMEOUT = aiohttp.ClientTimeout(total=12)
#: Covers are fetched only from the catalogues themselves.
COVER_HOSTS = {"covers.openlibrary.org", "books.google.com", "books.googleusercontent.com"}
MAX_COVER_BYTES = 5 * 1024 * 1024

#: The app's topics and the words that call for them.
TOPICS: dict[str, tuple[str, ...]] = {
    "fasting": ("fasting", "fast", "fasts", "lent", "lenten", "asceticism", "ascetic"),
    "prayer": ("prayer", "praying", "intercession", "devotion"),
    "lent": ("lent", "lenten", "great lent", "pascha", "holy week"),
    "bible-study": ("bible study", "commentary", "commentaries", "exegesis", "biblical studies", "hermeneutics"),
    "early-church": ("early church", "church fathers", "patristic", "fathers of the church", "apostolic fathers", "desert fathers"),
    "health": ("health", "nutrition", "diet", "fitness", "wellness"),
}


def _blank() -> dict[str, Any]:
    return {"title": "", "authors": [], "subjects": [], "comments": "", "publisher": "", "published": "",
            "isbn": "", "language": "", "series": "", "cover": None, "cover_url": ""}


def pdf_details(data: bytes) -> dict[str, Any]:
    """A PDF's document info (title, author, subject, keywords), if it has any."""
    out = _blank()
    try:
        from pypdf import PdfReader

        info = PdfReader(io.BytesIO(data)).metadata or {}
    except Exception as exc:  # an unreadable PDF just has no details
        log.info("[lookup] PDF info unreadable: %s", exc)
        return out
    title = str(info.get("/Title") or "").strip()
    author = str(info.get("/Author") or "").strip()
    out["title"] = "" if re.fullmatch(r"(?i)untitled|microsoft word.*|\.?doc.*", title) else title
    out["authors"] = [a.strip() for a in re.split(r"\s*(?:;|&|\band\b)\s*", author) if a.strip()] if author else []
    words = " ; ".join(str(info.get(k) or "") for k in ("/Subject", "/Keywords"))
    out["subjects"] = [w.strip() for w in re.split(r"[;,]", words) if w.strip()][:20]
    return out


def file_details(data: bytes, filename: str) -> dict[str, Any]:
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext == "epub":
        found = epub_details(data)
        out = _blank()
        out.update({k: found.get(k) or out[k] for k in ("title", "authors", "comments", "publisher", "published", "isbn", "language", "cover")})
        out["subjects"] = found.get("tags") or []
        return out
    if ext == "pdf":
        return pdf_details(data)
    return _blank()


def _from_stem(filename: str) -> dict[str, Any]:
    """"Great Lent - Alexander Schmemann.pdf" names its title and author."""
    out = _blank()
    stem = re.sub(r"[_]+", " ", filename.rsplit(".", 1)[0]).strip()
    parts = [p.strip() for p in re.split(r"\s+-\s+", stem) if p.strip()]
    if len(parts) == 2:
        out["title"], out["authors"] = parts[0], [parts[1]]
    elif stem:
        out["title"] = stem
    return out


async def _json(session: aiohttp.ClientSession, url: str) -> dict:
    try:
        async with session.get(url, timeout=TIMEOUT) as resp:
            if resp.status != 200:
                return {}
            return await resp.json(content_type=None)
    except Exception as exc:  # a catalogue that is down is skipped
        log.info("[lookup] %s failed: %s", urlparse(url).netloc, exc)
        return {}


async def google_books(session: aiohttp.ClientSession, title: str, author: str, isbn: str) -> dict[str, Any]:
    if isbn:
        q = f"isbn:{isbn}"
    elif title:
        q = f"intitle:{title}" + (f"+inauthor:{author}" if author else "")
    else:
        return _blank()
    # Without a key Google shares one daily quota among everyone and soon
    # answers 429; GOOGLE_BOOKS_API_KEY (free) gives the family its own.
    key = os.getenv("GOOGLE_BOOKS_API_KEY", "").strip()
    url = f"https://www.googleapis.com/books/v1/volumes?maxResults=3&q={quote(q)}" + (f"&key={quote(key)}" if key else "")
    data = await _json(session, url)
    items = data.get("items") or []
    if not items:
        return _blank()
    info = items[0].get("volumeInfo") or {}
    out = _blank()
    out["title"] = " : ".join(x for x in (info.get("title"), info.get("subtitle")) if x)
    out["authors"] = list(info.get("authors") or [])
    out["subjects"] = [part.strip() for cat in info.get("categories") or [] for part in str(cat).split("/") if part.strip()]
    out["comments"] = re.sub(r"<[^>]+>", "", str(info.get("description") or "")).strip()
    out["publisher"] = str(info.get("publisher") or "")
    out["published"] = str(info.get("publishedDate") or "")[:10]
    out["language"] = str(info.get("language") or "")
    for ident in info.get("industryIdentifiers") or []:
        if ident.get("type") in ("ISBN_13", "ISBN_10") and (found := clean_isbn(ident.get("identifier", ""))):
            out["isbn"] = found
            break
    thumb = (info.get("imageLinks") or {}).get("thumbnail") or ""
    if thumb:
        out["cover_url"] = thumb.replace("http://", "https://").replace("&edge=curl", "").replace("zoom=1", "zoom=2")
    return out


#: What an Open Library search must be asked for: since 2025 its default answer has
#: no subjects, publishers or ISBNs, which left every looked-up book without tags.
OPEN_LIBRARY_FIELDS = "key,title,author_name,subject,publisher,first_publish_year,isbn,language,cover_i"


async def open_library(session: aiohttp.ClientSession, title: str, author: str, isbn: str) -> dict[str, Any]:
    if isbn:
        url = f"https://openlibrary.org/search.json?limit=1&isbn={quote(isbn)}"
    elif title:
        url = f"https://openlibrary.org/search.json?limit=1&title={quote(title)}" + (f"&author={quote(author)}" if author else "")
    else:
        return _blank()
    url += f"&fields={OPEN_LIBRARY_FIELDS}"
    docs = (await _json(session, url)).get("docs") or []
    if not docs:
        return _blank()
    doc = docs[0]
    out = _blank()
    out["title"] = str(doc.get("title") or "")
    out["authors"] = list(doc.get("author_name") or [])[:4]
    out["subjects"] = list(doc.get("subject") or [])[:25]
    out["publisher"] = (doc.get("publisher") or [""])[0]
    if doc.get("first_publish_year"):
        out["published"] = str(doc["first_publish_year"])
    out["isbn"] = next((clean_isbn(i) for i in doc.get("isbn") or [] if clean_isbn(i)), "")
    # A work lists the language of every translation; English when it is one,
    # otherwise only an unambiguous single language.
    languages = list(doc.get("language") or [])
    out["language"] = "eng" if "eng" in languages else (languages[0] if len(languages) == 1 else "")
    if doc.get("cover_i"):
        out["cover_url"] = f"https://covers.openlibrary.org/b/id/{doc['cover_i']}-L.jpg"
    if doc.get("key"):
        work = await _json(session, f"https://openlibrary.org{doc['key']}.json")
        desc = work.get("description")
        out["comments"] = str(desc.get("value") if isinstance(desc, dict) else desc or "").strip()
    return out


#: How many tags a book is given: a few that say what it is, not a subject index.
MAX_TAGS = 5
#: A library tag used by fewer books than this is not offered from a subject
#: (one-off catalogue strings such as "1725-1798" or "etc.").
MIN_TAG_USES = 2


#: Catalogue subjects that say nothing about what a book is (Open Library's lending
#: shelves, a language, "general").
_EMPTY_SUBJECTS = frozenset({
    "general", "accessible book", "protected daisy", "in library", "lending library", "large type books",
    "english", "english language", "fiction in english", "open library staff picks", "ebook", "ebooks", "book",
    "books", "internet archive wishlist", "long now manual for civilization",
})


def _usable_tag(name: str) -> bool:
    """A library tag that names a genre or subject, not a fragment of a catalogue heading."""
    name = name.strip()
    if name.lower() in _EMPTY_SUBJECTS:
        return False
    return 2 < len(name) <= 40 and "--" not in name and bool(re.search(r"[A-Za-z]{3}", name)) \
        and not re.fullmatch(r"[\d\s.,-]+", name) and not re.fullmatch(r"[A-Za-z]{1,4}\.", name)


def _subject_parts(subject: str) -> list[str]:
    """"England -- Fiction", "Fiction / Christian" and "Fiction, general" name several subjects each."""
    return [p.strip() for p in re.split(r"\s*(?:--|/|;|,)\s*", str(subject)) if p.strip()]


def suggest_tags(found: dict[str, Any], existing: list[str], limit: int = MAX_TAGS,
                 usage: dict[str, int] | None = None) -> list[dict[str, Any]]:
    """A small, consistent set of tags for the book, from the library's own tags.

    Tags are never invented from a catalogue's subject strings (the library already
    has two thousand of those): a subject, or a part of one, is kept only when the
    library already uses that tag, most used first, so every book lands on the same
    shelves. The app's topics (fasting, prayer...) are added when the title,
    subjects or description call for them.
    """
    usage = usage or {}
    by_lower = {t.lower(): t for t in existing}
    picked: dict[str, dict[str, Any]] = {}
    candidates: list[tuple[int, str, str]] = []
    for subject in found.get("subjects") or []:
        for part in [str(subject).strip(), *_subject_parts(subject)]:
            name = by_lower.get(part.lower())
            if not name or not _usable_tag(name):
                continue
            uses = usage.get(name, MIN_TAG_USES if not usage else 0)
            if uses < MIN_TAG_USES:
                continue
            candidates.append((uses, name, str(subject)))
    for _uses, name, subject in sorted(candidates, key=lambda c: (-c[0], c[1].lower())):
        if name.lower() not in picked:
            picked[name.lower()] = {"name": name, "why": f"subject “{subject}”", "existing": True}
    if len(picked) < limit:
        # Then a library tag the subject names in so many words ("American poetry" is
        # Poetry), most used first: the library's shelf, never the catalogue's heading.
        shelves = sorted((n for n in existing if _usable_tag(n) and usage.get(n, 0) >= MIN_TAG_USES
                          and len(n) >= 4 and n.lower() not in picked), key=lambda n: (-usage.get(n, 0), n.lower()))
        subjects = [str(x).lower() for x in found.get("subjects") or []]
        for name in shelves:
            if len(picked) >= limit:
                break
            pattern = rf"\b{re.escape(name.lower())}\b"
            subject = next((x for x in subjects if re.search(pattern, x)), None)
            if subject:
                picked[name.lower()] = {"name": name, "why": f"subject “{subject}”", "existing": True}
    text = " ".join([found.get("title") or "", " ".join(found.get("subjects") or []), (found.get("comments") or "")[:2000]]).lower()
    topics: list[dict[str, Any]] = []
    for topic, words in TOPICS.items():
        if topic in picked:
            continue
        # Whole words only: "breakfast" is not a fast.
        hit = next((w for w in words if re.search(rf"\b{re.escape(w)}\b", text)), None)
        if hit:
            name = by_lower.get(topic) or by_lower.get(f"jarvis:{topic}") or topic
            topics.append({"name": name, "why": f"mentions “{hit.strip()}”", "existing": name.lower() in by_lower})
    out = [*picked.values(), *[t for t in topics if t["name"].lower() not in picked]]
    return out[:limit]


# ── Is a catalogue's answer this book? ──────────────────────────────────────

#: How alike a catalogue's title must be to the book's (after dropping subtitles).
TITLE_MATCH = 0.75


def _norm_words(text: str) -> str:
    text = re.sub(r"['\u2019]", "", str(text or "").lower())
    return " ".join(w for w in re.findall(r"[a-z0-9]+", text) if w not in ("the", "a", "an"))


def _main_title(title: str) -> str:
    return re.split(r"\s*[:(\[]\s*|\s+-\s+", str(title or ""), maxsplit=1)[0]


def _edition_of(one: str, other: str) -> bool:
    """"wild at heart" and "wild at heart revised updated": one title, an edition note added.

    The shorter must be at least two words and half of the longer, so "book" is not
    "book thief".
    """
    short, long = sorted((one.split(), other.split()), key=len)
    return len(short) >= 2 and len(short) * 2 >= len(long) and long[:len(short)] == short


def same_book(title: str, authors: list[str], found: dict[str, Any]) -> bool:
    """Whether a catalogue result is the book asked about, not just its first search hit.

    A search for the file name "book" answered "The Book Thief"; taking the first hit
    unchecked files a book under someone else's title. The result's main title must
    match the asked title (either may carry a subtitle or an edition note), and when
    an author is known one of the result's authors must share a surname with them.
    """
    from difflib import SequenceMatcher

    if not found.get("title") or not title:
        return False
    want, got = _norm_words(_main_title(title)), _norm_words(_main_title(found["title"]))
    full_want, full_got = _norm_words(title), _norm_words(found["title"])
    if not want or not got:
        return False
    alike = max(SequenceMatcher(None, want, got).ratio(),
                1.0 if _edition_of(full_want, full_got) else 0.0)
    if alike < TITLE_MATCH:
        return False
    asked = [a for a in authors if a.strip() and a.strip().lower() != "unknown"]
    if not asked or not found.get("authors"):
        return True
    surnames = {(_norm_words(a).split() or [""])[-1] for a in asked}
    return any((_norm_words(a).split() or [""])[-1] in surnames for a in found["authors"])


async def fetch_cover(url: str) -> bytes | None:
    """A catalogue's cover image; only from the catalogues' own hosts, over HTTPS."""
    parsed = urlparse(url or "")
    if parsed.scheme != "https" or parsed.hostname not in COVER_HOSTS:
        return None
    try:
        async with aiohttp.ClientSession() as session, session.get(url, timeout=TIMEOUT, allow_redirects=False) as resp:
            if resp.status != 200 or not str(resp.headers.get("Content-Type", "")).startswith("image/"):
                return None
            data = await resp.content.read(MAX_COVER_BYTES + 1)
            # Open Library answers a 1x1 GIF when it has no cover.
            return data if 1000 < len(data) <= MAX_COVER_BYTES else None
    except Exception as exc:
        log.info("[lookup] cover %s failed: %s", parsed.hostname, exc)
        return None


async def inspect(data: bytes, filename: str, existing_tags: list[str], *, online: bool = True,
                  usage: dict[str, int] | None = None, known: dict[str, Any] | None = None) -> dict[str, Any]:
    """Every field Jarvis can find for a book, with where each one came from."""
    base = file_details(data, filename)
    out = await lookup(base, filename, existing_tags, online=online, usage=usage, known=known)
    out.pop("cover", None)  # the preview carries it; bytes are not JSON
    return out


async def lookup(base: dict[str, Any], filename: str, existing_tags: list[str], *, online: bool = True,
                 usage: dict[str, int] | None = None, known: dict[str, Any] | None = None) -> dict[str, Any]:
    """Every field Jarvis can find for a book: ``base`` (what the file says), then the catalogues.

    ``known`` are fields the library or the person already has (a title typed in,
    a book's catalogue row): they are what the catalogues are searched by and what
    their answers are checked against. A catalogue answer that is not the same
    book (``same_book``) is dropped, and ``identified`` says whether any was kept.
    """
    known = known or {}
    layers: list[tuple[str, dict[str, Any]]] = [("the file", base)]
    stem = _from_stem(filename)
    title = str(known.get("title") or base["title"] or stem["title"] or "")
    authors = list(known.get("authors") or base["authors"] or stem["authors"] or [])
    isbn = str(known.get("isbn") or base["isbn"] or "")
    identified = False
    rejected: list[str] = []
    if online and (title or isbn):
        author = authors[0] if authors else ""
        async with aiohttp.ClientSession(headers={"User-Agent": "JarvisOS-Library/1.0"}) as session:
            for source, fetch in (("Google Books", google_books), ("Open Library", open_library)):
                found = await fetch(session, title, author, isbn)
                if not found.get("title"):
                    continue
                # An ISBN search is the book by definition; a title search must be checked.
                if (isbn and found.get("isbn") == isbn) or same_book(title, authors, found):
                    layers.append((source, found))
                    identified = True
                else:
                    rejected.append(f"{source} suggested “{found['title']}”")
    layers.append(("the file name", stem))

    out: dict[str, Any] = {}
    sources: dict[str, str] = {}
    for key in ("title", "authors", "comments", "publisher", "published", "isbn", "language", "series", "cover_url"):
        for source, found in layers:
            if found.get(key):
                out[key] = found[key]
                sources[key] = source
                break
        else:
            out[key] = [] if key == "authors" else ""
    subjects: list[str] = []
    for _, found in layers:
        subjects += [s for s in found.get("subjects") or [] if s not in subjects]
    out["subjects"] = subjects[:30]
    cover = base.get("cover")
    if cover:
        sources["cover"] = "the file"
        out["cover_url"] = ""
    elif out.get("cover_url"):
        sources["cover"] = sources.pop("cover_url", "a catalogue")
    out["cover_preview"] = f"data:image/jpeg;base64,{base64.b64encode(cover).decode('ascii')}" if cover and len(cover) < 2_000_000 else ""
    out["tags"] = suggest_tags(out, existing_tags, usage=usage)
    out["sources"] = sources
    out["cover"] = cover
    out["identified"] = identified
    out["rejected"] = rejected
    return out


# ── Filling in a book that is being added, or one already on the shelf ──────

#: The fields a book is filled in with, in the order a reply names them.
FIELDS = ("title", "authors", "series", "publisher", "published", "isbn", "comments", "language")


def _given(value: Any) -> bool:
    if isinstance(value, list):
        return any(str(v).strip() and str(v).strip().lower() != "unknown" for v in value)
    text = str(value or "").strip()
    return bool(text) and text.lower() not in ("unknown", "untitled")


def merge(given: dict[str, Any], found: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
    """``given`` (typed by a person, or already in the library) wins; ``found`` fills the rest.

    Returns the merged fields and, for each field ``found`` filled, where it came from.
    """
    merged: dict[str, Any] = {}
    filled: dict[str, str] = {}
    sources = found.get("sources") or {}
    for key in FIELDS:
        if _given(given.get(key)):
            merged[key] = given[key]
        elif _given(found.get(key)):
            merged[key] = found[key]
            filled[key] = str(sources.get(key) or "a catalogue")
        else:
            merged[key] = [] if key == "authors" else ""
    if _given(given.get("tags")):
        merged["tags"] = list(given["tags"])
    else:
        merged["tags"] = [t["name"] for t in found.get("tags") or []]
        if merged["tags"]:
            filled["tags"] = "the library's tags"
    return merged, filled


def report(title: str, identified: bool, filled: dict[str, str], rejected: list[str]) -> str:
    """One sentence for the person: what was filled in, or that the book was not identified."""
    if identified:
        named = ", ".join(k for k in (*FIELDS, "tags") if k in filled) or "nothing new"
        return f"Added “{title}”. Filled in from the file and the catalogues: {named}."
    why = f" ({'; '.join(rejected)}, which did not match)" if rejected else ""
    return (f"Added “{title}”, but I could not identify it in the book catalogues{why}. "
            "Please check the title and author.")
