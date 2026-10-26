"""SQLite connection pragmas shared by the services that keep a local database.

Identity, health and bible each keep a file-backed SQLite database that one
service process writes and the same process reads. On the default rollback
journal a single reader blocks the writer, so a burst of parallel requests turns
into "database is locked" 500s. WAL lets readers and the writer proceed
together, and a busy timeout makes the rare genuine contention wait instead of
failing immediately.

RAG already sets these by hand in ``services/rag/db.py``; this helper is for the
SQLModel-based services, where the connection is opened by SQLAlchemy.
"""

from sqlalchemy import Column, Engine, MetaData, event, inspect, text

# Wait this long for a competing writer before raising "database is locked".
BUSY_TIMEOUT_MS = 5000


def apply_sqlite_pragmas(engine: Engine) -> None:
    """Enable WAL, a busy timeout and NORMAL sync on every connection.

    Safe to call for an in-memory database too: SQLite simply keeps its memory
    journal mode. Must be called before the first connection is opened, which is
    why each service calls it right after ``create_engine``.

    A no-op for any other dialect, so a service can call it unconditionally.
    """
    if engine.dialect.name != "sqlite":
        return

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragmas(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        try:
            # journal_mode is persisted in the database file, so an existing
            # rollback-journal database switches over on its first connection.
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
            cursor.execute("PRAGMA synchronous=NORMAL")
        finally:
            cursor.close()


def _default_sql(column: Column) -> str | None:
    """A constant default SQLite accepts for a newly added column."""
    default = column.default
    if default is not None and getattr(default, "is_scalar", False):
        value = default.arg
        if isinstance(value, bool):
            return "1" if value else "0"
        if isinstance(value, (int, float)):
            return str(value)
        return repr(str(value))
    if column.nullable:
        return None
    # SQLite refuses ADD COLUMN NOT NULL without a default, so the rows that are
    # already there need something of the right shape.
    name = type(column.type).__name__.upper()
    if any(word in name for word in ("INT", "FLOAT", "NUMERIC", "DECIMAL", "REAL", "BOOLEAN")):
        return "0"
    return "''"


def add_missing_columns(engine: Engine, metadata: MetaData) -> list[str]:
    """Add every column the models declare but the database is missing.

    ``create_all`` creates missing *tables* and never alters an existing one, so
    without this the next field added to a model crash-loops the service with
    "no such column" until somebody writes the ALTER by hand. Only additive:
    columns are never dropped or retyped. Returns the columns it added, as
    ``table.column``, so a caller can log them. A no-op for other dialects.
    """
    if engine.dialect.name != "sqlite":
        return []

    inspector = inspect(engine)
    present = set(inspector.get_table_names())
    added: list[str] = []
    for table in metadata.sorted_tables:
        if table.name not in present:
            continue
        have = {column["name"] for column in inspector.get_columns(table.name)}
        for column in table.columns:
            if column.name in have:
                continue
            ddl = f'ALTER TABLE "{table.name}" ADD COLUMN "{column.name}" {column.type.compile(engine.dialect)}'
            default = _default_sql(column)
            if default is not None:
                ddl += f" DEFAULT {default}"
            with engine.connect() as conn:
                conn.execute(text(ddl))
                conn.commit()
            added.append(f"{table.name}.{column.name}")
    return added
