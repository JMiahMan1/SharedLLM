# services/bible/corpus.py
"""Corpus import and read-path lookup.

Two halves:

* **import** -- ``import_corpus()`` takes a source file plus an expected sha256
  and writes ``BibleVersion``/``BibleBook``/``BibleVerse`` rows. It verifies
  the checksum and the chapter shape *before* writing, and refuses a file whose
  books or chapter counts disagree with ``books.py``. A silently half-loaded
  Bible is the worst possible failure for a reading app: the reader opens John
  3:16 and gets nothing, with no error anywhere.

* **lookup** -- ``fetch_passage`` / ``fetch_chapter`` / ``search``. These run on
  the read path, so they are plain SELECTs against SQLite. No network, no LLM,
  no external API: whatever the reader is looking at has to be answerable when
  the internet is not there.

Source format is the widely-mirrored
``[{"name":..., "chapters":[[verse, ...], ...]}, ...]`` JSON shape (the format
Crosswire-derived dumps use). A file whose chapter count for Genesis is 51 does
not get imported.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from pathlib import Path

from sqlalchemy import func
from sqlmodel import Session, delete, select

from services.bible.books import BOOKS, BOOK_BY_OSIS, resolve_book
from services.bible.models import (
    BibleBook,
    BibleEdition,
    BibleVerse,
    BibleVersion,
    StudyNote,
)
from services.bible.refs import ReferenceError, VerseSpan

log = logging.getLogger(__name__)

# Letters/numbers a source file may use for a book, mapped to our OSIS id.
_BOOK_LABEL = re.compile(r"[^a-z0-9]+")

MANIFEST_PATH = Path(__file__).with_name("corpus_manifest.json")
MANIFEST_KIND = "jarvis.bible.corpus"

#: How a translation's text may be obtained. ``public_domain`` entries carry a
#: source URL and checksum so ``--manifest`` can install them unattended;
#: ``licensed`` entries never do, because the text is not ours to redistribute.
LICENSE_CLASSES = ("public_domain", "licensed")

#: An edition code is ``nkjv``, ``nkjv-tmn``, ``nkjv-macarthur``: lowercase,
#: digits and dashes, starting with a letter. Constrained so a code can be put in
#: a URL and compared without quoting surprises.
EDITION_CODE = re.compile(r"^[a-z][a-z0-9]*(-[a-z0-9]+)*$")


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class CorpusError(ValueError):
    """A corpus source could not be trusted. The message says why."""


def load_manifest(path: Path | None = None) -> list[dict]:
    """Read the catalogue of known translations.

    The manifest is the single place that says which translations this install
    knows about, which are public domain (and therefore reproducible from a URL
    plus a checksum), and which are copyrighted (and therefore something an
    operator has to supply). It is deliberately *not* a fallback: a version that
    is not in here simply is not offered.
    """
    manifest_path = path or MANIFEST_PATH
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise CorpusError(f"corpus manifest not found: {manifest_path}") from exc
    except json.JSONDecodeError as exc:
        raise CorpusError(f"{manifest_path.name} is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("kind") != MANIFEST_KIND:
        raise CorpusError(
            f"{manifest_path.name} must be an object with kind {MANIFEST_KIND!r}"
        )
    entries = payload.get("versions")
    if not isinstance(entries, list) or not entries:
        raise CorpusError(f"{manifest_path.name} lists no versions")

    versions: list[dict] = []
    for raw in entries:
        if not isinstance(raw, dict):
            raise CorpusError(f"{manifest_path.name} has a non-object version entry")
        code = str(raw.get("code") or "").strip().lower()
        name = str(raw.get("name") or "").strip()
        license_class = str(raw.get("license_class") or "").strip()
        if not code:
            raise CorpusError(f"{manifest_path.name} has a version with no code")
        if not name:
            raise CorpusError(f"corpus manifest version {code!r} has no name")
        if license_class not in LICENSE_CLASSES:
            raise CorpusError(
                f"corpus manifest version {code!r} has license_class "
                f"{license_class!r}; expected one of {', '.join(LICENSE_CLASSES)}"
            )
        if license_class == "public_domain" and not str(raw.get("source_url") or "").strip():
            raise CorpusError(
                f"corpus manifest version {code!r} is public_domain but has no source_url"
            )
        versions.append(
            {
                "code": code,
                "name": name,
                "language": str(raw.get("language") or "en"),
                "license_class": license_class,
                "rights_holder": str(raw.get("rights_holder") or ""),
                "source_url": str(raw.get("source_url") or ""),
                "sha256": str(raw.get("sha256") or ""),
                "primary": bool(raw.get("primary")),
                "provider": str(raw.get("provider") or "").strip(),
            }
        )

    seen: set[str] = set()
    for version in versions:
        if version["code"] in seen:
            raise CorpusError(f"corpus manifest lists {version['code']!r} twice")
        seen.add(version["code"])
    primary = [v["code"] for v in versions if v["primary"]]
    if len(primary) > 1:
        raise CorpusError(
            "corpus manifest marks more than one translation primary: " + ", ".join(sorted(primary))
        )
    return sorted(versions, key=lambda v: v["code"])


def primary_code(path: Path | None = None) -> str:
    """The translation this install reads by default, or ``""`` if unmarked.

    The manifest marks at most one translation ``primary``. That is the one the
    reader opens when it has no saved preference, and the one the widget shows.
    It is configuration rather than a hardcoded constant so that an install
    which reads the ASV instead has one obvious place to say so.
    """
    for version in load_manifest(path):
        if version["primary"]:
            return str(version["code"])
    return ""


def load_editions(path: Path | None = None) -> list[dict]:
    """Read the catalogue of study Bibles known to this install.

    The same two-axis idea as the translations, one level down: an entry names
    a study Bible, the translation it explains, and who published it. Editions
    are optional in the manifest -- an install may ship text and no commentary
    at all -- but an entry whose translation is not itself catalogued is
    rejected, because a study Bible for a translation we cannot serve is a
    promise the reader cannot keep.
    """
    manifest_path = path or MANIFEST_PATH
    payload = _manifest_payload(manifest_path)
    known = {version["code"] for version in load_manifest(manifest_path)}
    entries = payload.get("editions") or []
    if not isinstance(entries, list):
        raise CorpusError(f"{manifest_path.name} has a non-list editions entry")

    editions: list[dict] = []
    seen: set[str] = set()
    for raw in entries:
        if not isinstance(raw, dict):
            raise CorpusError(f"{manifest_path.name} has a non-object edition entry")
        code = str(raw.get("code") or "").strip().lower()
        version = str(raw.get("version") or "").strip().lower()
        name = str(raw.get("name") or "").strip()
        license_class = str(raw.get("license_class") or "").strip() or "licensed"
        if not EDITION_CODE.match(code):
            raise CorpusError(
                f"corpus manifest edition {code!r} is not a usable code; expected "
                "lowercase words separated by dashes, e.g. nkjv-macarthur"
            )
        if not name:
            raise CorpusError(f"corpus manifest edition {code!r} has no name")
        if version not in known:
            raise CorpusError(
                f"corpus manifest edition {code!r} explains {version or 'nothing'!r}, "
                f"which is not a catalogued translation ({', '.join(sorted(known))})"
            )
        if license_class not in LICENSE_CLASSES:
            raise CorpusError(
                f"corpus manifest edition {code!r} has license_class "
                f"{license_class!r}; expected one of {', '.join(LICENSE_CLASSES)}"
            )
        if code in seen:
            raise CorpusError(f"corpus manifest lists edition {code!r} twice")
        seen.add(code)
        editions.append(
            {
                "code": code,
                "version": version,
                "name": name,
                "publisher": str(raw.get("publisher") or ""),
                "language": str(raw.get("language") or "en"),
                "license_class": license_class,
                "rights_holder": str(raw.get("rights_holder") or ""),
                "source_url": str(raw.get("source_url") or ""),
                "sha256": str(raw.get("sha256") or ""),
            }
        )
    return sorted(editions, key=lambda e: e["code"])


def _manifest_payload(manifest_path: Path) -> dict:
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise CorpusError(f"corpus manifest not found: {manifest_path}") from exc
    except json.JSONDecodeError as exc:
        raise CorpusError(f"{manifest_path.name} is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("kind") != MANIFEST_KIND:
        raise CorpusError(
            f"{manifest_path.name} must be an object with kind {MANIFEST_KIND!r}"
        )
    return payload


def _acquisition_hint(version: dict) -> str:
    """How to obtain a translation we do not ship the text for."""
    provider = str(version.get("provider") or "")
    if version["license_class"] == "public_domain":
        if version["source_url"]:
            return (
                "Install it with: python3 -m services.bible.import_corpus --manifest "
                f"--only {version['code']}"
            )
        return "This public-domain translation has no source_url in the corpus manifest."
    holder = f" ({version['rights_holder']})" if version["rights_holder"] else ""
    if provider:
        return (
            f"{version['name']}{holder} is copyrighted, so its text is not bundled. "
            f"Install it from the {provider} provider on the Admin > Bible page."
        )
    return (
        f"{version['name']}{holder} is copyrighted, so its text is not bundled. "
        "Import the copy you are licensed to use with: "
        f"python3 -m services.bible.import_corpus --only {version['code']} "
        f"--source <path-to-{version['code']}.json>"
    )


def _load_source(path: Path) -> list[dict]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise CorpusError(f"{path.name} is not valid JSON: {exc}") from exc
    if not isinstance(payload, list) or not payload:
        raise CorpusError(f"{path.name} must be a non-empty JSON array of books")
    return payload


def _match_osis(label: str, index: int) -> str | None:
    """Map a source-file book label onto our canonical OSIS id.

    Source dumps disagree about book naming (abbreviations, Portuguese names,
    "First Samuel"), so we try the label as a book alias and then fall back to
    canonical position, which is stable across every dump of the same 66 books.
    """
    osis = resolve_book(label)
    if osis:
        return osis
    squashed = _BOOK_LABEL.sub("", label.lower())
    for candidate in BOOKS:
        if _BOOK_LABEL.sub("", candidate["name"].lower()) == squashed:
            return candidate["osis"]
    positional = BOOKS[index] if 0 <= index < len(BOOKS) else None
    if positional:
        log.warning(
            "[Bible] Could not match source book %r; assuming canonical position %d (%s)",
            label, index + 1, positional["osis"],
        )
        return positional["osis"]
    return None


def parse_source(
    payload: list[dict], *, allow_gaps: bool = False
) -> tuple[str, list[tuple[str, list[list[str]]]]]:
    """Normalise a source file into ``(name, [(osis, [[verse, ...], ...])])``.

    Returns the detected translation name and the books in canonical order with
    chapter counts validated against ``books.py``.

    A blank verse is a refusal by default. That is what catches a truncated or
    mis-paginated PDF, where a blank slot means text was lost on the way in. It
    is the wrong verdict for a translation served by an online provider, which is
    authoritative about its own versification and simply omits verses that
    textual criticism treats as later additions -- NIV2011 has no Matthew 17:21
    at all. ``allow_gaps`` is therefore set only on the provider path, and the
    blank slot is kept as ``""`` so every verse number still lines up with the
    printed one. ``omitted_verses`` then names each one, so a gap stays visible
    instead of quietly looking like a short chapter.
    """
    books: dict[str, list[list[str]]] = {}
    names: dict[str, str] = {}
    for index, entry in enumerate(payload):
        label = str(entry.get("name") or entry.get("abbrev") or "")
        osis = _match_osis(label, index)
        if osis is None:
            raise CorpusError(f"source book {label!r} at position {index + 1} is not one of the 66")
        chapters = entry.get("chapters")
        if not isinstance(chapters, list) or not chapters:
            raise CorpusError(f"source book {label!r} has no chapters")
        canonical = next(b for b in BOOKS if b["osis"] == osis)
        if len(chapters) != canonical["chapters"]:
            raise CorpusError(
                f"{canonical['name']} in the source has {len(chapters)} chapters, "
                f"but the book has {canonical['chapters']}"
            )
        if osis in books:
            raise CorpusError(f"source contains {canonical['name']} twice")
        normalised: list[list[str]] = []
        for chapter_index, chapter in enumerate(chapters, start=1):
            if not isinstance(chapter, list):
                raise CorpusError(
                    f"{canonical['name']} chapter {chapter_index} in the source is not a list"
                )
            if not chapter and not allow_gaps:
                raise CorpusError(
                    f"{canonical['name']} chapter {chapter_index} in the source is empty"
                )
            cleaned = []
            for verse_index, verse in enumerate(chapter, start=1):
                text = str(verse).strip()
                if not text and not allow_gaps:
                    raise CorpusError(
                        f"{canonical['name']} {chapter_index}:{verse_index} is blank in the source"
                    )
                cleaned.append(text)
            normalised.append(cleaned)
        books[osis] = normalised
        names[osis] = label

    missing = [b["osis"] for b in BOOKS if b["osis"] not in books]
    if missing:
        readable = ", ".join(
            next(b["name"] for b in BOOKS if b["osis"] == osis) for osis in missing[:6]
        )
        raise CorpusError(
            f"source is missing {len(missing)} of the 66 books ({readable}"
            f"{'...' if len(missing) > 6 else ''}); refusing to import a partial Bible"
        )

    ordered = [b["osis"] for b in BOOKS]
    return "", [(osis, books[osis]) for osis in ordered]


def omitted_verses(normalised: list[tuple[str, list[list[str]]]]) -> list[str]:
    """Name every blank verse slot in a parsed source, as ``"Matthew 17:21"``.

    Only meaningful with ``parse_source(..., allow_gaps=True)``: a file import
    never gets this far, because a blank slot there is a refusal.
    """
    omitted: list[str] = []
    for osis, chapters in normalised:
        canonical = next(b for b in BOOKS if b["osis"] == osis)
        for chapter_number, chapter in enumerate(chapters, start=1):
            for verse_number, text in enumerate(chapter, start=1):
                if not text:
                    omitted.append(f"{canonical['name']} {chapter_number}:{verse_number}")
    return omitted


def import_corpus(
    session: Session,
    *,
    code: str,
    name: str,
    source_path: Path,
    expected_sha256: str | None = None,
    language: str = "en",
    license_class: str = "public_domain",
    detected_name: str = "",
    edition: str | None = None,
    edition_name: str = "",
    publisher: str = "",
    rights_holder: str = "",
    allow_gaps: bool = False,
) -> dict:
    """Import one translation. Replaces any existing rows for ``code``.

    ``edition`` names the study Bible this file came from, when the file is one.
    The verse text is still installed once per translation, so importing a
    second study Bible of the same translation replaces the text with identical
    text and leaves every other edition's notes untouched -- which is why the
    notes are keyed by edition and not deleted here.

    ``allow_gaps`` is for provider translations that omit verses on purpose; see
    ``parse_source``. The returned summary then carries an ``omitted`` list naming
    each absent verse, because a translation with deliberate gaps must still say
    which ones they are.
    """
    if not source_path.is_file():
        raise CorpusError(f"corpus source file not found: {source_path}")

    actual = file_sha256(source_path)
    if expected_sha256 and actual.lower() != expected_sha256.strip().lower():
        raise CorpusError(
            f"checksum mismatch for {source_path.name}: expected {expected_sha256}, got {actual}"
        )

    _, normalised = parse_source(_load_source(source_path), allow_gaps=allow_gaps)
    omitted = omitted_verses(normalised)
    verse_count = sum(
        1 for _osis, chapters in normalised for chapter in chapters for text in chapter if text
    )

    session.exec(
        delete(BibleVerse).where(BibleVerse.version_code == code)  # type: ignore[arg-type]
    )
    session.exec(
        delete(BibleBook).where(BibleBook.version_code == code)  # type: ignore[arg-type]
    )
    session.exec(delete(BibleVersion).where(BibleVersion.code == code))  # type: ignore[arg-type]

    session.add(
        BibleVersion(
            code=code,
            name=detected_name or name,
            language=language,
            license_class=license_class,
            verse_count=verse_count,
        )
    )
    for osis, chapters in normalised:
        canonical = next(b for b in BOOKS if b["osis"] == osis)
        session.add(
            BibleBook(
                version_code=code,
                osis=osis,
                name=canonical["name"],
                order=canonical["order"],
                chapters=len(chapters),
            )
        )
        for chapter_number, chapter in enumerate(chapters, start=1):
            for verse_number, text in enumerate(chapter, start=1):
                if not text:
                    continue
                session.add(
                    BibleVerse(
                        version_code=code,
                        osis=osis,
                        chapter=chapter_number,
                        verse=verse_number,
                        text=text,
                    )
                )
    session.commit()

    edition_code = register_edition(
        session,
        code=edition or code,
        version=code,
        name=edition_name or f"{detected_name or name} (text only)",
        publisher=publisher,
        language=language,
        license_class=license_class,
        rights_holder=rights_holder,
    )["code"]
    return {
        "code": code,
        "name": detected_name or name,
        "sha256": actual,
        "books": len(normalised),
        "verses": verse_count,
        "edition": edition_code,
        "omitted": omitted,
    }


def list_versions(session: Session) -> list[dict]:
    rows = session.exec(select(BibleVersion).order_by(BibleVersion.code)).all()
    return [
        {
            "code": row.code,
            "name": row.name,
            "language": row.language,
            "license_class": row.license_class,
            "verse_count": row.verse_count,
        }
        for row in rows
    ]


def default_version_code(session: Session, *, manifest_path: Path | None = None) -> str:
    """Which installed translation a reader gets when they have no preference.

    The manifest's ``primary`` translation wins when its text is installed.
    Otherwise the installed translation with the most verses wins, which in
    practice means "the most complete Bible we can actually serve". Returns
    ``""`` when nothing is installed, so the caller can raise its own message
    rather than inventing a fallback code.
    """
    available = list_versions(session)
    if not available:
        return ""
    preferred = primary_code(manifest_path)
    if preferred:
        for row in available:
            if row["code"] == preferred:
                return str(row["code"])
    richest = max(available, key=lambda row: (int(row["verse_count"]), str(row["code"])))
    return str(richest["code"])


def catalogue(session: Session, *, manifest_path: Path | None = None) -> list[dict]:
    """Every translation we know about, and whether its text is installed.

    The version picker shows this: an uninstalled translation is listed with the
    reason it is absent rather than simply missing, so nobody wonders why the
    ESV they expected is not in the list.
    """
    imported = {row["code"]: row for row in list_versions(session)}
    editions_by_version: dict[str, int] = {}
    for row in list_editions(session):
        editions_by_version[row["version"]] = editions_by_version.get(row["version"], 0) + 1
    entries: list[dict] = []
    for version in load_manifest(manifest_path):
        row = imported.pop(version["code"], None)
        entry = {
            "code": version["code"],
            "name": version["name"],
            "language": version["language"],
            "license_class": version["license_class"],
            "rights_holder": version["rights_holder"],
            "installed": row is not None,
            "verse_count": row["verse_count"] if row else 0,
            "editions": editions_by_version.get(version["code"], 0),
            "primary": version["primary"],
            "provider": version["provider"],
            "note": "",
        }
        if row is None:
            entry["note"] = _acquisition_hint(version)
        entries.append(entry)

    for code, row in sorted(imported.items()):
        entry = {
            "code": code,
            "name": row["name"],
            "language": row["language"],
            "license_class": row["license_class"],
            "rights_holder": "",
            "installed": True,
            "verse_count": row["verse_count"],
            "editions": editions_by_version.get(code, 0),
            "primary": code == primary_code(manifest_path),
            "provider": "",
            "note": "",
        }
        if row["license_class"] == "licensed":
            entry["note"] = (
                f"{row['name']} is installed from a licensed source. Do not "
                "redistribute it."
            )
        entries.append(entry)
    return entries


def require_version(session: Session, code: str, *, manifest_path: Path | None = None) -> str:
    """Resolve the requested version or raise, naming what IS available.

    A reading app that quietly serves the wrong translation is worse than one
    that says it has nothing, so this never falls back to a default.
    """
    row = session.exec(select(BibleVersion).where(BibleVersion.code == code)).first()
    if row is not None:
        return row.code
    known = {entry["code"]: entry for entry in catalogue(session, manifest_path=manifest_path)}
    if code in known:
        raise ReferenceError(
            f"Bible version {code!r} is not installed. {known[code]['note']}"
        )
    available = ", ".join(
        entry["code"] for entry in known.values() if entry["installed"]
    ) or "none installed"
    raise ReferenceError(
        f"Bible version {code!r} is not in the corpus manifest and is not "
        f"installed. Installed versions: {available}. Add it to "
        "services/bible/corpus_manifest.json before importing it."
    )


# ── study editions ───────────────────────────────────────────────────────────
#
# Everything below is the second axis: which study Bible explains the text. The
# functions here never touch ``BibleVerse``, because the text is shared.


def register_edition(
    session: Session,
    *,
    code: str,
    version: str,
    name: str,
    publisher: str = "",
    language: str = "en",
    license_class: str = "licensed",
    rights_holder: str = "",
    note_count: int = 0,
    note_kinds: str = "",
) -> dict:
    """Create or update one study edition. Returns the stored row as a dict."""
    clean = str(code or "").strip().lower()
    if not EDITION_CODE.match(clean):
        raise CorpusError(
            f"study edition code {code!r} is not usable; expected lowercase words "
            "separated by dashes, e.g. nkjv-macarthur"
        )
    if not str(name or "").strip():
        raise CorpusError(f"study edition {clean!r} needs a name")
    require_version(session, version)
    row = session.exec(
        select(BibleEdition).where(BibleEdition.code == clean)
    ).first()
    if row is None:
        row = BibleEdition(code=clean, version_code=version)
        session.add(row)
    row.version_code = version
    row.name = str(name).strip()
    row.publisher = str(publisher or "").strip()
    row.language = str(language or "en").strip() or "en"
    row.license_class = license_class
    row.rights_holder = str(rights_holder or "").strip()
    row.note_count = int(note_count)
    row.note_kinds = str(note_kinds or "").strip()
    session.commit()
    session.refresh(row)
    return _edition_dict(row)


def ensure_default_edition(session: Session, version: str) -> str:
    """Guarantee a translation has at least one edition and return its code.

    A translation with no study material still needs an edition, because "this
    translation has no notes" is only answerable when there is something to have
    notes *against*. The row is named after the translation, which is honest: it
    is the text with nobody's commentary on it.
    """
    existing = session.exec(
        select(BibleEdition.code)
        .where(BibleEdition.version_code == version)
        .order_by(BibleEdition.code)
        .limit(1)
    ).first()
    if existing:
        return str(existing[0])
    version_row = session.exec(
        select(BibleVersion).where(BibleVersion.code == version)
    ).first()
    name = version_row.name if version_row else version
    license_class = version_row.license_class if version_row else "public_domain"
    register_edition(
        session,
        code=version,
        version=version,
        name=f"{name} (text only)",
        license_class=license_class,
    )
    return version


def list_editions(session: Session, version: str | None = None) -> list[dict]:
    """Installed editions, richest first so the default needs no extra flag."""
    statement = select(BibleEdition)
    if version:
        statement = statement.where(BibleEdition.version_code == version)
    rows = session.exec(
        statement.order_by(
            BibleEdition.note_count.desc(), BibleEdition.code
        )
    ).all()
    return [_edition_dict(row) for row in rows]


def _edition_dict(row: BibleEdition) -> dict:
    kinds = sorted({part for part in row.note_kinds.split(",") if part})
    return {
        "code": row.code,
        "version": row.version_code,
        "name": row.name,
        "publisher": row.publisher,
        "language": row.language,
        "license_class": row.license_class,
        "rights_holder": row.rights_holder,
        "note_count": row.note_count,
        "note_kinds": kinds,
        "imported_at": row.imported_at.isoformat() if row.imported_at else "",
    }


def default_edition(session: Session, version: str) -> str:
    """Which study Bible to show when the reader has not chosen one.

    The one with the most notes, because that is the study Bible somebody
    actually installed rather than the implicit text-only row. Ties break on
    code so the answer does not depend on row order.
    """
    editions = list_editions(session, version)
    if not editions:
        return ensure_default_edition(session, version)
    return str(editions[0]["code"])


def require_edition(session: Session, version: str, edition: str | None = None) -> str:
    """Resolve a requested study edition, or fail naming the ones installed."""
    require_version(session, version)
    if edition:
        row = session.exec(
            select(BibleEdition).where(BibleEdition.code == str(edition).strip().lower())
        ).first()
        if row is None:
            available = ", ".join(entry["code"] for entry in list_editions(session))
            raise ReferenceError(
                f"Study edition {edition!r} is not installed. Installed editions for "
                f"{version}: {available or 'none'}."
            )
        if row.version_code != version:
            raise ReferenceError(
                f"Study edition {row.code!r} explains {row.version_code}, not {version}."
            )
        return row.code
    return default_edition(session, version)


def edition_catalogue(session: Session, version: str | None = None) -> list[dict]:
    """Every study Bible we know about, and whether its notes are installed.

    Same reasoning as :func:`catalogue` for translations: a study Bible that is
    catalogued but not installed is listed with the command that installs it, so
    its absence reads as a task rather than a bug.
    """
    installed = {entry["code"]: entry for entry in list_editions(session, version)}
    entries: list[dict] = []
    for edition in load_editions():
        if version and edition["version"] != version:
            continue
        row = installed.pop(edition["code"], None)
        entry = dict(edition)
        entry["version"] = edition["version"]
        entry["installed"] = row is not None
        entry["note_count"] = row["note_count"] if row else 0
        entry["note_kinds"] = row["note_kinds"] if row else []
        entry["note"] = "" if row else _edition_acquisition_hint(edition)
        entries.append(entry)
    for code, row in sorted(installed.items()):
        if version and row["version"] != version:
            continue
        entry = dict(row)
        entry["installed"] = True
        entry["note"] = (
            f"{row['name']} is installed from a licensed source. Do not redistribute it."
            if row["license_class"] == "licensed"
            else ""
        )
        entries.append(entry)
    entries.sort(key=lambda e: (-e["note_count"], e["code"]))
    return entries


def _edition_acquisition_hint(edition: dict) -> str:
    holder = f" ({edition['rights_holder']})" if edition["rights_holder"] else ""
    if not edition["source_url"]:
        return (
            f"{edition['name']}{holder} is copyrighted, so its notes are not bundled. "
            f"Import the copy you are licensed to use with: "
            f"python3 -m services.bible.import_epub --code {edition['version']} "
            f"--edition {edition['code']} --source <path> --import --import-notes"
        )
    return (
        f"Install it with: python3 -m services.bible.import_corpus --manifest "
        f"--only {edition['version']}"
    )


def notes_summary(session: Session, code: str) -> dict:
    """Count and kinds actually stored for one study edition, read from the rows.

    A column select through ``Session.exec`` yields bare values, not one-tuples,
    so each row is the kind itself.
    """
    kinds: dict[str, int] = {}
    for kind in session.exec(
        select(StudyNote.kind).where(StudyNote.edition_code == code)
    ):
        key = str(kind)
        kinds[key] = kinds.get(key, 0) + 1
    return {"count": sum(kinds.values()), "kinds": sorted(kinds)}


def record_edition_notes(session: Session, code: str, count: int | None = None, kinds=None) -> None:
    """Update the denormalised note count/kinds after an import.

    Pass neither and the real figures are recounted from the stored rows, which
    is what a code rename needs: the notes moved, the summary did not.
    """
    row = session.exec(
        select(BibleEdition).where(BibleEdition.code == code)
    ).first()
    if row is None:
        return
    if count is None or kinds is None:
        live = notes_summary(session, code)
        count = live["count"]
        kinds = live["kinds"]
    row.note_count = int(count)
    row.note_kinds = ",".join(sorted({str(kind) for kind in kinds if kind}))
    session.commit()


def refresh_edition_notes(session: Session) -> list[str]:
    """Recount every installed edition's cached notes and return what changed.

    The count and kinds on the row are a convenience so the picker does not have
    to group over tens of thousands of notes. Anything that moves notes without
    rewriting those two fields leaves the catalogue quietly wrong, and a wrong
    count reads as "this study Bible has no notes". So the totals are treated as
    derived: recounting is always safe, and only genuine differences are
    returned so the caller can say what it had to put right.
    """
    corrected: list[str] = []
    for row in session.exec(select(BibleEdition)).all():
        live = notes_summary(session, row.code)
        kinds = ",".join(live["kinds"])
        if int(row.note_count or 0) == live["count"] and (row.note_kinds or "") == kinds:
            continue
        row.note_count = live["count"]
        row.note_kinds = kinds
        corrected.append(row.code)
    if corrected:
        session.commit()
    return corrected


def remove_edition(session: Session, code: str) -> dict:
    """Drop a study Bible's notes and row, leaving the translation readable.

    The counterpart to having several editions: taking one away must never cost
    anybody their Bible. Text is untouched by design, so this cannot destroy
    Scripture even if the code passed in is the translation's own code.
    """
    from services.bible.models import StudyNote

    clean = str(code or "").strip().lower()
    row = session.exec(select(BibleEdition).where(BibleEdition.code == clean)).first()
    if row is None:
        raise ReferenceError(f"Study edition {clean!r} is not installed.")
    if clean == row.version_code:
        raise ReferenceError(
            f"{clean!r} is the text-only edition for {row.version_code}; a translation "
            "is not a study Bible and cannot be removed."
        )
    removed = session.exec(
        delete(StudyNote).where(StudyNote.edition_code == clean)
    ).rowcount
    session.exec(delete(BibleEdition).where(BibleEdition.code == clean))
    session.commit()
    return {"edition": clean, "notes_removed": int(removed or 0)}


def rename_edition(session: Session, old: str, new: str, *, name: str = "") -> dict:
    """Move a study edition -- and every note it owns -- to a new code.

    The migration that adopted pre-edition notes names them after the
    translation (``nkjv``), which is honest but not what the manifest calls the
    study Bible (``nkjv-tmn``). Rather than guess which is which at boot, this
    moves them on request and reports the count, so the operator can see exactly
    what moved before deciding to re-import anything.
    """
    source = str(old or "").strip().lower()
    target = str(new or "").strip().lower()
    if not EDITION_CODE.match(target):
        raise CorpusError(
            f"study edition code {new!r} is not usable; expected lowercase words "
            "separated by dashes, e.g. nkjv-macarthur"
        )
    if source == target:
        raise CorpusError(f"{source!r} and {target!r} are the same study edition.")
    row = session.exec(
        select(BibleEdition).where(BibleEdition.code == source)
    ).first()
    if row is None:
        raise CorpusError(f"Study edition {source!r} is not installed.")
    clash = session.exec(
        select(BibleEdition).where(BibleEdition.code == target)
    ).first()
    if clash is not None:
        raise CorpusError(
            f"Study edition {target!r} already exists. Remove it first, or import "
            "the notes into it with import_epub.py --edition."
        )
    from services.bible.models import StudyNote

    row.code = target
    if name:
        row.name = name
    session.exec(
        StudyNote.__table__.update()  # type: ignore[attr-defined]
        .where(StudyNote.edition_code == source)
        .values(edition_code=target)
    )
    session.commit()
    session.refresh(row)
    # The codes moved, so the cached summary is stale; recount from the rows or
    # the picker would offer a count the notes do not match.
    moved = notes_summary(session, target)["count"]
    record_edition_notes(session, target)
    session.refresh(row)
    entry = _edition_dict(row)
    entry["notes_moved"] = moved
    return entry


def list_books(session: Session, version: str) -> list[dict]:
    rows = session.exec(
        select(BibleBook).where(BibleBook.version_code == version).order_by(BibleBook.order)
    ).all()
    return [
        {"osis": row.osis, "name": row.name, "chapters": row.chapters, "order": row.order}
        for row in rows
    ]


def _verse_payload(row: BibleVerse) -> dict:
    name = BOOK_BY_OSIS.get(row.osis, {}).get("name", row.osis)
    return {
        "version": row.version_code,
        "osis": row.osis,
        "book_name": name,
        "chapter": row.chapter,
        "verse": row.verse,
        "reference": f"{name} {row.chapter}:{row.verse}",
        "text": row.text,
    }


def fetch_chapter(session: Session, version: str, osis: str, chapter: int) -> list[dict]:
    """Every verse of one chapter.

    ``osis`` is resolved through the alias table first: callers hold whatever
    the reader typed or tapped, and a lowercase "gen" must reach Genesis rather
    than come back as "gen is not in version kjv".
    """
    require_version(session, version)
    osis = resolve_book(osis) or ""
    if not osis:
        raise ReferenceError(f"{osis!r} is not one of the 66 canonical books")
    if chapter < 1:
        raise ReferenceError(f"chapter numbers start at 1, not {chapter}")
    rows = session.exec(
        select(BibleVerse)
        .where(
            BibleVerse.version_code == version,  # type: ignore[arg-type]
            BibleVerse.osis == osis,  # type: ignore[arg-type]
            BibleVerse.chapter == chapter,  # type: ignore[arg-type]
        )
        .order_by(BibleVerse.verse)
    ).all()
    if not rows:
        book_row = session.exec(
            select(BibleBook).where(
                BibleBook.version_code == version, BibleBook.osis == osis  # type: ignore[arg-type]
            )
        ).first()
        if book_row is None:
            raise ReferenceError(f"{osis} is not in version {version}")
        raise ReferenceError(
            f"{book_row.name} {chapter} is not in version {version} "
            f"(that version has {book_row.chapters} chapters)"
        )
    return [_verse_payload(row) for row in rows]


def fetch_span(session: Session, version: str, span: VerseSpan) -> list[dict]:
    """Every verse a span covers, in canonical order."""
    end_chapter = span.resolved_chapter_end
    out: list[dict] = []
    for chapter in range(span.chapter_start, end_chapter + 1):
        for payload in fetch_chapter(session, version, span.book, chapter):
            if payload["verse"] < span.verse_start:
                continue
            if chapter == end_chapter and span.verse_end is not None and payload["verse"] > span.verse_end:
                continue
            out.append(payload)
    if not out:
        raise ReferenceError(
            f"{span.display()} has no verses in version {version}"
        )
    return out


def fetch_passage(session: Session, version: str, spans: list[VerseSpan]) -> list[dict]:
    verses: list[dict] = []
    for span in spans:
        verses.extend(fetch_span(session, version, span))
    return verses


def search_verses(
    session: Session,
    version: str,
    terms: str,
    *,
    osis: str | None = None,
    limit: int = 100,
) -> list[dict]:
    """Substring search across the imported corpus.

    Deliberately a LIKE scan over SQLite FTS-free text: the corpus is tens of
    thousands of rows, it is a single local file, and a search index is a later
    problem than an honest answer. ``sanitize_like`` stops a reader typing
    ``%`` from matching the whole Bible.
    """
    require_version(session, version)
    cleaned = terms.strip()
    if not cleaned:
        raise ReferenceError("search text is empty")
    pattern = f"%{sanitize_like(cleaned)}%"
    statement = (
        select(BibleVerse)
        .where(
            BibleVerse.version_code == version,  # type: ignore[arg-type]
            BibleVerse.text.like(pattern, escape="\\"),  # type: ignore[attr-defined]
        )
        .order_by(BibleVerse.osis, BibleVerse.chapter, BibleVerse.verse)
        .limit(limit)
    )
    if osis:
        resolved_book = resolve_book(osis)
        if not resolved_book:
            raise ReferenceError(f"{osis!r} is not one of the 66 canonical books")
        statement = statement.where(BibleVerse.osis == resolved_book)  # type: ignore[arg-type]
    return [_verse_payload(row) for row in session.exec(statement).all()]


def sanitize_like(term: str) -> str:
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def count_rows(session: Session, model: Any) -> int:
    """Row count for one table.

    ``Session.exec`` returns a bare scalar for an aggregate select and a tuple
    for a column select, so go through one helper rather than sprinkling
    ``[0]`` around and guessing.
    """
    return int(session.exec(select(func.count()).select_from(model)).one() or 0)


def corpus_summary(session: Session) -> dict:
    return {
        "versions": list_versions(session),
        "verse_rows": count_rows(session, BibleVerse),
        "canonical_books": len(BOOKS),
    }