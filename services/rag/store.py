# services/rag/store.py
"""Vector store adapters for the RAG service.

Two implementations share one interface:

* ``SqliteVecAdapter`` — uses the ``sqlite-vec`` ``vec0`` virtual table for
  fast, disk-backed approximate/exact nearest-neighbour search.
* ``NumpyVecAdapter`` — a pure-Python/numpy fallback used when the
  ``sqlite-vec`` extension cannot be loaded. It stores serialized float32
  blobs in a regular table and computes cosine similarity in Python.

All adapters operate on the unified ``vec_rag_items`` table (or ``vec_store``
for the fallback), keyed by the same ``id`` used in ``rag_items``. Filtering by
collection/user is done via a JOIN against ``rag_items``, so the dimension
mismatch problem described in the migration blueprint (hardcoded ``float[384]``)
is avoided entirely — the dimension is supplied at table-creation time by the
caller after dynamic detection.
"""
from __future__ import annotations

import abc
import logging

import numpy as np

log = logging.getLogger("rag")


def serialize_vector(vector: list[float] | np.ndarray) -> bytes:
    """Serialize a float vector to a little-endian float32 blob."""
    arr = np.asarray(vector, dtype=np.float32)
    return arr.astype("<f4").tobytes()


def deserialize_vector(blob: bytes) -> np.ndarray:
    return np.frombuffer(blob, dtype="<f4")


class VectorStoreAdapter(abc.ABC):
    @abc.abstractmethod
    def add(self, doc_id: str, embedding: list[float], collection: str, user_id: str) -> None:
        ...

    @abc.abstractmethod
    def search(
        self, collection: str, user_id: str, query_vector: list[float], k: int
    ) -> list[tuple[str, float]]:
        """Return ``(doc_id, distance)`` pairs sorted by ascending distance."""

    @abc.abstractmethod
    def delete(self, doc_id: str) -> None:
        ...

    @abc.abstractmethod
    def count(self, collection: str | None = None) -> int:
        ...


class SqliteVecAdapter(VectorStoreAdapter):
    """Exact KNN via the ``sqlite-vec`` ``vec0`` extension."""

    def __init__(self, conn):
        self.conn = conn

    def add(self, doc_id: str, embedding: list[float], collection: str, user_id: str) -> None:
        blob = serialize_vector(embedding)
        # vec0 virtual tables do NOT honor INSERT OR REPLACE — upsert explicitly
        # so re-syncing an existing id (e.g. HA entities) updates the vector
        # instead of raising "UNIQUE constraint failed on primary key".
        self.conn.execute("DELETE FROM vec_rag_items WHERE id = ?", [doc_id])
        self.conn.execute(
            "INSERT INTO vec_rag_items(embedding, collection_name, user_id, id) "
            "VALUES(?, ?, ?, ?)",
            [blob, collection, user_id, doc_id],
        )

    def search(
        self, collection: str, user_id: str, query_vector: list[float], k: int
    ) -> list[tuple[str, float]]:
        blob = serialize_vector(query_vector)
        # Documented sqlite-vec KNN-with-metadata pattern: the collection/user
        # metadata live in the vec0 table and are filtered directly in the WHERE
        # alongside `embedding MATCH`, so the KNN is correctly scoped per
        # collection (a global KNN + JOIN used to drop in-collection hits once
        # other collections crowded the top-k, returning 0 for lesson retrieval).
        # `user_id IN (?, 'default')` also surfaces shared ("default") lessons.
        rows = self.conn.execute(
            "SELECT id, distance FROM vec_rag_items "
            "WHERE embedding MATCH ? AND collection_name = ? "
            "AND user_id IN (?, 'default') "
            "ORDER BY distance LIMIT ?",
            [blob, collection, user_id, k],
        ).fetchall()
        return [(r["id"], float(r["distance"])) for r in rows]

    def delete(self, doc_id: str) -> None:
        self.conn.execute("DELETE FROM vec_rag_items WHERE id = ?", [doc_id])

    def count(self, collection: str | None = None) -> int:
        if collection is None:
            row = self.conn.execute("SELECT COUNT(*) AS c FROM vec_rag_items").fetchone()
        else:
            row = self.conn.execute(
                "SELECT COUNT(*) AS c FROM rag_items WHERE collection_name = ?",
                [collection],
            ).fetchone()
        return int(row["c"]) if row else 0


class NumpyVecAdapter(VectorStoreAdapter):
    """Pure-numpy fallback. No native extension required."""

    def __init__(self, conn):
        self.conn = conn

    def add(self, doc_id: str, embedding: list[float], collection: str, user_id: str) -> None:
        blob = serialize_vector(embedding)
        # Upsert: remove any prior row for this id before inserting.
        self.conn.execute("DELETE FROM vec_store WHERE id = ?", [doc_id])
        self.conn.execute(
            "INSERT INTO vec_store(id, collection_name, user_id, embedding) "
            "VALUES(?, ?, ?, ?)",
            [doc_id, collection, user_id, blob],
        )

    def search(
        self, collection: str, user_id: str, query_vector: list[float], k: int
    ) -> list[tuple[str, float]]:
        rows = self.conn.execute(
            "SELECT id, embedding FROM vec_store "
            "WHERE collection_name = ? AND (user_id = ? OR user_id = 'default')",
            [collection, user_id],
        ).fetchall()
        if not rows:
            return []

        q = np.asarray(query_vector, dtype=np.float32)
        q_norm = np.linalg.norm(q) or 1.0
        scored: list[tuple[str, float]] = []
        for r in rows:
            vec = deserialize_vector(r["embedding"])
            v_norm = np.linalg.norm(vec) or 1.0
            # cosine distance = 1 - cosine_similarity
            sim = float(np.dot(q, vec) / (q_norm * v_norm))
            scored.append((r["id"], 1.0 - sim))

        scored.sort(key=lambda x: x[1])
        return scored[:k]

    def delete(self, doc_id: str) -> None:
        self.conn.execute("DELETE FROM vec_store WHERE id = ?", [doc_id])

    def count(self, collection: str | None = None) -> int:
        if collection is None:
            row = self.conn.execute("SELECT COUNT(*) AS c FROM vec_store").fetchone()
        else:
            row = self.conn.execute(
                "SELECT COUNT(*) AS c FROM vec_store WHERE collection_name = ?",
                [collection],
            ).fetchone()
        return int(row["c"]) if row else 0


def build_adapter(conn) -> VectorStoreAdapter:
    """Return a ``SqliteVecAdapter`` if ``sqlite-vec`` works, else numpy fallback."""
    try:
        test = conn.execute("SELECT COUNT(*) FROM vec_rag_items").fetchone()
        if test is not None:
            log.info("Using SqliteVecAdapter (sqlite-vec extension active).")
            return SqliteVecAdapter(conn)
    except Exception as e:  # pragma: no cover - environment dependent
        log.warning(f"sqlite-vec unavailable ({e}); falling back to NumpyVecAdapter.")
    log.warning("Using NumpyVecAdapter fallback (no sqlite-vec extension).")
    return NumpyVecAdapter(conn)
