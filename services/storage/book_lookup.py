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
    data = await _json(session, f"https://www.googleapis.com/books/v1/volumes?maxResults=3&q={quote(q)}")
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


async def open_library(session: aiohttp.ClientSession, title: str, author: str, isbn: str) -> dict[str, Any]:
    if isbn:
        url = f"https://openlibrary.org/search.json?limit=1&isbn={quote(isbn)}"
    elif title:
        url = f"https://openlibrary.org/search.json?limit=1&title={quote(title)}" + (f"&author={quote(author)}" if author else "")
    else:
        return _blank()
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
    out["language"] = (doc.get("language") or [""])[0]
    if doc.get("cover_i"):
        out["cover_url"] = f"https://covers.openlibrary.org/b/id/{doc['cover_i']}-L.jpg"
    if doc.get("key"):
        work = await _json(session, f"https://openlibrary.org{doc['key']}.json")
        desc = work.get("description")
        out["comments"] = str(desc.get("value") if isinstance(desc, dict) else desc or "").strip()
    return out


def suggest_tags(found: dict[str, Any], existing: list[str], limit: int = 8) -> list[dict[str, Any]]:
    """Tags for the book: library tags its subjects name, then the app's topics it calls for."""
    by_lower = {t.lower(): t for t in existing}
    picked: dict[str, dict[str, Any]] = {}
    for subject in found.get("subjects") or []:
        name = by_lower.get(str(subject).strip().lower())
        if name and name.lower() not in picked:
            picked[name.lower()] = {"name": name, "why": f"subject “{subject}”", "existing": True}
    text = " ".join([found.get("title") or "", " ".join(found.get("subjects") or []), (found.get("comments") or "")[:2000]]).lower()
    for topic, words in TOPICS.items():
        if topic in picked:
            continue
        # Whole words only: "breakfast" is not a fast.
        hit = next((w for w in words if re.search(rf"\b{re.escape(w)}\b", text)), None)
        if hit:
            name = by_lower.get(topic) or by_lower.get(f"jarvis:{topic}") or topic
            picked[name.lower()] = {"name": name, "why": f"mentions “{hit.strip()}”", "existing": name.lower() in by_lower}
    return list(picked.values())[:limit]


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


async def inspect(data: bytes, filename: str, existing_tags: list[str], *, online: bool = True) -> dict[str, Any]:
    """Every field Jarvis can find for a book, with where each one came from."""
    layers: list[tuple[str, dict[str, Any]]] = [("the file", file_details(data, filename))]
    base = layers[0][1]
    if online:
        title = base["title"] or _from_stem(filename)["title"]
        author = (base["authors"] or _from_stem(filename)["authors"] or [""])[0]
        isbn = base["isbn"]
        async with aiohttp.ClientSession(headers={"User-Agent": "JarvisOS-Library/1.0"}) as session:
            layers.append(("Google Books", await google_books(session, title, author, isbn)))
            layers.append(("Open Library", await open_library(session, title, author, isbn)))
    layers.append(("the file name", _from_stem(filename)))

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
    out["tags"] = suggest_tags(out, existing_tags)
    out["sources"] = sources
    return out
