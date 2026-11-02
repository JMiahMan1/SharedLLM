"""Bibles from the family library: any Calibre book tagged "Bible".

Tagging a book "Bible" (or ``jarvis:bible``) in Calibre is how the family
says "this is a Bible we read". Only that exact tag counts: a library's own
subject headings ("Bible. O.T. -- Legends", "Bible--Hermeneutics") are books
*about* the Bible and are left alone.

For each tagged book this works out what the importer needs, without asking:

* the file: EPUB first (it carries the study notes), then PDF;
* the translation, from the title and tags ("NKJV, Apply the Word Study
  Bible" is the NKJV), against the corpus manifest, whose licence facts are
  the only ones trusted -- a translation the manifest does not know is listed
  with that reason and never installed on a guess;
* the edition: a study Bible becomes its own edition over the translation
  ("nkjv-apply-the-word-study-bible"), so two study Bibles over one
  translation sit side by side and a plain Bible is the text alone.

``sync`` installs every tagged Bible that has not been installed from that
file yet, through the ordinary importer (the same code an upload uses, which
refuses anything short of a complete Bible). A refused book is recorded and
not retried until its file changes, so a book that is not really a Bible is
tried once, not every few hours.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from sqlmodel import Session, select

from services.bible import corpus, importer
from services.bible.models import ImportRun

log = logging.getLogger(__name__)

TAGS = frozenset({"bible", "jarvis:bible"})
FORMATS = ("EPUB", "PDF")
#: How often the background sync looks for newly tagged Bibles.
SYNC_EVERY_S = 6 * 3600

# Words that name a translation in a title, longest first so "NKJV" and "New
# King James" win over "KJV" and "King James".
_EXTRA_NAMES = {
    "nkjv": ("new king james", "nkjv"),
    "kjv": ("king james", "authorized version", "kjv"),
    "esv": ("english standard", "esv"),
    "niv": ("new international version", "niv"),
    "nlt": ("new living translation", "nlt"),
    "asv": ("american standard version", "asv"),
    "web": ("world english bible",),
}


def is_bible_tag(tag: str) -> bool:
    return tag.strip().lower() in TAGS


def _aliases() -> list[tuple[str, str]]:
    """(phrase, code) pairs from the manifest, most specific first."""
    pairs: list[tuple[str, str]] = []
    for entry in corpus.load_manifest():
        code = str(entry["code"]).lower()
        pairs.append((str(entry.get("name") or "").lower(), code))
        pairs.append((code, code))
        pairs.extend((phrase, code) for phrase in _EXTRA_NAMES.get(code, ()))
    return sorted({p for p in pairs if p[0]}, key=lambda p: -len(p[0]))


def detect_code(title: str, tags: list[str]) -> str | None:
    """The manifest translation a book is, from its title and its tags."""
    haystack = " " + re.sub(r"[^a-z0-9]+", " ", " ".join([title, *tags]).lower()) + " "
    for phrase, code in _aliases():
        if f" {re.sub(r'[^a-z0-9]+', ' ', phrase).strip()} " in haystack:
            return code
    return None


def _main_title(title: str) -> str:
    """The title without its subtitle (after ": ") or a trailing ", eBook"."""
    main = re.split(r":\s", title, maxsplit=1)[0]
    return re.sub(r",?\s*e-?book\s*$", "", main, flags=re.IGNORECASE).strip(" ,;")


def edition_for(code: str, title: str, existing: list[tuple[str, str, str]] | None = None) -> tuple[str, str]:
    """(edition code, edition name) for a book of translation ``code``.

    The title without the translation's own words names the edition ("Apply
    the Word Study Bible"); a title that is only the translation ("The Holy
    Bible, ESV") is the plain text, edition == code. An edition already
    installed over the same translation whose name is in the title (the NLT
    "Courage For Life" notes) is reused, so a book tagged later does not
    install its notes twice.
    """
    title = _main_title(title)
    for ed_code, ed_name, version in existing or []:
        if version == code and ed_code != code and ed_name and ed_name.lower() in title.lower():
            return ed_code, ed_name
    words = title
    for phrase, alias_code in _aliases():
        if alias_code == code:
            words = re.sub(rf"\b{re.escape(phrase)}\b", " ", words, flags=re.IGNORECASE)
    words = re.sub(r"\b(holy|bible|ebook|e-book|edition|version|translation)\b", " ", words, flags=re.IGNORECASE)
    # A leading "The" says nothing ("The Holy Bible"); one inside the name stays.
    words = re.sub(r"^\W*the\b", " ", words.strip(" ,:;"), flags=re.IGNORECASE)
    slug = re.sub(r"[^a-z0-9]+", "-", words.lower()).strip("-")
    if not slug:
        return code, ""
    name = re.sub(r"\s+", " ", re.sub(r"\s*[,:;]\s*(e-?book)?\s*$", "", title, flags=re.IGNORECASE)).strip(" ,:;")
    return f"{code}-{slug}"[:60].rstrip("-"), name


def pick_file(book: dict[str, Any]) -> dict[str, str] | None:
    """The file to import: EPUB first, then PDF."""
    files = {str(f.get("format") or "").upper(): f for f in book.get("files") or []}
    for fmt in FORMATS:
        if fmt in files:
            return files[fmt]
    return None


def source_of(path: str) -> str:
    """How an install from this library file is recorded (``ImportPlan.source_label``)."""
    return f"nextcloud:{path}"


def installed_editions(session: Session) -> list[tuple[str, str, str]]:
    from services.bible.models import BibleEdition

    return [(e.code, e.name, e.version_code) for e in session.exec(select(BibleEdition)).all()]


def plan_for(
    book: dict[str, Any], *, code: str | None = None, existing: list[tuple[str, str, str]] | None = None,
) -> tuple[importer.ImportPlan | None, str]:
    """The import plan for one tagged book, or why there is none."""
    chosen = pick_file(book)
    if not chosen:
        return None, f"No EPUB or PDF to read (it has {', '.join(book.get('formats') or []) or 'no file'})."
    code = (code or detect_code(str(book.get("title") or ""), list(book.get("tags") or [])) or "").lower()
    known = {str(e["code"]).lower() for e in corpus.load_manifest()}
    if not code:
        return None, "Could not tell which translation this is from its title. Choose one to install it."
    if code not in known:
        return None, f"{code.upper()} is not in the Bible catalogue yet, so its licence is unknown."
    edition, edition_name = edition_for(code, str(book.get("title") or ""), existing)
    plan = importer.ImportPlan(
        code=code,
        kind="epub" if str(chosen.get("format")).upper() == "EPUB" else "pdf",
        library_path=str(chosen["path"]),
        edition=edition,
        edition_name=edition_name,
        import_notes=str(chosen.get("format")).upper() == "EPUB",
    )
    return plan, ""


def installed_elsewhere(plan: importer.ImportPlan, existing: list[tuple[str, str, str]]) -> str | None:
    """The name of the study Bible this book already is in the app, installed
    from another copy of the file (an upload), or None."""
    for ed_code, ed_name, version in existing:
        if version == plan.code and ed_code == plan.edition and ed_code != plan.code:
            return ed_name
    return None


def last_run(session: Session, path: str) -> ImportRun | None:
    return session.exec(
        select(ImportRun).where(ImportRun.source == source_of(path)).order_by(ImportRun.id.desc())  # type: ignore[union-attr]
    ).first()


def describe(session: Session, book: dict[str, Any]) -> dict[str, Any]:
    """One tagged book as Admin > Bible shows it."""
    plan, reason = plan_for(book, existing=installed_editions(session))
    chosen = pick_file(book)
    run = last_run(session, str(chosen["path"])) if chosen else None
    elsewhere = installed_elsewhere(plan, installed_editions(session)) if plan and run is None else None
    if run is not None and run.status == "succeeded":
        state = "installed"
    elif elsewhere:
        state = "installed"
        reason = f"Already in the Bible app as {elsewhere} (installed from another copy)."
    elif run is not None:
        state = "failed"
    elif plan is None:
        state = "needs attention"
    else:
        state = "ready"
    return {
        "id": book.get("id"),
        "title": book.get("title"),
        "authors": book.get("authors") or [],
        "file": chosen["path"] if chosen else None,
        "format": chosen.get("format") if chosen else None,
        "code": plan.code if plan else None,
        "edition": plan.edition if plan else None,
        "edition_name": plan.edition_name if plan else None,
        "state": state,
        "reason": reason or (run.message if run is not None and run.status != "succeeded" else ""),
        "installed_at": run.created_at.isoformat() if run is not None and run.status == "succeeded" else None,
    }


def due(session: Session, books: list[dict[str, Any]]) -> list[importer.ImportPlan]:
    """Plans for every tagged Bible not yet tried from its current file."""
    plans: list[importer.ImportPlan] = []
    existing = installed_editions(session)
    for book in books:
        plan, _ = plan_for(book, existing=existing)
        if plan is None or installed_elsewhere(plan, existing):
            continue
        if last_run(session, plan.library_path) is None:
            plans.append(plan)
    return plans
