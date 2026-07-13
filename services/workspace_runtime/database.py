from sqlalchemy import inspect, text
from sqlmodel import Session, SQLModel, create_engine

from services.config import WORKSPACE_DATABASE_URL

DATABASE_URL = WORKSPACE_DATABASE_URL
engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False, "timeout": 30} if "sqlite" in DATABASE_URL else {}
)


def init_db():
    SQLModel.metadata.create_all(engine)
    _migrate_workspace_table()


def _migrate_workspace_table():
    inspector = inspect(engine)
    try:
        columns = {column["name"] for column in inspector.get_columns("workspace")}
    except Exception:
        return

    with engine.begin() as conn:
        if "repo_url" not in columns:
            conn.execute(text("ALTER TABLE workspace ADD COLUMN repo_url VARCHAR"))
        if "webhook_token_enc" not in columns:
            conn.execute(text("ALTER TABLE workspace ADD COLUMN webhook_token_enc VARCHAR"))
        if "auto_backup_enabled" not in columns:
            conn.execute(text("ALTER TABLE workspace ADD COLUMN auto_backup_enabled BOOLEAN DEFAULT 0"))
        if "excludes" not in columns:
            # Use TEXT for JSON storage in SQLite
            conn.execute(text("ALTER TABLE workspace ADD COLUMN excludes TEXT"))
        if "is_default" not in columns:
            conn.execute(text("ALTER TABLE workspace ADD COLUMN is_default BOOLEAN DEFAULT 0"))
        if "created_at" not in columns:
            # Add the column WITHOUT a server default: some SQLite builds / legacy
            # file formats reject ADD COLUMN ... DEFAULT CURRENT_TIMESTAMP ("Cannot
            # add a column with non-constant default"). New rows still get created_at
            # via the model's default_factory, and we backfill existing rows here.
            conn.execute(text("ALTER TABLE workspace ADD COLUMN created_at TIMESTAMP"))
            conn.execute(
                text("UPDATE workspace SET created_at = CURRENT_TIMESTAMP WHERE created_at IS NULL")
            )


def get_session():
    with Session(engine) as session:
        yield session
