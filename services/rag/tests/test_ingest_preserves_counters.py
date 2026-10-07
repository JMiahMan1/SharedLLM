"""A re-ingest refreshes content but never erases a row's history counters.

``_add_item`` used to write ``usage_count=0, last_used_at=NULL,
applied_count=0`` as literals inside ``INSERT OR REPLACE``, so every replace
zeroed the counters the learning loop scores against: re-learning the same
rule wiped the ``applied_count`` that ``Apply: [id]`` citations had earned,
and the HA sync (which re-adds every entity every five minutes) reset every
entity's ``usage_count`` on each run. Content, metadata and the lesson
columns must still update -- only the counters and ``created_at`` survive.
"""

from services.rag import db
from services.rag import main as rag_main
from services.rag.store import NumpyVecAdapter

DIM = 16
NOW = "2026-10-07T00:00:00Z"


def _fake_embed(texts):
    return [[0.0] * DIM for _ in texts]


def _install(tmp_path):
    conn = db.get_db_connection(str(tmp_path / "rag.db"))
    db.init_schema(conn, DIM)
    rag_main.conn = conn
    rag_main.adapter = NumpyVecAdapter(conn)
    rag_main.embedder = object()
    rag_main.embed = _fake_embed
    rag_main.EMBEDDING_DIM = DIM
    return conn


def _ingest(conn, *, doc_id="lesson-abc", content="first rule text", metadata=None, created_at=1000):
    rag_main._add_item(
        "system_learnings", doc_id, "default", content,
        metadata if metadata is not None else {"rule": "when x do y"},
        created_at, NOW,
    )
    return conn.execute("SELECT * FROM rag_items WHERE id = ?", [doc_id]).fetchone()


def test_a_fresh_row_starts_at_zero(tmp_path):
    conn = _install(tmp_path)
    row = _ingest(conn)
    assert row["usage_count"] == 0
    assert row["applied_count"] == 0
    assert row["last_used_at"] is None
    assert row["created_at"] == 1000


def test_a_reingest_keeps_usage_applied_and_created_at(tmp_path):
    conn = _install(tmp_path)
    _ingest(conn)
    conn.execute(
        "UPDATE rag_items SET usage_count = 7, last_used_at = 111,"
        " applied_count = 3 WHERE id = 'lesson-abc'"
    )
    conn.commit()
    before = conn.execute(
        "SELECT created_at, usage_count, last_used_at, applied_count"
        " FROM rag_items WHERE id = 'lesson-abc'"
    ).fetchone()
    row = _ingest(conn, content="second rule text", metadata={"rule": "updated rule"})
    assert row["usage_count"] == before["usage_count"] == 7
    assert row["last_used_at"] == before["last_used_at"]
    assert row["applied_count"] == before["applied_count"] == 3
    assert row["created_at"] == before["created_at"] == 1000


def test_the_refreshed_content_and_lesson_columns_still_update(tmp_path):
    conn = _install(tmp_path)
    _ingest(conn)
    row = _ingest(conn, content="second rule text", metadata={"rule": "updated rule", "outcome": "failure"})
    assert row["content"] == "second rule text"
    assert row["rule"] == "updated rule"
    assert row["outcome"] == "failure"


def test_metadata_usage_count_stays_in_step_with_the_column(tmp_path):
    conn = _install(tmp_path)
    _ingest(conn)
    conn.execute(
        "UPDATE rag_items SET usage_count = 7 WHERE id = 'lesson-abc'"
    )
    conn.commit()
    _ingest(conn)
    row = conn.execute(
        "SELECT metadata FROM rag_items WHERE id = 'lesson-abc'"
    ).fetchone()
    import json
    assert json.loads(row["metadata"])["usage_count"] == 7


def test_counters_survive_back_to_back_reingests(tmp_path):
    conn = _install(tmp_path)
    _ingest(conn)
    conn.execute(
        "UPDATE rag_items SET usage_count = 4, applied_count = 2 WHERE id = 'lesson-abc'"
    )
    conn.commit()
    _ingest(conn, content="second")
    row = _ingest(conn, content="third")
    assert row["usage_count"] == 4
    assert row["applied_count"] == 2


def test_a_new_id_gets_zero_counters(tmp_path):
    conn = _install(tmp_path)
    _ingest(conn, doc_id="lesson-old")
    row = _ingest(conn, doc_id="lesson-new")
    assert row["usage_count"] == 0
    assert row["applied_count"] == 0
    assert row["last_used_at"] is None
    assert row["created_at"] == 1000
