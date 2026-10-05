# services/bible/import_corpus.py
"""Install Bible translations into the corpus database.

Which translations exist is declared in ``corpus_manifest.json``, not in code.
Public-domain entries carry a source URL and a checksum, so they install
unattended::

    python3 -m services.bible.import_corpus --manifest

Copyrighted entries (ESV, NIV, NLT, NKJV) deliberately carry no URL: their text
is not ours to redistribute. You import the copy you are licensed to use, and the
manifest supplies the name, licence class and rights holder so nothing has to be
typed twice::

    python3 -m services.bible.import_corpus --only esv --source ~/Books/ESV.json

``--list`` prints the catalogue with what is installed and, for anything missing,
the exact command that would install it.

The checksum is not optional ceremony: it is how a truncated download or a
half-written file is caught before 31,000 verses go missing. ``--sha256``
accepts the digest printed by a previous import, so a re-import can assert it
is replacing the same bytes.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

from sqlmodel import SQLModel, Session, create_engine

from services.bible.corpus import (
    CorpusError,
    catalogue,
    file_sha256,
    import_corpus,
    load_manifest,
)

DOWNLOAD_TIMEOUT = 120


def _database_url(explicit: str | None) -> str:
    if explicit:
        return explicit
    from services.config import BIBLE_DATABASE_URL

    if not BIBLE_DATABASE_URL:
        raise CorpusError(
            "BIBLE_DATABASE_URL is not set, so there is no corpus database to "
            "import into. Set it in .env (the compose default is "
            "sqlite:////data/bible.db) or pass --database-url."
        )
    return BIBLE_DATABASE_URL


def _download(url: str, destination: Path) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "sharedllm-bible-import"})
    try:
        with urllib.request.urlopen(request, timeout=DOWNLOAD_TIMEOUT) as response:
            destination.write_bytes(response.read())
    except urllib.error.URLError as exc:
        raise CorpusError(f"could not download {url}: {exc}") from exc


def _install(session, version: dict, source: Path, sha256: str | None) -> dict:
    if version["license_class"] == "public_domain" and version["sha256"]:
        expected = sha256 or version["sha256"]
    else:
        expected = sha256
    return import_corpus(
        session,
        code=version["code"],
        name=version["name"],
        source_path=source,
        expected_sha256=expected,
        language=version["language"],
        license_class=version["license_class"],
    )


def _licence_note(version: dict) -> str:
    holder = f" ({version['rights_holder']})" if version["rights_holder"] else ""
    return f"{version['name']}{holder} is copyrighted; import your licensed copy."


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Install Bible translations into the corpus")
    parser.add_argument("--list", action="store_true", help="print the catalogue and exit")
    parser.add_argument("--manifest", action="store_true", help="install every public-domain translation")
    parser.add_argument("--only", default=None, help="install one manifest entry by code")
    parser.add_argument("--code", default=None, help="version code (defaults to the manifest entry)")
    parser.add_argument("--name", default=None, help="override the manifest name")
    parser.add_argument("--source", default=None, type=Path, help="source JSON file")
    parser.add_argument("--sha256", default=None, help="expected sha256 of the source file")
    parser.add_argument("--database-url", default=None)
    parser.add_argument(
        "--download-dir",
        default=None,
        type=Path,
        help="where to keep downloaded sources (default: alongside the database)",
    )
    args = parser.parse_args(argv)

    try:
        manifest = {entry["code"]: entry for entry in load_manifest()}
    except CorpusError as exc:
        print(f"corpus manifest unusable: {exc}", file=sys.stderr)
        return 1

    try:
        database_url = _database_url(args.database_url)
    except CorpusError as exc:
        print(f"corpus import failed: {exc}", file=sys.stderr)
        return 1

    engine = create_engine(database_url)
    SQLModel.metadata.create_all(engine)

    try:
        with Session(engine) as session:
            if args.list:
                print(json.dumps(catalogue(session), indent=2))
                return 0

            if args.manifest or args.only:
                return _install_manifest_entries(
                    session,
                    manifest,
                    only=args.only,
                    source=args.source,
                    sha256=args.sha256,
                    download_dir=args.download_dir,
                    database_url=database_url,
                )

            if not args.code or not args.source:
                print(
                    "corpus import failed: pass --manifest to install every "
                    "public-domain translation, --only <code> for one, or "
                    "--code and --source to import a file directly",
                    file=sys.stderr,
                )
                return 2

            version = manifest.get(args.code.strip().lower())
            summary = import_corpus(
                session,
                code=args.code.strip().lower(),
                name=args.name or (version["name"] if version else args.code),
                source_path=args.source,
                expected_sha256=args.sha256,
                language=version["language"] if version else "en",
                license_class=version["license_class"] if version else "public_domain",
            )
    except CorpusError as exc:
        print(f"corpus import failed: {exc}", file=sys.stderr)
        return 1

    if not args.sha256:
        summary["note"] = (
            f"no --sha256 was supplied; pin {summary['sha256']} on the next import "
            f"so a truncated source is caught"
        )
    print(json.dumps(summary, indent=2))
    return 0


def _install_manifest_entries(
    session,
    manifest: dict[str, dict],
    *,
    only: str | None,
    source: Path | None,
    sha256: str | None,
    download_dir: Path | None,
    database_url: str,
) -> int:
    if only is not None:
        entry = manifest.get(only.strip().lower())
        if entry is None:
            known = ", ".join(sorted(manifest))
            print(
                f"corpus import failed: {only!r} is not in corpus_manifest.json "
                f"(known: {known})",
                file=sys.stderr,
            )
            return 2
        candidates = [entry]
    else:
        candidates = [manifest[code] for code in sorted(manifest)]

    installed: list[dict] = []
    skipped: list[dict] = []
    stage_dir = download_dir or Path(database_url.replace("sqlite:///", "")).parent

    for version in candidates:
        local_source = source
        if local_source is None and version["source_url"]:
            stage_dir.mkdir(parents=True, exist_ok=True)
            local_source = stage_dir / f"{version['code']}.json"
            if not local_source.is_file():
                _download(version["source_url"], local_source)
        elif local_source is None:
            skipped.append({"code": version["code"], "reason": _licence_note(version)})
            continue

        try:
            summary = _install(session, version, local_source, sha256)
        except CorpusError as exc:
            skipped.append({"code": version["code"], "reason": str(exc)})
            continue
        summary["license_class"] = version["license_class"]
        installed.append(summary)

    report = {"installed": installed, "skipped": skipped}
    if not installed and skipped:
        print(json.dumps(report, indent=2))
        print("corpus import failed: nothing was installed", file=sys.stderr)
        return 1
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())