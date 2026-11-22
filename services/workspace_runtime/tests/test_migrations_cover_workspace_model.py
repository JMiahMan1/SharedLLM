"""Every column a workspace model gains must reach existing databases.

``SQLModel.metadata.create_all`` creates missing tables and never alters an
existing one, so a field added to a model is invisible to an install with data.
This test pins the columns each table had before ``database.WORKSPACE_COLUMNS``
existed; a column in neither that snapshot nor the migration list fails here,
naming the entry to add.
"""
from sqlalchemy import create_engine, inspect, text
from sqlmodel import SQLModel

import services.workspace_runtime.models  # noqa: F401  (registers the tables)
from services.workspace_runtime import database

BASELINE: dict[str, set[str]] = {
    "workspace": {"access_policy", "auto_pull_enabled", "capabilities", "default_branch", "display_name", "git_remote", "id", "last_raven_mission_id", "local_path", "nextcloud_path", "owner_user", "quarantined", "scope", "sync_mode", "webhook_token"},
    "workspacesyncentry": {"etag", "is_dir", "mtime_ns", "path", "sha256", "size", "workspace_id"},
}


def test_every_model_column_is_in_the_baseline_or_a_migration():
    migrated = {("workspace", column) for column, _ in database.WORKSPACE_COLUMNS}
    missing = []
    for name, table in SQLModel.metadata.tables.items():
        if name not in BASELINE:
            continue
        for column in table.columns:
            if column.name not in BASELINE[name] and (name, column.name) not in migrated:
                missing.append(f'("{name}", "{column.name}", <SQL>)')
    assert not missing, "add these to services/workspace_runtime/database.py WORKSPACE_COLUMNS: " + ", ".join(missing)


def test_an_old_workspace_table_gains_every_new_column(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    SQLModel.metadata.create_all(engine)
    keep = sorted(BASELINE["workspace"])
    defs = ['"id" PRIMARY KEY' if column == "id" else f'"{column}"' for column in keep]
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE workspace"))
        conn.execute(text(f"CREATE TABLE workspace ({', '.join(defs)})"))

    monkeypatch.setattr(database, "engine", engine)
    database._migrate_workspace_table()

    have = {column["name"] for column in inspect(engine).get_columns("workspace")}
    wanted = {column.name for column in SQLModel.metadata.tables["workspace"].columns}
    assert wanted <= have, sorted(wanted - have)
