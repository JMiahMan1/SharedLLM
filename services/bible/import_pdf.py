# services/bible/import_pdf.py
"""Install a Bible from a PDF you have the right to read.

    python3 -m services.bible.import_pdf --report ~/Books/ESV.pdf
    python3 -m services.bible.import_pdf --code esv --source ~/Books/ESV.pdf

``--report`` is the mode that matters. It prints, per book, how many chapters
came out against how many the Bible has, so a layout we mis-read is visible
before anything is written. ``--import`` refuses to write unless every book
has the right chapter count, because a Bible with the wrong number of chapters
is worse than no Bible: the reader would serve chapter 51 of Genesis from the
next book's text.

A scanned PDF has no text layer. This says so instead of importing an empty
database; run OCR first (``ocrmypdf``) and try again.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from sqlmodel import SQLModel, Session, create_engine

from services.bible.books import BOOKS
from services.bible.corpus import CorpusError, import_corpus, load_manifest
from services.bible.pdf_import import PdfImportError, extract, to_source

STAGE_SUFFIX = ".source.json"


def _database_url(explicit: str | None) -> str:
    if explicit:
        return explicit
    from services.config import BIBLE_DATABASE_URL

    if not BIBLE_DATABASE_URL:
        raise PdfImportError(
            "BIBLE_DATABASE_URL is not set, so there is no corpus database to "
            "import into. Set it in .env (the compose default is "
            "sqlite:////data/bible.db) or pass --database-url."
        )
    return BIBLE_DATABASE_URL


def report(path: Path, *, strict: bool = False) -> dict:
    result = extract(path, strict=strict)
    books = result["books"]
    verses = sum(len(c) for chapters in books.values() for c in chapters)
    bad = [b for b in result["reports"] if not b["ok"]]
    return {
        "path": str(path),
        "columns": result["columns"],
        "body_size": result["body_size"],
        "books_found": len(books),
        "books_expected": len(BOOKS),
        "verses_found": verses,
        "books_needing_attention": [b["name"] for b in bad],
        "reports": result["reports"],
        "warnings": result["warnings"],
    }


def _print_report(data: dict) -> None:
    print(f"{data['path']}")
    print(
        f"  {data['books_found']}/{data['books_expected']} books, "
        f"{data['verses_found']} verses, {data['columns']}-column, "
        f"body text {data['body_size']}pt"
    )
    for warning in data["warnings"]:
        print(f"  ! {warning}")
    for entry in data["reports"]:
        mark = "ok  " if entry["ok"] else "FAIL"
        print(
            f"  {mark} {entry['name']:<22} {entry['chapters']:>3}/"
            f"{entry['expected_chapters']:<3} chapters  "
            f"{entry['verses']:>5} verses"
        )
        for problem in entry["problems"]:
            print(f"       - {problem}")
    if data["books_needing_attention"]:
        print(
            "\n  Not imported. Every book must have its full chapter count; "
            "re-run with --report to see which."
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Import a Bible translation from a PDF")
    parser.add_argument("--source", required=True, type=Path, help="the PDF")
    parser.add_argument("--code", default=None, help="version code (defaults to the PDF stem)")
    parser.add_argument("--name", default=None, help="override the detected title")
    parser.add_argument("--report", action="store_true", help="only report; never write")
    parser.add_argument(
        "--import",
        dest="do_import",
        action="store_true",
        help="write to the corpus (refuses unless every book checks out)",
    )
    parser.add_argument("--stage", default=None, type=Path, help="where to keep the extracted JSON")
    parser.add_argument("--database-url", default=None)
    args = parser.parse_args(argv)

    try:
        database_url = _database_url(args.database_url)
    except PdfImportError as exc:
        print(f"pdf import failed: {exc}", file=sys.stderr)
        return 1

    try:
        data = report(args.source)
    except PdfImportError as exc:
        print(f"pdf import failed: {exc}", file=sys.stderr)
        return 1

    _print_report(data)
    if args.report or not args.do_import:
        if not args.do_import and not args.report:
            print("\n  Nothing was written. Add --import to install it.", file=sys.stderr)
            return 2
        return 0

    unfinished = [b for b in data["reports"] if not b["ok"]]
    if unfinished:
        print(
            "pdf import failed: "
            + ", ".join(b["name"] for b in unfinished)
            + " did not extract a full chapter count, so the text cannot be "
            "trusted. See the report above.",
            file=sys.stderr,
        )
        return 1

    try:
        result = extract(args.source)
        payload = to_source(result)
    except PdfImportError as exc:
        print(f"pdf import failed: {exc}", file=sys.stderr)
        return 1

    code = (args.code or args.source.stem).strip().lower()
    manifest = {entry["code"]: entry for entry in load_manifest()}
    known = manifest.get(code)
    stage = args.stage or args.source.with_suffix(STAGE_SUFFIX)
    stage.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    try:
        database_url = args.database_url or database_url
        engine = create_engine(database_url)
        SQLModel.metadata.create_all(engine)
        with Session(engine) as session:
            summary = import_corpus(
                session,
                code=code,
                name=args.name or (known["name"] if known else args.source.stem),
                source_path=stage,
                expected_sha256=None,
                language=known["language"] if known else "en",
                license_class=known["license_class"] if known else "licensed",
            )
    except CorpusError as exc:
        print(f"pdf import failed: {exc}", file=sys.stderr)
        return 1

    summary["license_class"] = known["license_class"] if known else "licensed"
    summary["rights_holder"] = known["rights_holder"] if known else ""
    summary["extracted_from"] = str(args.source)
    summary["staged_source"] = str(stage)
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())