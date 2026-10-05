# services/bible/editions.py
"""Manage the study Bibles installed on this server.

    python3 -m services.bible.editions list
    python3 -m services.bible.editions rename nkjv nkjv-tmn --name "NKJV Study Bible"
    python3 -m services.bible.editions remove nkjv-macarthur

Translations are not editable here. Verse text is imported and verified; this
command only touches the study layer, which is the part a family accumulates.

Everything refuses rather than guessing. Renaming onto an existing code is an
error, not a merge, because a merge would silently drop one study Bible's
commentary. Removing a translation's own text-only edition is refused outright,
because a translation is not a study Bible and a reader must never be able to
delete their Scripture through this door.
"""
from __future__ import annotations

import argparse
import json
import sys

from sqlmodel import SQLModel, Session, create_engine

from services.bible import migrations
from services.bible.corpus import (
    CorpusError,
    edition_catalogue,
    list_editions,
    refresh_edition_notes,
    remove_edition,
    rename_edition,
)
from services.bible.refs import ReferenceError


def _database_url(explicit: str | None) -> str:
    if explicit:
        return explicit
    from services.config import BIBLE_DATABASE_URL

    if not BIBLE_DATABASE_URL:
        raise CorpusError(
            "BIBLE_DATABASE_URL is not set, so there is no corpus database to "
            "look at. Set it in .env (the compose default is sqlite:////data/bible.db) "
            "or pass --database-url."
        )
    return BIBLE_DATABASE_URL


def _lines(session: Session) -> list[str]:
    rows: list[str] = []
    for entry in list_editions(session):
        kinds = [str(k) for k in (entry.get("note_kinds") or [])]
        rows.append(
            f"  {entry['code']:<18} {entry['name']}"
            + (f" — {entry['publisher']}" if entry["publisher"] else "")
            + f"  [{entry['note_count']} notes"
            + (f", {', '.join(kinds)}" if kinds else "")
            + f", {entry['license_class']}]"
        )
    if not rows:
        rows.append("  (no study editions installed)")
    known = [e for e in edition_catalogue(session) if not e["installed"]]
    for entry in known:
        rows.append(f"  {entry['code']:<18} {entry['name']} — not installed")
        if entry["note"]:
            rows.append(f"      {entry['note']}")
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Manage installed study Bibles")
    parser.add_argument("command", choices=["list", "rename", "remove"])
    parser.add_argument("target", nargs="?", default=None, help="the edition code")
    parser.add_argument("replacement", nargs="?", default=None, help="the new edition code")
    parser.add_argument("--name", default="", help="new display name (rename only)")
    parser.add_argument("--database-url", default=None)
    args = parser.parse_args(argv)

    if args.command != "list" and not args.target:
        print(f"editions {args.command} needs the edition code as its argument", file=sys.stderr)
        return 2
    if args.command == "rename" and not args.replacement:
        print("editions rename needs the new edition code as its second argument", file=sys.stderr)
        return 2

    try:
        engine = create_engine(_database_url(args.database_url))
        SQLModel.metadata.create_all(engine)
        # An existing corpus predates the edition columns. Bring it forward the
        # same way the service does, or a database the running service can read
        # would look empty here — which reads as "not installed" and is wrong.
        applied = migrations.apply(engine)
        with Session(engine) as session:
            if args.command == "list":
                refreshed = refresh_edition_notes(session)
                print("Installed study editions:")
                for line in _lines(session):
                    print(line)
                if applied["applied"] or applied["editions_created"]:
                    print()
                    print(f"Migrated: added {', '.join(applied['applied']) or 'nothing'}")
                    for line in applied["editions_created"]:
                        print(f"Created edition {line}")
                if refreshed:
                    print()
                    print(f"Recounted notes for {', '.join(refreshed)}")
                return 0
            if args.command == "rename":
                result = rename_edition(
                    session, args.target, args.replacement, name=args.name
                )
                print(json.dumps(result, indent=2))
                return 0
            result = remove_edition(session, args.target)
            print(json.dumps(result, indent=2))
            return 0
    except (CorpusError, ReferenceError) as exc:
        print(f"editions {args.command} failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())