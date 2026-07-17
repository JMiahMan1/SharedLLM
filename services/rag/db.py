# services/rag/db.py
"""SQLite connection management and schema initialization for the RAG service.

Replaces ChromaDB with a native SQLite database backed by the ``sqlite-vec``
extension (with a pure-Python/numpy fallback handled in ``store.py``).

The embedding dimension is detected dynamically at startup from the active
``EMBEDDING_MODEL``. The migration blueprint hardcodes ``float[384]``, but the
deployed model (``nomic-ai/nomic-embed-text-v1.5``) produces **768-dimensional**
vectors. Declaring the ``vec0`` virtual table with the wrong dimension would
corrupt every insert, so we never trust a hardcoded value — we probe the model.
"""
import logging
import os
import sqlite3

# Where the RAG database lives. ChromaDB used CHROMA_PERSIST_DIR=/data/chroma_db;
# the new sqlite database lives alongside it at /data/rag.db.
RAG_DB_PATH = os.getenv("RAG_DB_PATH", "/data/rag.db").strip() or "/data/rag.db"

# The relational collections are stored in the generic `rag_items` table.
GENERIC_COLLECTIONS = {
    "nextcloud_files",
    "ha_entities",
    "system_capabilities",
    "system_learnings",
    "user_facts",
    "intercom_facts",
    "telemetry_alerts",
}

# Collections that also have a dedicated relational table (Section 6 of the
# migration blueprint). These are mirrored into `rag_items` for unified vector
# search while keeping structured columns available for relational queries.
STRUCTURED_COLLECTIONS = {
    "mission_history",
    "conversation_memory",
    "network_topology",
}


def get_db_connection(db_path: str = RAG_DB_PATH) -> sqlite3.Connection:
    """Open a SQLite connection with WAL mode and the sqlite-vec extension.

    If the ``sqlite_vec`` extension cannot be loaded (e.g. Python's ``sqlite3``
    was built without extension support), the connection is still returned — the
    store layer will fall back to the numpy adapter which does not need it.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    # 1. WAL mode: allow concurrent reads while writing.
    conn.execute("PRAGMA journal_mode=WAL;")
    # 2. Wait up to 10s instead of raising "database is locked".
    conn.execute("PRAGMA busy_timeout=10000;")
    conn.execute("PRAGMA foreign_keys=ON;")

    # 3. Load sqlite-vec extension (best effort).
    try:
        conn.enable_load_extension(True)
        import sqlite_vec  # pyright: ignore[reportMissingImports]

        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
    except Exception as e:  # pragma: no cover - environment dependent
        conn.enable_load_extension(False)
        # The store layer decides whether to fall back to numpy.
        import logging

        logging.getLogger("rag").warning(f"sqlite-vec extension unavailable: {e}")

    return conn


def detect_dimension(embed_fn, fallback: int = 768) -> int:
    """Detect the embedding dimension by probing the active model.

    Never trusts a hardcoded value. Falls back to ``fallback`` (the known
    dimension for the deployed nomic model) only if probing fails.
    """
    try:
        probe = list(embed_fn(["__dimension_probe__"]))
        if probe and len(probe[0]) > 0:
            return len(probe[0])
    except Exception as e:  # pragma: no cover - environment dependent
        import logging

        logging.getLogger("rag").warning(f"Embedding dimension probe failed: {e}")
    return fallback


def init_schema(conn: sqlite3.Connection, dim: int) -> None:
    """Create all tables. ``dim`` is the (dynamically detected) embedding size."""
    cur = conn.cursor()

    # Generic metadata table — holds every collection's items.
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS rag_items (
            id TEXT PRIMARY KEY,
            collection_name TEXT NOT NULL,
            user_id TEXT NOT NULL,
            content TEXT NOT NULL,
            metadata TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            indexed_at TEXT NOT NULL,
            usage_count INTEGER NOT NULL DEFAULT 0,
            last_used_at TEXT
        )
        """
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_rag_items_lookup "
        "ON rag_items(collection_name, user_id)"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_rag_items_usage "
        "ON rag_items(collection_name, usage_count DESC)"
    )

    # ── Incremental migration: older DBs lack the usage-tracking columns. ──
    _migrate_rag_items_usage_columns(cur)

    # Vector table. Dimension is dynamic, never hardcoded. Created only when the
    # sqlite-vec extension is available; otherwise the numpy fallback adapter
    # (which uses the regular `vec_store` table) is used and this is skipped.
    try:
        cur.execute(
            f"""
            CREATE VIRTUAL TABLE IF NOT EXISTS vec_rag_items USING vec0(
                id TEXT PRIMARY KEY,
                embedding float[{dim}]
            )
            """
        )
    except Exception as e:  # pragma: no cover - environment dependent
        logging.getLogger("rag").warning(
            f"vec0 extension unavailable; skipping vec_rag_items (using numpy fallback): {e}"
        )

    # BM25 keyword search via FTS5 (best effort; skipped if unavailable).
    try:
        cur.execute(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS rag_fts USING fts5(
                id UNINDEXED,
                content,
                collection_name UNINDEXED,
                user_id UNINDEXED
            )
            """
        )
    except Exception as e:  # pragma: no cover - environment dependent
        logging.getLogger("rag").warning(f"FTS5 unavailable, BM25 disabled: {e}")

    # numpy fallback vector store (used when sqlite-vec is missing).
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS vec_store (
            id TEXT PRIMARY KEY,
            collection_name TEXT NOT NULL,
            user_id TEXT NOT NULL,
            embedding BLOB NOT NULL
        )
        """
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_vec_store_lookup "
        "ON vec_store(collection_name, user_id)"
    )

    # ── Section 6: dedicated relational tables ─────────────────────────────
    # NOTE: Vectors for *all* collections (including the structured ones below)
    # live in the unified `vec_rag_items` table keyed by the same id. We do not
    # create per-collection vec0 tables — that would duplicate the embedding
    # dimension config and risk mismatch. The dedicated tables below hold the
    # structured relational columns; similarity search always goes through
    # `rag_items` + `vec_rag_items` filtered by `collection_name`.

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS mission_history (
            mission_id TEXT PRIMARY KEY,
            task_description TEXT NOT NULL,
            final_status TEXT NOT NULL,
            error_summary TEXT,
            steps_json TEXT NOT NULL,
            content TEXT NOT NULL,
            user_id TEXT NOT NULL DEFAULT 'default',
            created_at INTEGER NOT NULL
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS conversation_memory (
            utterance_id TEXT PRIMARY KEY,
            speaker TEXT NOT NULL,
            text_content TEXT NOT NULL,
            room_id TEXT NOT NULL,
            content TEXT NOT NULL,
            user_id TEXT NOT NULL DEFAULT 'default',
            timestamp INTEGER NOT NULL
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS network_topology (
            container_name TEXT PRIMARY KEY,
            ip_address TEXT NOT NULL,
            exposed_ports TEXT NOT NULL,
            discovered_services TEXT NOT NULL,
            network_name TEXT NOT NULL,
            content TEXT NOT NULL,
            user_id TEXT NOT NULL DEFAULT 'default'
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS telemetry_alerts (
            alert_id TEXT PRIMARY KEY,
            entity_id TEXT NOT NULL,
            alert_type TEXT NOT NULL,
            severity TEXT NOT NULL,
            content TEXT NOT NULL,
            user_id TEXT NOT NULL DEFAULT 'default',
            created_at INTEGER NOT NULL
        )
        """
    )

    # Persist the detected dimension so restarts stay consistent.
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS rag_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )
    cur.execute(
        "INSERT OR REPLACE INTO rag_meta(key, value) VALUES('embedding_dim', ?)",
        [str(dim)],
    )

    conn.commit()


def get_stored_dimension(conn: sqlite3.Connection) -> int | None:
    try:
        row = conn.execute(
            "SELECT value FROM rag_meta WHERE key='embedding_dim'"
        ).fetchone()
        return int(row["value"]) if row else None
    except Exception:
        return None


def _migrate_rag_items_usage_columns(cur: sqlite3.Cursor) -> None:
    """Add usage-tracking columns to ``rag_items`` without recreating the table.

    Older databases were created before reuse tracking existed. ``CREATE TABLE``
    only runs on first creation, so we must add the columns explicitly here.
    """
    existing = {row[1] for row in cur.execute("PRAGMA table_info(rag_items)").fetchall()}
    if "usage_count" not in existing:
        cur.execute(
            "ALTER TABLE rag_items ADD COLUMN usage_count INTEGER NOT NULL DEFAULT 0"
        )
    if "last_used_at" not in existing:
        cur.execute("ALTER TABLE rag_items ADD COLUMN last_used_at TEXT")
