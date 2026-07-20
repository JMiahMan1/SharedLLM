"""
Comprehensive tests for RAG search across BOTH vector backends.

The production path uses sqlite-vec (``SqliteVecAdapter``). The local/dev
environment may lack the native extension, in which case the tests fall back to
the pure-Python ``NumpyVecAdapter``. Either way the same API logic and scoping
rules are exercised, and the real vec0 path is tested whenever sqlite-vec is
importable (CI / container).

These tests pin the bug where collection scoping dropped in-collection hits
(semantic search returned 0 for queries that clearly matched a lesson) and
verify reindex + default-user fallback.
"""
import asyncio
import hashlib
import os
import re

os.environ.setdefault("INTERNAL_SECRET", "test-secret")

import numpy as np
import pytest

from services.rag import db
from services.rag import main as rag_main
from services.rag.schemas import IngestRequest, SearchRequest
from services.rag.store import NumpyVecAdapter, SqliteVecAdapter

DIM = 16


def _fake_embed(texts):
    """Deterministic bag-of-words embedding so cosine similarity tracks word
    overlap — lets us assert semantic relevance without loading a real model."""
    out = []
    for t in texts:
        v = np.zeros(DIM, dtype=np.float32)
        for w in re.findall(r"[a-z0-9]+", t.lower()):
            h = int(hashlib.md5(w.encode()).hexdigest(), 16) % DIM
            v[h] += 1.0
        n = np.linalg.norm(v) or 1.0
        out.append((v / n).tolist())
    return out


def _install(tmp_path, adapter_cls):
    conn = db.get_db_connection(str(tmp_path / "rag.db"))
    db.init_schema(conn, DIM)
    adapter = adapter_cls(conn)
    rag_main.conn = conn
    rag_main.adapter = adapter
    rag_main.embedder = object()  # satisfied; we override embed below
    rag_main.embed = _fake_embed
    rag_main.EMBEDDING_DIM = DIM
    return conn


async def _ingest(user_id, content, collection):
    return await rag_main.ingest(
        IngestRequest(user_id=user_id, content=content, collection_name=collection)
    )


async def _search(query, user_id, collection, k=5):
    return (await rag_main.search(
        SearchRequest(query=query, user_id=user_id, k=k, collection_name=collection)
    )).results


# ───────────────────────────── API logic (both backends) ─────────────────────

@pytest.mark.parametrize("adapter_cls", [NumpyVecAdapter, SqliteVecAdapter])
def test_ingest_then_vector_search_returns_doc(tmp_path, adapter_cls, monkeypatch):
    pytest.importorskip("sqlite_vec") if adapter_cls is SqliteVecAdapter else None
    _install(tmp_path, adapter_cls)
    asyncio.run(_ingest(
        "default",
        "When provisioning a GitHub repository, create it via gh repo create, "
        "configure the origin remote, then push the code with a README and tests.",
        "system_learnings",
    ))
    results = asyncio.run(_search(
        "create a github repository and push the code", "default", "system_learnings"
    ))
    assert len(results) >= 1
    assert "github" in results[0].content.lower()


@pytest.mark.parametrize("adapter_cls", [NumpyVecAdapter, SqliteVecAdapter])
def test_collection_scoping_isolated(tmp_path, adapter_cls, monkeypatch):
    pytest.importorskip("sqlite_vec") if adapter_cls is SqliteVecAdapter else None
    _install(tmp_path, adapter_cls)
    asyncio.run(_ingest("default", "github repo create and push the code", "system_learnings"))
    asyncio.run(_ingest("default", "the weather in spain is sunny today", "notes"))
    # Searching system_learnings must NOT surface the unrelated 'notes' doc
    # (which would be the top hit if collection scoping were broken).
    results = asyncio.run(_search("sunny weather spain", "default", "system_learnings"))
    assert len(results) >= 1
    assert all("weather" not in r.content.lower() for r in results)


@pytest.mark.parametrize("adapter_cls", [NumpyVecAdapter, SqliteVecAdapter])
def test_default_user_lesson_visible_to_other_users(tmp_path, adapter_cls, monkeypatch):
    pytest.importorskip("sqlite_vec") if adapter_cls is SqliteVecAdapter else None
    _install(tmp_path, adapter_cls)
    asyncio.run(_ingest(
        "default",
        "When provisioning a GitHub repository create it via gh repo create and push",
        "system_learnings",
    ))
    # alice has no lessons of her own, but shared ("default") lessons are visible.
    results = asyncio.run(_search("create a github repository and push", "alice", "system_learnings"))
    assert len(results) >= 1
    assert "github" in results[0].content.lower()


@pytest.mark.parametrize("adapter_cls", [NumpyVecAdapter, SqliteVecAdapter])
def test_long_mission_query_returns_github_lesson(tmp_path, adapter_cls, monkeypatch):
    """The exact regression: a long, specific mission query must surface the
    github-push lesson (semantic search used to return 0 for it)."""
    pytest.importorskip("sqlite_vec") if adapter_cls is SqliteVecAdapter else None
    _install(tmp_path, adapter_cls)
    asyncio.run(_ingest(
        "default",
        "When provisioning a GitHub repository from a Raven workspace, explicitly "
        "create the repo via gh repo create, configure the origin remote, rename "
        "the local branch to main, and write the README before pushing.",
        "system_learnings",
    ))
    asyncio.run(_ingest(
        "default",
        "Use ruff check and pytest to validate python code before committing.",
        "system_learnings",
    ))
    long_query = (
        "Build a small, well-tested Python library called pycfgkit that reads a "
        "hierarchical TOML/INI config with overrides and exports flattened env "
        "vars, then create a GitHub repository for it and push the code with a "
        "README and tests."
    )
    results = asyncio.run(_search(long_query, "default", "system_learnings"))
    assert len(results) >= 1
    assert "github" in results[0].content.lower()


@pytest.mark.parametrize("adapter_cls", [NumpyVecAdapter, SqliteVecAdapter])
def test_bm25_keyword_search_works(tmp_path, adapter_cls, monkeypatch):
    pytest.importorskip("sqlite_vec") if adapter_cls is SqliteVecAdapter else None
    _install(tmp_path, adapter_cls)
    asyncio.run(_ingest("default", "Provision a GitHub repository with gh repo create and push", "system_learnings"))
    results = asyncio.run(_search("gh repo create push github", "default", "system_learnings"))
    assert len(results) >= 1


# ───────────────────────────── reindex (stale-vector recovery) ───────────────

@pytest.mark.parametrize("adapter_cls", [NumpyVecAdapter, SqliteVecAdapter])
def test_reindex_rebuilds_vector_store(tmp_path, adapter_cls, monkeypatch):
    pytest.importorskip("sqlite_vec") if adapter_cls is SqliteVecAdapter else None
    conn = _install(tmp_path, adapter_cls)
    asyncio.run(_ingest("default", "create a GitHub repository and push the code", "system_learnings"))
    assert rag_main.adapter.count() == 1
    # Simulate stale/missing vectors: wipe the vector store.
    if adapter_cls is SqliteVecAdapter:
        conn.execute("DELETE FROM vec_rag_items")
    else:
        conn.execute("DELETE FROM vec_store")
    conn.commit()
    assert rag_main.adapter.count() == 0
    # Reindex must rebuild vectors from rag_items and restore search.
    n = rag_main.reindex_all()
    assert n == 1
    assert rag_main.adapter.count() == 1
    results = asyncio.run(_search("github repository push", "default", "system_learnings"))
    assert len(results) >= 1


def test_reindex_is_idempotent_and_fast(tmp_path, monkeypatch):
    pytest.importorskip("sqlite_vec")
    _install(tmp_path, SqliteVecAdapter)
    for i in range(20):
        asyncio.run(_ingest("default", f"document number {i} about github push repo", "system_learnings"))
    n1 = rag_main.reindex_all()
    n2 = rag_main.reindex_all()
    assert n1 == 20 and n2 == 20
    assert rag_main.adapter.count() == 20


# ───────────────────── legacy schema migration (production path) ──────────────

def test_legacy_vec_table_is_migrated_and_reindexed(tmp_path):
    """The exact production scenario: a DB whose vec_rag_items was created with
    the OLD ``vec0(id, embedding)`` schema (no metadata columns) must be migrated
    to the metadata-column schema on init WITHOUT re-embedding (existing vectors
    are preserved), and scoped search must work afterwards."""
    pytest.importorskip("sqlite_vec")
    import struct

    db_path = str(tmp_path / "legacy.db")
    conn = db.get_db_connection(db_path)

    # Build the LEGACY table shape and seed rag_items (the source of truth).
    conn.execute("CREATE TABLE IF NOT EXISTS rag_items ("
                 "id TEXT PRIMARY KEY, collection_name TEXT NOT NULL, user_id TEXT NOT NULL, "
                 "content TEXT NOT NULL, metadata TEXT NOT NULL, created_at INTEGER NOT NULL, "
                 "indexed_at TEXT NOT NULL, usage_count INTEGER NOT NULL DEFAULT 0, last_used_at TEXT)")
    conn.execute(f"CREATE VIRTUAL TABLE vec_rag_items USING vec0(id TEXT PRIMARY KEY, embedding float[{DIM}])")
    content = "create a github repository and push the code"
    conn.execute("INSERT INTO rag_items(id,collection_name,user_id,content,metadata,created_at,indexed_at) "
                 "VALUES('x','system_learnings','default',?,'{}',0,'')", [content])
    # Seed the legacy vector for doc 'x' with the SAME embedding the model would
    # produce, so we can prove it was preserved (not regenerated).
    known_vec = _fake_embed([content])[0]
    conn.execute("INSERT INTO vec_rag_items(id,embedding) VALUES('x', ?)",
                 [struct.pack(f"<{DIM}f", *known_vec)])
    conn.commit()
    legacy_cols = {r[1] for r in conn.execute("PRAGMA table_info(vec_rag_items)").fetchall()}
    assert "collection_name" not in legacy_cols  # confirm we start on the legacy shape

    # init_schema must migrate the legacy vec table to the metadata-column shape
    # AND preserve the existing vector (copy, no re-embed).
    db.init_schema(conn, DIM)
    new_cols = {r[1] for r in conn.execute("PRAGMA table_info(vec_rag_items)").fetchall()}
    assert "collection_name" in new_cols and "user_id" in new_cols
    # Vector was PRESERVED during migration — no reindex required.
    assert conn.execute("SELECT COUNT(*) FROM vec_rag_items").fetchone()[0] == 1

    # Wire the app globals; scoped search must work against the preserved vector.
    rag_main.conn = conn
    rag_main.adapter = SqliteVecAdapter(conn)
    rag_main.embedder = object()
    rag_main.embed = _fake_embed
    rag_main.EMBEDDING_DIM = DIM
    results = asyncio.run(_search("github repository push code", "default", "system_learnings"))
    assert len(results) >= 1
    assert "github" in results[0].content.lower()


def test_migration_drops_orphan_vectors(tmp_path):
    """Legacy vectors with no matching rag_items row are orphans and must be
    dropped during migration (they cannot be scoped without metadata)."""
    pytest.importorskip("sqlite_vec")
    import struct

    conn = db.get_db_connection(str(tmp_path / "orphan.db"))
    conn.execute("CREATE TABLE IF NOT EXISTS rag_items ("
                 "id TEXT PRIMARY KEY, collection_name TEXT NOT NULL, user_id TEXT NOT NULL, "
                 "content TEXT NOT NULL, metadata TEXT NOT NULL, created_at INTEGER NOT NULL, "
                 "indexed_at TEXT NOT NULL, usage_count INTEGER NOT NULL DEFAULT 0, last_used_at TEXT)")
    conn.execute(f"CREATE VIRTUAL TABLE vec_rag_items USING vec0(id TEXT PRIMARY KEY, embedding float[{DIM}])")
    conn.execute("INSERT INTO rag_items(id,collection_name,user_id,content,metadata,created_at,indexed_at) "
                 "VALUES('keep','system_learnings','default','a github push lesson','{}',0,'')")
    conn.execute("INSERT INTO vec_rag_items(id,embedding) VALUES('keep', ?)",
                 [struct.pack(f"<{DIM}f", *_fake_embed(["a github push lesson"])[0])])
    conn.execute("INSERT INTO vec_rag_items(id,embedding) VALUES('orphan', ?)",
                 [struct.pack(f"<{DIM}f", *_fake_embed(["orphan"])[0])])
    conn.commit()

    db.init_schema(conn, DIM)
    ids = {r[0] for r in conn.execute("SELECT id FROM vec_rag_items")}
    assert ids == {"keep"}  # orphan dropped, real vector preserved
