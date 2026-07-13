"""
Unit tests for the sqlite-vec migration of the RAG service.

sqlite-vec is NOT installed in the local/dev environment, so these tests
exercise the pure-Python ``NumpyVecAdapter`` fallback path (the same code that
runs in production if the native extension fails to load). The API-level tests
monkeypatch the module globals (``conn``, ``adapter``, ``embed``) so they do not
require fastembed or the native extension.
"""
import os
import sqlite3

os.environ.setdefault("INTERNAL_SECRET", "test-secret")

import numpy as np
import pytest

from services.rag import db
from services.rag import main as rag_main
from services.rag.schemas import IngestRequest, SearchRequest
from services.rag.store import (
    NumpyVecAdapter,
    build_adapter,
    deserialize_vector,
    serialize_vector,
)

DIM = 8


# ──────────────────────────────── store / adapter ───────────────────────────

def test_serialize_roundtrip():
    v = [0.1, -0.2, 0.3, 0.4]
    blob = serialize_vector(v)
    assert isinstance(blob, bytes)
    assert np.allclose(deserialize_vector(blob), np.array(v, dtype=np.float32))


def _memory_store_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE vec_store(id TEXT PRIMARY KEY, collection_name TEXT NOT NULL, "
        "user_id TEXT NOT NULL, embedding BLOB NOT NULL)"
    )
    return conn


def test_numpy_adapter_add_and_search():
    conn = _memory_store_conn()
    adapter = NumpyVecAdapter(conn)
    adapter.add("a", [1.0, 0.0, 0.0], "col", "default")
    adapter.add("b", [0.0, 1.0, 0.0], "col", "default")
    adapter.add("c", [0.0, 0.0, 1.0], "col", "default")

    res = adapter.search("col", "default", [0.9, 0.1, 0.0], k=2)
    assert res[0][0] == "a"
    assert len(res) == 2
    # cosine distance is symmetric/non-negative
    assert res[0][1] <= res[1][1]


def test_numpy_adapter_user_partition():
    conn = _memory_store_conn()
    adapter = NumpyVecAdapter(conn)
    adapter.add("a", [1.0, 0.0], "col", "alice")
    adapter.add("b", [1.0, 0.0], "col", "bob")
    # alice must not see bob's vector
    res = adapter.search("col", "alice", [1.0, 0.0], k=5)
    assert [r[0] for r in res] == ["a"]


def test_numpy_adapter_delete_and_count():
    conn = _memory_store_conn()
    adapter = NumpyVecAdapter(conn)
    adapter.add("a", [1.0, 0.0], "col", "default")
    adapter.add("b", [0.0, 1.0], "col", "default")
    assert adapter.count() == 2
    adapter.delete("a")
    assert adapter.count() == 1


def test_build_adapter_falls_back_to_numpy_without_extension():
    # In this environment sqlite-vec is absent -> build_adapter must return the
    # numpy fallback rather than crash.
    conn = _memory_store_conn()
    adapter = build_adapter(conn)
    assert isinstance(adapter, NumpyVecAdapter)


# ──────────────────────────────── db schema ─────────────────────────────────

def test_init_schema_creates_tables(tmp_path):
    db_path = str(tmp_path / "rag.db")
    conn = db.get_db_connection(db_path)
    # Should not raise even when vec0 / FTS5 are unavailable.
    db.init_schema(conn, DIM)
    tables = {
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table','view')"
        ).fetchall()
    }
    for expected in (
        "rag_items",
        "vec_store",
        "mission_history",
        "conversation_memory",
        "network_topology",
        "telemetry_alerts",
        "rag_meta",
    ):
        assert expected in tables, f"missing table {expected}"
    conn.close()


def test_dimension_detection_uses_model_not_hardcoded():
    # Probe with a fake embedder; must reflect the model output, never 384.
    def fake_embed(texts):
        return [[0.0] * 768 for _ in texts]

    assert db.detect_dimension(fake_embed, fallback=384) == 768

    def fake_embed_bad(texts):
        raise RuntimeError("boom")

    assert db.detect_dimension(fake_embed_bad, fallback=384) == 384


# ──────────────────────────────── API logic ─────────────────────────────────
# These call the endpoint functions directly with monkeypatched globals so we
# never need fastembed or the native extension.


def _install_fakes(tmp_path):
    db_path = str(tmp_path / "rag.db")
    conn = db.get_db_connection(db_path)
    db.init_schema(conn, DIM)
    adapter = NumpyVecAdapter(conn)

    def fake_embed(texts):
        out = []
        for t in texts:
            v = np.zeros(DIM, dtype=np.float32)
            for i, ch in enumerate(t):
                v[i % DIM] += ord(ch)
            out.append(v.tolist())
        return out

    rag_main.conn = conn
    rag_main.adapter = adapter
    rag_main.embedder = object()  # satisfied; we override embed below
    rag_main.embed = fake_embed
    rag_main.EMBEDDING_DIM = DIM
    return conn


@pytest.mark.asyncio
async def test_ingest_and_search(tmp_path):
    _install_fakes(tmp_path)
    ingest_resp = await rag_main.ingest(
        IngestRequest(
            user_id="alice",
            content="The quick brown fox",
            metadata={"source": "test"},
            collection_name="nextcloud_files",
        )
    )
    assert ingest_resp["status"] == "SUCCESS"

    results = (await rag_main.search(
        SearchRequest(query="quick brown fox", user_id="alice", k=3, collection_name="nextcloud_files")
    )).results
    assert len(results) >= 1
    assert "fox" in results[0].content


@pytest.mark.asyncio
async def test_search_user_partition(tmp_path):
    _install_fakes(tmp_path)
    await rag_main.ingest(
        IngestRequest(user_id="alice", content="alice secret note", collection_name="user_facts")
    )
    await rag_main.ingest(
        IngestRequest(user_id="bob", content="bob secret note", collection_name="user_facts")
    )
    alice_results = (await rag_main.search(
        SearchRequest(query="secret note", user_id="alice", k=5, collection_name="user_facts")
    )).results
    assert all(r.metadata.get("user_id") == "alice" for r in alice_results)


@pytest.mark.asyncio
async def test_sync_missions_stores_and_searchable(tmp_path):
    _install_fakes(tmp_path)
    await rag_main.sync_missions(
        {
            "missions": [
                {
                    "mission_id": "m1",
                    "task_description": "Fix vitest import error in CI",
                    "final_status": "SUCCESS",
                    "error_summary": "",
                }
            ]
        }
    )
    # Confirm it landed in the dedicated relational table.
    row = rag_main._conn().execute(
        "SELECT task_description FROM mission_history WHERE mission_id='m1'"
    ).fetchone()
    assert row is not None

    results = (await rag_main.search(
        SearchRequest(query="vitest import failure", user_id="default", k=3, collection_name="mission_history")
    )).results
    assert len(results) >= 1


@pytest.mark.asyncio
async def test_sync_conversations_and_network_and_alerts(tmp_path):
    _install_fakes(tmp_path)
    await rag_main.sync_conversations(
        {"utterances": [{"speaker": "jarvis", "text_content": "remember to turn off kitchen lights", "room_id": "kitchen"}]}
    )
    await rag_main.sync_network(
        {"containers": [{"container_name": "redis", "ip_address": "10.0.0.5", "exposed_ports": ["6379"], "network_name": "bridge"}]}
    )
    await rag_main.sync_telemetry_alerts(
        {"alerts": [{"entity_id": "vacuum.robot", "alert_type": "battery_drain", "severity": "high", "content": "battery dropped 90% in 5 minutes"}]}
    )
    c = rag_main._conn()
    assert c.execute("SELECT COUNT(*) FROM conversation_memory").fetchone()[0] == 1
    assert c.execute("SELECT COUNT(*) FROM network_topology").fetchone()[0] == 1
    assert c.execute("SELECT COUNT(*) FROM telemetry_alerts").fetchone()[0] == 1

    # network topology should be semantically searchable
    net = (await rag_main.search(
        SearchRequest(query="redis port", user_id="default", k=3, collection_name="network_topology")
    )).results
    assert len(net) >= 1
