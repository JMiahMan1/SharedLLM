# services/bible/migrations.py
"""Schema repairs that ``create_all`` cannot do on its own.

``SQLModel.metadata.create_all`` creates tables that do not exist and leaves
existing ones completely alone. That is the right default, but it means adding a
column is invisible to an install that already has a database -- the service
starts, every query that touches the new column raises, and the only symptom is
a broken reader. There is no Alembic in this service, so the few column
additions we have made are applied here.

The rules this module follows:

* **Idempotent.** It runs on every boot and does nothing when the database is
  already current.
* **Additive only.** It adds tables and columns. It never drops or rewrites one,
  so there is no path where running it loses imported Scripture.
* **It reports.** Every applied step and every table already inspected is
  returned, so a boot log can say what happened rather than leaving the operator
  to guess whether an import is still readable.
* **It never guesses.** A database that cannot be inspected raises, naming the
  database, rather than being quietly assumed current.

A migration that cannot run leaves the database exactly as it found it: the
version backfill and the edition backfill are two separate statements, and a
failure in the second does not roll back the first, because both are safe to
repeat.
"""
from __future__ import annotations

import logging

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

log = logging.getLogger(__name__)

#: ``(table, column, ddl type, default literal)``. Only additive changes belong
#: here; anything that would rewrite imported text belongs in a re-import.
COLUMNS: tuple[tuple[str, str, str, str], ...] = (
    ("studynote", "edition_code", "TEXT", "''"),
    ("userbiblestate", "default_edition", "TEXT", "''"),
    ("userbiblestate", "favorite_version", "TEXT", "''"),
    ("userbiblestate", "compare_version", "TEXT", "''"),
    ("userbiblestate", "cross_version_notes", "INTEGER", "0"),
    ("userbiblestate", "show_notes", "INTEGER", "0"),
    ("narrationaudio", "chunk_index", "INTEGER", "0"),
)


class MigrationError(RuntimeError):
    """The database is not in a state this module can repair."""


def _existing_columns(engine: Engine, table: str) -> dict[str, object]:
    inspector = inspect(engine)
    if table not in inspector.get_table_names():
        raise MigrationError(
            f"table {table!r} is missing; run "
            "`python -m sqlmodel` create_all, or re-import the corpus"
        )
    return {str(column["name"]): column for column in inspector.get_columns(table)}


def apply(engine: Engine) -> dict:
    """Bring an existing database up to the current schema. Returns a summary."""
    applied: list[str] = []
    inspected: list[str] = []

    for table, column, ddl_type, default in COLUMNS:
        columns = _existing_columns(engine, table)
        inspected.append(table)
        if column in columns:
            continue
        with engine.begin() as connection:
            connection.execute(
                text(
                    f"ALTER TABLE {table} ADD COLUMN {column} {ddl_type}"
                    f" NOT NULL DEFAULT {default}"
                )
            )
        applied.append(f"{table}.{column}")
        log.info("[Bible] added column %s.%s", table, column)

    backfilled = _backfill_editions(engine, inspected)
    return {"applied": applied, "inspected": sorted(set(inspected)), "editions_created": backfilled}


def _backfill_editions(engine: Engine, inspected: list[str]) -> list[str]:
    """Give every translation that already has notes an edition to own them.

    Notes imported before editions existed carry an empty ``edition_code``. Left
    alone they would be invisible to every edition-scoped query, which looks
    exactly like "the import never happened". They are adopted into an edition
    named after the translation ("NKJV") -- the honest description, since that
    is what was installed -- and every installed translation gets an edition row
    so the picker has something to show for plain public-domain text too.
    """
    if "studynote" not in inspected:
        return []
    _existing_columns(engine, "bibleedition")
    _existing_columns(engine, "bibleversion")

    created: list[str] = []
    with engine.begin() as connection:
        adopted = connection.execute(
            text(
                "SELECT version_code, COUNT(*) FROM studynote"
                " WHERE edition_code IS NULL OR edition_code = ''"
                " GROUP BY version_code"
            )
        ).all()
        for version_code, count in adopted:
            connection.execute(
                text(
                    "UPDATE studynote SET edition_code = :version"
                    " WHERE edition_code IS NULL OR edition_code = ''"
                ),
                {"version": version_code},
            )
            name = connection.execute(
                text("SELECT name FROM bibleversion WHERE code = :code"),
                {"code": version_code},
            ).scalar()
            connection.execute(
                text(
                    "INSERT INTO bibleedition"
                    " (code, version_code, name, publisher, language, license_class,"
                    "  rights_holder, note_count, note_kinds, imported_at)"
                    " SELECT :code, code, name, '', language, license_class, '', :count, '',"
                    "        CURRENT_TIMESTAMP"
                    " FROM bibleversion WHERE code = :code"
                ),
                {"code": version_code, "count": int(count)},
            )
            created.append(f"{version_code} (adopted {count} notes)")

        empty = connection.execute(
            text("SELECT code, name, language, license_class FROM bibleversion")
        ).all()
        for code, name, language, license_class in empty:
            exists = connection.execute(
                text("SELECT 1 FROM bibleedition WHERE code = :code"), {"code": code}
            ).first()
            if exists:
                continue
            connection.execute(
                text(
                    "INSERT INTO bibleedition"
                    " (code, version_code, name, publisher, language, license_class,"
                    "  rights_holder, note_count, note_kinds, imported_at)"
                    " VALUES (:code, :code, :name, '', :language, :license_class, '', 0, '',"
                    "         CURRENT_TIMESTAMP)"
                ),
                {
                    "code": code,
                    "name": f"{name} (text only)",
                    "language": language,
                    "license_class": license_class,
                },
            )
            created.append(f"{code} (text only)")
    if created:
        log.info("[Bible] created study editions: %s", ", ".join(created))
    return created