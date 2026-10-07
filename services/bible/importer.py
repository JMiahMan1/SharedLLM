"""One import path for every way a translation or study Bible can arrive.

Before this existed the only way in was three command-line tools, which is fine
for the person who set the server up and useless for the person who just bought
a study Bible. The logic moved here unchanged; the commands are now thin callers,
so what the admin page reports is exactly what ``import_pdf`` would have printed.

Three sources, one shape of answer:

``json``
    Corpus-shaped JSON, already extracted. Verified against the manifest's
    checksum when one is given, so a truncated download cannot install a short
    Bible.
``pdf`` / ``epub``
    A print Bible or an e-book. Extracted once to corpus-shaped JSON, staged
    beside the source so the extraction can be inspected, and only then
    installed. Study notes ride along from an e-book when asked for, out of the
    same extraction rather than a second pass over a 16 MB file.
``provider``
    Fetched from a declared online service (see :mod:`services.bible.providers`).
    The text is written to the import directory, cached, and thereafter
    indistinguishable from one installed from a file.

Every run is recorded in ``ImportRun`` -- including the ones that fail, because
"it said it worked and now Psalm 23 is missing" needs an answer the reader can
see.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from services.bible import corpus, epub_import, pdf_import, provider_cache, study
from services.bible.corpus import CorpusError
from services.bible.models import ImportRun
from services.bible.providers import ProviderError, ProviderUnavailable
from services.bible.refs import ReferenceError

KINDS = ("json", "pdf", "epub")
SUFFIX_KINDS = {".json": "json", ".pdf": "pdf", ".epub": "epub"}
MAX_UPLOAD_BYTES = 256 * 1024 * 1024

class ImportRefusal(ValueError):
    """The request cannot be attempted at all (a 400)."""


EXPECTED_FAILURES = (
    CorpusError,
    ReferenceError,
    ImportRefusal,
    ProviderError,
    ProviderUnavailable,
    FileNotFoundError,
)


@dataclass(frozen=True)
class ImportPlan:
    """What the operator asked to install."""

    code: str
    kind: str = "json"
    name: str = ""
    source_path: str = ""
    sha256: str = ""
    edition: str = ""
    edition_name: str = ""
    publisher: str = ""
    rights_holder: str = ""
    import_notes: bool = False
    provider: str = ""
    provider_id: str = ""
    dry_run: bool = False
    budget: int | None = None
    library_path: str = ""

    def source_label(self) -> str:
        if self.library_path:
            return f"nextcloud:{self.library_path}"
        if self.provider:
            return f"{self.provider}:{self.provider_id}" if self.provider_id else self.provider
        return self.source_path

    def _replace_source(self, path: Path) -> "ImportPlan":
        """The same request, pointed at a local file that is already here.

        ``library_path`` is cleared because the file has been fetched: leaving it
        set would send the next pass back to the shelf for a download it already
        has.
        """
        return replace(
            self,
            source_path=str(path),
            library_path="",
            provider="",
            provider_id="",
        )


@dataclass
class ImportReport:
    """The outcome, in a form both an HTTP response and a log line can carry."""

    status: str
    kind: str
    code: str
    source: str
    message: str
    name: str = ""
    verse_count: int = 0
    book_count: int = 0
    note_count: int = 0
    staged_path: str = ""
    log: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """A dry run did what it was asked; only a refusal is not okay."""
        return self.status in {"succeeded", "dry_run"}

    def as_dict(self) -> dict:
        return {
            "status": self.status,
            "kind": self.kind,
            "code": self.code,
            "name": self.name,
            "source": self.source,
            "message": self.message,
            "verse_count": self.verse_count,
            "book_count": self.book_count,
            "note_count": self.note_count,
            "staged_path": self.staged_path,
            "log": list(self.log),
        }


@dataclass
class Materialised:
    """Corpus-shaped JSON plus whatever else the source yielded.

    ``notes_only`` marks a commentary: passage notes and no verse text, so
    there is no translation for ``import_corpus`` to install and the notes are
    the whole deliverable.
    """

    path: Path
    staged: Path | None = None
    notes: list[dict] = field(default_factory=list)
    report_lines: list[str] = field(default_factory=list)
    allow_gaps: bool = False
    dry_run: bool = False
    sample_verses: int = 0
    notes_only: bool = False


def detect_kind(path: Path) -> str:
    """Which importer a file needs, decided by its suffix."""
    kind = SUFFIX_KINDS.get(path.suffix.lower())
    if not kind:
        supported = ", ".join(sorted(SUFFIX_KINDS))
        raise ImportRefusal(
            f"{path.name} is not a Bible file this service can read. "
            f"Supported extensions: {supported}."
        )
    return str(kind)


def _manifest_entry(code: str) -> dict:
    for entry in corpus.load_manifest():
        if entry["code"] == code:
            return entry
    known = ", ".join(e["code"] for e in corpus.load_manifest())
    raise ImportRefusal(
        f"{code!r} is not in the corpus manifest, so its licence class is unknown and it "
        f"will not be installed. Add it first; this install knows: {known}."
    )


def _resolved(plan: ImportPlan) -> dict:
    """Plan + manifest -> the names, licence facts and endpoints the importer needs.

    The endpoint values are read here rather than passed in so that an import
    which never touches the library does not require the storage service to be
    configured, and so there is exactly one place that knows what a library
    import needs.
    """
    entry = _manifest_entry(plan.code)
    name = plan.name or str(entry["name"])
    return {
        "entry": entry,
        "name": name,
        "license_class": str(entry["license_class"]),
        "rights_holder": plan.rights_holder or str(entry["rights_holder"]),
        "edition": plan.edition or plan.code,
        "edition_name": plan.edition_name or name,
        "publisher": plan.publisher or str(entry["rights_holder"]),
        "storage_url": _live_config("STORAGE_SVC_URL"),
        "internal_secret": _live_config("INTERNAL_SECRET"),
        "library_root": _live_config("CALIBRE_LIBRARY_PATH"),
    }


def _live_config(name: str) -> str:
    """Read one config value as it stands now, not as it stood at boot.

    ``resolve_runtime_config`` rewrites module globals once at startup, so an
    operator who sets the library path in the settings database would otherwise
    not see it take effect until the next restart.
    """
    import services.config as config

    return str(getattr(config, name, "") or "")


def _run_async(coroutine: Any) -> Any:
    """Drive a coroutine from synchronous code, refusing to do it inside a loop."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coroutine)
    coroutine.close()
    raise ImportRefusal(
        "An import was started from inside the running event loop, which would deadlock. "
        "Use the admin import endpoints rather than calling the importer directly."
    )


def run(session, plan: ImportPlan, *, registry=None, import_dir: Path | None = None) -> ImportReport:
    """Install whatever ``plan`` describes. Never raises for an expected failure.

    Refusals (incomplete Bible, bad checksum, unreachable provider) come back as
    ``failed`` reports so the caller can log them and show them; only a bug in the
    service should escape as an exception.
    """
    started = time.monotonic()
    report = ImportReport(status="failed", kind=plan.kind, code=plan.code, source=plan.source_label(), message="")
    try:
        resolved = _resolved(plan)
        report.name = resolved["name"]
        # Normalise here rather than trusting the caller: a directory arrives as a
        # str from the settings database and as a Path from main._import_dir(), and
        # "str / str" is not a message anyone can act on.
        materialised = _materialise(
            plan, resolved, report, registry, Path(import_dir) if import_dir else None
        )
        if materialised.dry_run:
            report.verse_count = materialised.sample_verses
            report.book_count = 1
            report.status = "dry_run"
            report.message = (
                f"{resolved['name']} parses correctly and was NOT installed. "
                "Nothing was written to the Bible database."
            )
            report.log.extend(_record(session, plan, report, int((time.monotonic() - started) * 1000)))
            return report
        if materialised.notes_only:
            if not plan.import_notes:
                raise ImportRefusal(
                    f"{report.source} carries study notes but no verse text, so there is no "
                    f"translation to install. Set import_notes to file its "
                    f"{len(materialised.notes)} notes against an edition."
                )
            report.note_count = _install_notes(session, plan, resolved, materialised, report)
            report.status = "succeeded"
            report.message = (
                f"Filed {report.note_count} study notes against "
                f"{resolved['edition']!r}. The source carries no verse text, so no "
                "translation was installed."
            )
            report.log.extend(_record(session, plan, report, int((time.monotonic() - started) * 1000)))
            return report
        report.staged_path = str(materialised.staged) if materialised.staged else ""
        summary = corpus.import_corpus(
            session,
            code=plan.code,
            name=resolved["name"],
            source_path=materialised.path,
            expected_sha256=plan.sha256 or None,
            language=str(resolved["entry"]["language"]),
            license_class=resolved["license_class"],
            detected_name=resolved["name"],
            edition=resolved["edition"],
            edition_name=resolved["edition_name"],
            publisher=resolved["publisher"],
            rights_holder=resolved["rights_holder"],
            allow_gaps=materialised.allow_gaps,
        )
        report.verse_count = int(summary.get("verses", 0))
        report.book_count = int(summary.get("books", 0))
        report.log.append(
            f"Imported {report.verse_count} verses across {report.book_count} books as {plan.code!r}."
        )
        omitted = list(summary.get("omitted") or [])
        if omitted:
            shown = ", ".join(omitted[:12])
            report.log.append(
                f"{len(omitted)} verses are absent from this translation by design "
                f"({shown}{'...' if len(omitted) > 12 else ''}); they keep their printed numbers."
            )
        if plan.import_notes:
            report.note_count = _install_notes(session, plan, resolved, materialised, report)
        report.status = "succeeded"
        report.message = (
            f"{resolved['name']} is installed as {plan.code!r}"
            + (f" with {report.note_count} study notes." if report.note_count else ".")
        )
    except EXPECTED_FAILURES as exc:
        report.status = "failed"
        report.message = str(exc)
        report.log.append(f"Refused: {exc}")
    report.log.extend(_record(session, plan, report, int((time.monotonic() - started) * 1000)))
    return report


def _materialise(
    plan: ImportPlan, resolved: dict, report: ImportReport, registry, import_dir: Path | None
) -> Materialised:
    """Produce corpus-shaped JSON for ``plan``, and say where it came from."""
    if plan.library_path:
        return _materialise_from_library(plan, resolved, report, import_dir)

    if plan.provider:
        return _materialise_from_provider(plan, resolved, report, registry, import_dir)

    if not plan.source_path:
        raise ImportRefusal(
            "Nothing to import: give a source_path for a file import, a library_path "
            "for one from the Nextcloud shelf, or a provider for an online one."
        )
    source = Path(plan.source_path).expanduser()
    if not source.exists():
        raise FileNotFoundError(f"{source} does not exist on the bible service's filesystem.")
    if not source.is_file():
        raise ImportRefusal(f"{source} is a directory, not a Bible file.")

    report.kind = plan.kind or detect_kind(source)
    if report.kind == "json":
        return Materialised(path=source)

    if report.kind == "pdf":
        extracted = pdf_import.extract(source, strict=True)
        payload = pdf_import.to_source(extracted)
        lines = [
            f"{entry['osis']}: {entry['chapters']} chapters, {entry['verses']} verses"
            + ("" if entry["ok"] else f" -- {entry['problems'][0]}")
            for entry in extracted["reports"]
        ]
        report.log.append(f"Read {len(lines)} books from the PDF.")
        report.log.extend(lines)
        notes: list[dict] = []
    else:
        extracted = epub_import.extract(source)
        report.log.extend(epub_import.report_lines(extracted))
        notes = [note.as_dict() for note in extracted["notes"]]
        if extracted.get("mode") == "commentary":
            report.log.append(
                f"Commentary source: {len(notes)} notes anchored to passages. "
                "It carries no verse text, so nothing is staged for a translation install."
            )
            return Materialised(path=source, notes=notes, notes_only=True)
        payload = epub_import.to_corpus_source(extracted)

    staged = source.with_suffix(source.suffix + ".source.json")
    staged.parent.mkdir(parents=True, exist_ok=True)
    staged.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    report.log.append(f"Staged the extraction beside the source as {staged.name}.")
    return Materialised(path=staged, staged=staged, notes=notes)


def _materialise_from_library(
    plan: ImportPlan, resolved: dict, report: ImportReport, import_dir: Path | None
) -> Materialised:
    """Fetch a Bible off the Nextcloud shelf, then import it like any file.

    The download lands in the import folder and is then handled by the ordinary
    file path below, so a shelf EPUB is parsed by exactly the code a browser
    upload of the same book would be. That is the whole point of doing the
    transfer first and the parsing second: there is one importer to trust, not
    one per source.

    The file is fetched under its own name rather than the translation code so
    two shelves holding ``Bible.epub`` do not overwrite each other, and so the
    operator can recognise it in the import folder afterwards.
    """
    if import_dir is None:
        raise ImportRefusal(
            "A library import needs a writable import folder, and none is configured. "
            "Set bible_import_dir (or BIBLE_IMPORT_DIR)."
        )

    from services.bible import library as library_mod

    client = library_mod.LibraryClient(
        storage_url=resolved.get("storage_url", ""),
        internal_secret=resolved.get("internal_secret", ""),
    )
    root = str(resolved.get("library_root") or "")
    destination = import_dir / library_mod.candidate_names(plan.library_path)
    fetched = _run_async(client.fetch(root=root, path=plan.library_path, destination=destination))
    report.log.append(
        f"Fetched {plan.library_path} from the Nextcloud library to "
        f"{fetched.name} ({fetched.stat().st_size} bytes)."
    )
    report.kind = plan.kind or detect_kind(fetched)
    if report.kind == "json":
        return Materialised(path=fetched)
    return _materialise(plan._replace_source(fetched), resolved, report, registry=None, import_dir=import_dir)


def _materialise_from_provider(
    plan: ImportPlan, resolved: dict, report: ImportReport, registry, import_dir: Path | None
) -> Materialised:
    if registry is None:
        raise ImportRefusal("This service was started without a translation provider registry.")
    provider = registry.get(plan.provider)
    if not provider.configured():
        raise ProviderUnavailable(provider.unconfigured_reason())
    translation = plan.provider_id or plan.code
    cache = build_cache(provider, translation)
    report.log.append(cache.describe())
    if not cache.enabled:
        report.log.append(f"  {cache.reason}")

    if plan.dry_run:
        return _dry_run_from_provider(provider, translation, cache, report)

    directory = import_dir or Path(plan.source_path or ".")
    destination = directory / f"{plan.code}.provider.json"
    estimate = _run_async(provider.estimate(translation, cache=cache))
    try:
        budget = plan.budget if plan.budget is not None else provider_cache.call_budget()
    except ValueError as exc:
        raise ProviderUnavailable(
            f"{exc} No request was made and nothing was written."
        ) from exc
    report.log.extend(_call_log(estimate, cache, budget))
    report.log.append(f"Fetching {translation} from {provider.title}...")

    def progress(index: int, total: int, book: str) -> None:
        report.log.append(f"  book {index}/{total}: {book}")

    fetched, remote_name = _run_async(
        provider.fetch(
            translation,
            destination,
            progress=progress,
            cache=cache,
            budget=budget,
        )
    )
    report.log.extend(_cache_log(cache))
    if not plan.name:
        resolved["name"] = remote_name
        report.name = remote_name
    return Materialised(path=fetched, allow_gaps=True)


def build_cache(provider, translation: str) -> provider_cache.ChapterCache:
    """A cache for this translation, or one that explains why there is none."""
    return provider_cache.ChapterCache(
        provider_cache.cache_root(),
        provider_code=provider.code,
        base_url=provider.base_url,
        translation_id=translation,
    )


def _cache_log(cache: provider_cache.ChapterCache) -> list[str]:
    """Say what the fetch actually cost, so a month is never spent by accident."""
    stats = cache.stats
    lines = [
        f"Requests used: {stats.calls} "
        f"({stats.books_hits + stats.chapter_hits} chapters served from the cache)."
    ]
    if stats.calls == 0:
        lines.append("Nothing was requested: every chapter was already cached.")
    return lines


def _call_log(estimate: dict, cache: provider_cache.ChapterCache, budget: int | None) -> list[str]:
    lines = [
        f"Cost: {estimate['remaining']} of {estimate['chapters']} chapters are not cached yet"
        + (
            f", so {estimate['remaining']} requests"
            if estimate["remaining"]
            else ", so this translation needs no requests at all"
        )
        + "."
    ]
    if budget is not None:
        lines.append(
            f"Call budget for this run: {budget} requests."
            + ("" if estimate["remaining"] <= budget else " This import would be refused.")
        )
    return lines


def _dry_run_from_provider(
    provider, translation: str, cache: provider_cache.ChapterCache, report: ImportReport
) -> Materialised:
    """Prove the translation parses for about one request, and install nothing.

    A whole Bible costs roughly 1,190 requests, so being able to check the
    dialect and the book mapping first is the difference between a wasted month
    and a wasted minute.
    """
    estimate = _run_async(provider.estimate(translation, cache=cache))
    report.log.extend(_call_log(estimate, cache, None))
    if estimate["remaining"] > 1:
        report.log.append(
            f"A dry run fetches one book. {estimate['remaining']} requests are still "
            "outstanding, so this only proves the parsing, not the whole Bible."
        )
    sample = _run_async(provider.sample(translation, cache=cache))
    report.log.append(
        f"Sample {sample['reference']}: {sample['text'][:200]}"
        + ("..." if len(sample["text"]) > 200 else "")
    )
    report.log.append(
        "Nothing was installed and the Bible database was not touched; the fetched "
        "chapter is in the cache, so the real import will not pay for it again."
    )
    return Materialised(path=Path("."), dry_run=True, sample_verses=sample["verse_count"])


def _install_notes(
    session, plan: ImportPlan, resolved: dict, materialised: Materialised, report: ImportReport
) -> int:
    """File the study notes the extraction already yielded against the edition."""
    notes = materialised.notes
    if not notes:
        report.log.append(
            "That file carried no study notes. An e-book from a study Bible publisher is "
            "what carries them; a plain-text Bible does not."
        )
        return 0
    result = study.import_notes(
        session,
        plan.code,
        notes,
        source=plan.source_label(),
        edition=resolved["edition"],
        name=resolved["edition_name"],
        publisher=resolved["publisher"],
        license_class=resolved["license_class"],
        rights_holder=resolved["rights_holder"],
    )
    report.note_count = int(result.get("imported", 0))
    report.log.append(
        f"Filed {report.note_count} study notes against {resolved['edition']!r} "
        f"({result.get('skipped', 0)} skipped)."
    )
    return report.note_count


def _record(session, plan: ImportPlan, report: ImportReport, duration_ms: int) -> list[str]:
    """Persist the run, including the failures. Returns any complaint as lines.

    A history that silently stops recording is worse than a history with a gap,
    so a write failure is reported in the same log the operator is reading
    rather than swallowed into a default.
    """
    try:
        session.add(
            ImportRun(
                source=report.source,
                kind=report.kind,
                code=report.code,
                name=report.name,
                provider=plan.provider,
                provider_id=plan.provider_id,
                status=report.status,
                message=report.message,
                verse_count=report.verse_count,
                book_count=report.book_count,
                note_count=report.note_count,
                log="\n".join(report.log),
                duration_ms=duration_ms,
            )
        )
        session.commit()
    except Exception as exc:  # noqa: BLE001 - reported, never hidden
        session.rollback()
        return [f"Warning: this run was not added to the import history ({exc!s})."]
    return []


def recent_runs(session, *, limit: int = 25) -> list[dict]:
    """The last few import attempts, newest first."""
    from sqlmodel import select

    rows = session.exec(select(ImportRun).order_by(ImportRun.id.desc()).limit(limit)).all()
    return [
        {
            "id": row.id,
            "kind": row.kind,
            "code": row.code,
            "name": row.name,
            "source": row.source,
            "provider": row.provider,
            "status": row.status,
            "message": row.message,
            "verse_count": row.verse_count,
            "book_count": row.book_count,
            "note_count": row.note_count,
            "log": [line for line in str(row.log or "").splitlines() if line],
            "duration_ms": row.duration_ms,
            "created_at": row.created_at.isoformat() if row.created_at else "",
        }
        for row in rows
    ]