"""Every column a model gains must reach existing databases.

``create_all`` never adds a column to a table that already exists, so a new
field on a model is invisible to an install with data: every query on that
table then fails with "no such column". (Seven reader-state fields shipped that
way once and the Bible home card and reader answered 500 until a migration
followed.) This test pins the columns tables had before ``migrations.COLUMNS``
existed for them; a field that is in neither that snapshot nor COLUMNS fails
here, naming the line to add.

A brand new table needs nothing: ``create_all`` creates it whole. Add it to
BASELINE once it has shipped, so later columns on it are caught too.
"""
from sqlalchemy import create_engine, inspect, text
from sqlmodel import SQLModel

import services.bible.models  # noqa: F401  (registers the tables)
from services.bible import migrations

BASELINE: dict[str, set[str]] = {
    "achievementearned": {'achievement_id', 'earned_on', 'points', 'username'},
    "biblebook": {'chapters', 'id', 'name', 'order', 'osis', 'version_code'},
    "bibleedition": {'code', 'id', 'imported_at', 'language', 'license_class', 'name', 'note_count', 'note_kinds', 'publisher', 'rights_holder', 'version_code'},
    "bibleverse": {'chapter', 'osis', 'text', 'verse', 'version_code'},
    "bibleversion": {'code', 'id', 'imported_at', 'language', 'license_class', 'name', 'verse_count'},
    "concordanceguess": {'id', 'samples', 'share', 'strongs', 'testament', 'word'},
    "concordanceline": {'chapter', 'context', 'id', 'osis', 'strongs', 'verse', 'word'},
    "devotional": {'day', 'day_of_year', 'id', 'kind', 'month', 'reference', 'source', 'text', 'title', 'url', 'work'},
    "gamescore": {'correct', 'counted', 'created_at', 'day', 'duration_ms', 'id', 'mode', 'seed', 'total', 'username', 'version_code'},
    "importedbook": {'author', 'chapter_count', 'created_at', 'id', 'kind', 'source', 'source_path', 'title'},
    "importedbookalias": {'book_id', 'created_at', 'id', 'old_ordinal', 'old_section_id', 'ordinal', 'section_id'},
    "importedbookasset": {'book_id', 'data', 'id', 'key', 'mime'},
    "importedbookchapter": {'book_id', 'id', 'label', 'ordinal', 'questions', 'text'},
    "importrun": {'book_count', 'code', 'created_at', 'duration_ms', 'id', 'kind', 'log', 'message', 'name', 'note_count', 'provider', 'provider_id', 'source', 'status', 'verse_count'},
    "interlinearverse": {'book', 'chapter', 'id', 'imported_at', 'language', 'source', 'verse', 'words'},
    "lexiconentry": {'definition', 'gk', 'imported_at', 'language', 'part_of_speech', 'source', 'strongs', 'transliteration', 'usage', 'usage_total'},
    "memoryverse": {'chapter', 'created_at', 'due_on', 'id', 'lapses', 'last_score', 'level', 'osis', 'ref', 'reviews', 'set_id', 'username', 'verse_end', 'verse_start', 'version_code'},
    "narrationaudio": {'audio', 'created_at', 'key', 'mime_type', 'reference', 'verse_count', 'version_code', 'voice'},
    "planenrollment": {'active', 'completed_days', 'created_at', 'finished_on', 'id', 'plan_id', 'started_on', 'username'},
    "plangroup": {'created_at', 'created_by', 'id', 'name', 'plan_id', 'started_on'},
    "plangroupmember": {'created_at', 'group_id', 'id', 'invited_by', 'joined_at', 'status', 'username'},
    "planreflection": {'day', 'id', 'plan_id', 'text', 'updated_at', 'username'},
    "readingevent": {'created_at', 'day', 'id', 'kind', 'ref', 'username', 'value'},
    "studynote": {'body', 'chapter', 'id', 'imported_at', 'kind', 'ordinal', 'osis', 'source', 'verse', 'version_code'},
    "studysession": {'created_at', 'edition', 'id', 'reference', 'title', 'updated_at', 'username', 'version'},
    "studysessionturn": {'answer', 'created_at', 'id', 'question', 'references', 'session_id'},
    "userbiblestate": {'default_version', 'font_scale', 'last_book', 'last_chapter', 'last_open_day', 'last_read_at', 'last_verse', 'line_height', 'read_aloud_voice', 'split_view', 'theme', 'username'},
    "versecontext": {'body', 'chapter', 'edition_code', 'generated_at', 'key', 'model', 'notes_used', 'osis', 'reference', 'text', 'verse', 'verses_used', 'version_code'},
    "versemark": {'chapter', 'color', 'created_at', 'id', 'kind', 'note_path', 'note_preview', 'osis', 'updated_at', 'username', 'verse_end', 'verse_start', 'version_code'},
}


def test_every_model_column_is_in_the_baseline_or_a_migration():
    added = {(table, column) for table, column, _, _ in migrations.COLUMNS}
    missing = []
    for name, table in SQLModel.metadata.tables.items():
        if name not in BASELINE:
            continue
        for column in table.columns:
            if column.name not in BASELINE[name] and (name, column.name) not in added:
                missing.append(f'("{name}", "{column.name}", <TYPE>, <DEFAULT>)')
    assert not missing, "add these to services/bible/migrations.py COLUMNS: " + ", ".join(missing)


def test_an_old_reader_state_table_gains_every_new_column(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    SQLModel.metadata.create_all(engine)
    new = [c for t, c, _, _ in migrations.COLUMNS if t == "userbiblestate"]
    keep = sorted(BASELINE["userbiblestate"])
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE userbiblestate"))
        conn.execute(text(f"CREATE TABLE userbiblestate ({', '.join(f'{c}' for c in keep)})"))
    migrations.apply(engine)
    have = {c["name"] for c in inspect(engine).get_columns("userbiblestate")}
    assert set(new) <= have
