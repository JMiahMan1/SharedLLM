import os
from sqlmodel import Session, SQLModel, create_engine, select
from sqlalchemy import inspect, text
try:
    from .models import Workspace
except (ImportError, ValueError):
    from models import Workspace

DATABASE_URL = os.getenv("WORKSPACE_DATABASE_URL", "sqlite:///./workspace_runtime.db")
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


def get_session():
    with Session(engine) as session:
        yield session
