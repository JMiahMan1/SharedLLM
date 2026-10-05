# services/bible/import_epub.py
"""Install a Bible from an EPUB you have the right to read.

    python3 -m services.bible.import_epub --report ~/Books/nkjv.epub
    python3 -m services.bible.import_epub --code nkjv --import ~/Books/nkjv.epub
    python3 -m services.bible.import_epub --code nkjv --import --import-notes ~/Books/nkjv.epub

EPUB is the good case: an e-book carries a real table of contents, real chapter
and verse anchors, and usually a study apparatus, so the extraction is exact
rather than guessed. ``--report`` prints, per book, how many chapters came out
against how many the Bible has, plus how much study material was found and how
much of it is cited from a verse. ``--import`` refuses to write the text unless
every book has its full chapter count, because a Bible with the wrong number of
chapters is worse than no Bible: the reader would serve chapter 51 of Genesis
from the next book's text.

Study notes are a separate concern from the text, so they are a separate flag.
``--import-notes`` writes commentary, footnotes, introductions and headings into
their own table, keyed to the verse that cites them. Without the flag the reader
gets clean scripture and nothing else, which is the right default for the plain
translations in ``corpus_manifest.json``.

A translation can carry several study Bibles, so ``--edition`` says which one
this file is. The verse text is installed once per translation regardless of how
many study Bibles explain it -- a second study Bible of NKJV adds its own notes
and leaves the Thomas Nelson commentary untouched. ``--notes-only`` skips the
text entirely for the case where a family already has the translation and is
adding commentary for it.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from sqlmodel import SQLModel, Session, create_engine

from services.bible.corpus import (
    CorpusError,
    import_corpus,
    load_editions,
    load_manifest,
    require_version,
)
from services.bible.epub_import import EpubImportError, extract, report_lines, to_source
from services.bible.refs import ReferenceError
from services.bible.study import import_notes

STAGE_SUFFIX = ".source.json"


def _database_url(explicit: str | None) -> str:
    if explicit:
        return explicit
    from services.config import BIBLE_DATABASE_URL

    if not BIBLE_DATABASE_URL:
        raise EpubImportError(
            "BIBLE_DATABASE_URL is not set, so there is no corpus database to "
            "import into. Set it in .env (the compose default is "
            "sqlite:////data/bible.db) or pass --database-url."
        )
    return BIBLE_DATABASE_URL


def report(path: Path) -> dict:
    return extract(path)


def _print_report(result: dict) -> None:
    for line in report_lines(result):
        print(line)
    unfinished = [r for r in result["reports"] if not r.ok]
    if unfinished:
        print(
            "\n  Not imported. Every book must have its full chapter count; "
            "re-run with --report to see which."
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Import a Bible translation from an EPUB")
    parser.add_argument("--source", required=True, type=Path, help="the EPUB")
    parser.add_argument("--code", default=None, help="version code (defaults to the EPUB stem)")
    parser.add_argument("--name", default=None, help="override the detected title")
    parser.add_argument("--report", action="store_true", help="only report; never write")
    parser.add_argument(
        "--import",
        dest="do_import",
        action="store_true",
        help="write to the corpus (refuses unless every book checks out)",
    )
    parser.add_argument(
        "--import-notes",
        action="store_true",
        help="also write the commentary/footnotes found in the EPUB",
    )
    parser.add_argument(
        "--edition",
        default=None,
        help=(
            "study edition code, e.g. nkjv-macarthur (defaults to the code in the "
            "corpus manifest for this translation, else the translation code)"
        ),
    )
    parser.add_argument(
        "--edition-name",
        default=None,
        help="name of the study Bible, e.g. 'NKJV, The MacArthur Study Bible'",
    )
    parser.add_argument("--publisher", default=None, help="who published this study Bible")
    parser.add_argument(
        "--rights-holder",
        default=None,
        help="who licenses the notes (defaults to the translation's rights holder)",
    )
    parser.add_argument(
        "--notes-only",
        action="store_true",
        help="do not touch the verse text; add this study Bible's notes to an installed translation",
    )
    parser.add_argument("--stage", default=None, type=Path, help="where to keep the extracted JSON")
    parser.add_argument("--database-url", default=None)
    args = parser.parse_args(argv)

    if not args.do_import:
        try:
            data = report(args.source)
        except EpubImportError as exc:
            print(f"epub import failed: {exc}", file=sys.stderr)
            return 1

        _print_report(data)
        if args.report:
            return 0
        print("\n  Nothing was written. Add --import to install it.", file=sys.stderr)
        return 2

    try:
        database_url = _database_url(args.database_url)
    except EpubImportError as exc:
        print(f"epub import failed: {exc}", file=sys.stderr)
        return 1

    code = (args.code or args.source.stem).strip().lower()
    manifest = {entry["code"]: entry for entry in load_manifest()}
    known = manifest.get(code)
    if args.notes_only and not args.import_notes:
        print(
            "epub import failed: --notes-only has nothing to do without --import-notes. "
            "Add --import-notes, or drop --notes-only to install the verse text too.",
            file=sys.stderr,
        )
        return 2

    try:
        data = report(args.source)
    except EpubImportError as exc:
        print(f"epub import failed: {exc}", file=sys.stderr)
        return 1

    _print_report(data)

    if args.notes_only:
        unfinished_notes = [r for r in data["reports"] if not data["notes"]]
        if unfinished_notes:
            print(
                "epub import failed: no study notes were found in "
                f"{args.source.name}, so there is nothing to import.",
                file=sys.stderr,
            )
            return 1

    if not args.notes_only:
        unfinished = [r for r in data["reports"] if not r.ok]
        if unfinished:
            print(
                "epub import failed: "
                + ", ".join(r.name for r in unfinished)
                + " did not extract a full chapter count, so the text cannot be "
                "trusted. See the report above.",
                file=sys.stderr,
            )
            return 1

    edition_code = _edition_for(args.edition, code, known)
    edition_entry = _edition_entry(edition_code)

    summary: dict = {"code": code, "edition": edition_code}
    notes_summary = None
    try:
        engine = create_engine(database_url)
        SQLModel.metadata.create_all(engine)
        with Session(engine) as session:
            if args.notes_only:
                require_version(session, code)
                summary["text"] = {
                    "imported": 0,
                    "reason": "--notes-only was used; the verse text was left alone",
                }
            else:
                stage = args.stage or args.source.with_suffix(STAGE_SUFFIX)
                payload = to_source(data)
                stage.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
                summary = import_corpus(
                    session,
                    code=code,
                    name=args.name or (known["name"] if known else data["title"]),
                    source_path=stage,
                    expected_sha256=None,
                    language=known["language"] if known else "en",
                    license_class=known["license_class"] if known else "licensed",
                    edition=edition_code,
                    edition_name=_edition_display_name(args, data, edition_entry, code),
                    publisher=args.publisher or (edition_entry or {}).get("publisher", ""),
                    rights_holder=args.rights_holder
                    or (edition_entry or {}).get("rights_holder", "")
                    or (known["rights_holder"] if known else ""),
                )
                summary["staged_source"] = str(stage)
            if args.import_notes:
                notes_summary = import_notes(
                    session,
                    code,
                    data["notes"],
                    source=args.source.name,
                    edition=edition_code,
                    name=_edition_display_name(args, data, edition_entry, code),
                    publisher=args.publisher or (edition_entry or {}).get("publisher", ""),
                    license_class=(
                        edition_entry["license_class"] if edition_entry else "licensed"
                    ),
                    rights_holder=args.rights_holder
                    or (edition_entry or {}).get("rights_holder", "")
                    or (known["rights_holder"] if known else ""),
                )
    except (CorpusError, EpubImportError, ReferenceError) as exc:
        print(f"epub import failed: {exc}", file=sys.stderr)
        return 1

    summary["code"] = code
    summary["edition"] = edition_code
    summary["license_class"] = known["license_class"] if known else "licensed"
    summary["rights_holder"] = known["rights_holder"] if known else ""
    summary["extracted_from"] = str(args.source)
    if notes_summary is None:
        summary["notes"] = {
            "imported": 0,
            "skipped": 0,
            "reason": "study notes were not requested; add --import-notes to keep them",
        }
    else:
        summary["notes"] = notes_summary
    print(json.dumps(summary, indent=2))
    return 0


def _edition_for(explicit: str | None, code: str, known: dict | None) -> str:
    """Which study edition this import belongs to.

    An explicit ``--edition`` always wins. Otherwise a catalogued edition for
    this translation is used, so re-importing the NKJV study Bible lands back in
    its own row instead of creating a second copy of the same commentary.
    """
    if explicit:
        return str(explicit).strip().lower()
    try:
        for entry in load_editions():
            if entry["version"] == code:
                return str(entry["code"])
    except CorpusError:
        pass
    return code


def _edition_entry(edition_code: str) -> dict | None:
    try:
        return next(
            (e for e in load_editions() if e["code"] == edition_code), None
        )
    except CorpusError:
        return None


def _edition_display_name(args, data: dict, edition_entry: dict | None, code: str) -> str:
    """What the reader will see this study Bible called."""
    if args.edition_name:
        return args.edition_name
    if edition_entry:
        return str(edition_entry["name"])
    if args.name or data.get("title"):
        return f"{args.name or data['title']} (study notes)"
    return f"{code} study notes"


if __name__ == "__main__":
    raise SystemExit(main())