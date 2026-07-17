# services/rag/main.py
"""
Microservice 4: Context & RAG Service

Vector/semantic search and ingestion. ChromaDB has been replaced with a native
SQLite database backed by the ``sqlite-vec`` extension (with a pure-numpy
fallback). See ``docs/SQLITE_VEC_MIGRATION.md``.

Key design notes:
* The embedding **dimension is detected dynamically** from the active
  ``EMBEDDING_MODEL``. The migration blueprint hardcodes ``float[384]``, but the
  deployed model (``nomic-ai/nomic-embed-text-v1.5``) produces 768-dim vectors,
  so a hardcoded dimension would corrupt every insert. We probe the model at
  startup instead.
* All collections (generic + the Section 6 structured ones) store their vectors
  in a single unified ``vec_rag_items`` table keyed by ``id``, and their
  metadata in ``rag_items``. Structured collections additionally mirror into
  dedicated relational tables for direct SQL queries.
"""
import hashlib
import json
import logging
import os
import time
import traceback
from contextlib import asynccontextmanager, suppress
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
from fastapi.responses import JSONResponse

from services.config import INTERNAL_SECRET
from services.rag import db
from services.rag.schemas import IngestRequest, SearchRequest, SearchResponse, SearchResultItem
from services.rag.store import VectorStoreAdapter, build_adapter
from services.shared.info_endpoint import info_router

log = logging.getLogger("rag")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s")

DEFAULT_EMBEDDING_MODEL = "nomic-ai/nomic-embed-text-v1.5"
DEFAULT_EMBEDDING_DIM = 768

# Globals populated in the lifespan
conn = None
adapter: VectorStoreAdapter | None = None
BACKEND = "unknown"
embedder = None
EMBEDDING_MODEL = DEFAULT_EMBEDDING_MODEL
EMBEDDING_DIM = DEFAULT_EMBEDDING_DIM


def _conn():
    assert conn is not None, "RAG database not initialized"
    return conn


def _adapter():
    assert adapter is not None, "RAG vector adapter not initialized"
    return adapter

ACTIVE_STATES = {"on", "playing", "idle", "standby", "home", "cooling", "heating", "drying", "cleaning"}


def embed(texts: list[str]) -> list[list[float]]:
    """Embed a list of texts into float vectors using fastembed."""
    if embedder is None:
        raise RuntimeError("Embedder not initialized")
    return [v.tolist() for v in embedder.embed(texts)]


@asynccontextmanager
async def lifespan(app: FastAPI):
    global conn, adapter, embedder, EMBEDDING_MODEL, EMBEDDING_DIM, BACKEND
    from services import config as cfg
    from services.config import resolve_runtime_config

    # Resolve runtime config (sets cfg.EMBEDDING_MODEL from Identity).
    await resolve_runtime_config()

    EMBEDDING_MODEL = cfg.EMBEDDING_MODEL or DEFAULT_EMBEDDING_MODEL
    log.info(f"Initializing RAG Service. Embedding model: {EMBEDDING_MODEL}")

    os.makedirs(os.path.dirname(db.RAG_DB_PATH) or ".", exist_ok=True)

    conn = db.get_db_connection()
    embedder = _init_embedder(EMBEDDING_MODEL)

    # Detect the true embedding dimension from the live model.
    EMBEDDING_DIM = db.detect_dimension(embedder.embed, fallback=DEFAULT_EMBEDDING_DIM)
    log.info(f"Detected embedding dimension: {EMBEDDING_DIM}")

    db.init_schema(conn, EMBEDDING_DIM)
    _ensure_dimension_consistent(conn)

    adapter = build_adapter(conn)
    BACKEND = "sqlite-vec" if type(adapter).__name__ == "SqliteVecAdapter" else "numpy"
    log.info("RAG Service Ready.")
    yield
    try:
        _conn().commit()
        _conn().close()
    except Exception:
        pass
    log.info("RAG Service shutting down.")


def _init_embedder(model_name: str):
    from fastembed import TextEmbedding  # pyright: ignore[reportMissingImports]

    return TextEmbedding(model_name=model_name)


def _ensure_dimension_consistent(connection):
    """If the stored dimension differs from the detected one, rebuild vec tables."""
    stored = db.get_stored_dimension(connection)
    if stored is not None and stored != EMBEDDING_DIM:
        log.warning(
            f"Stored embedding dim {stored} != detected {EMBEDDING_DIM}; "
            "rebuilding vec_rag_items (vectors will be re-created on next sync)."
        )
        connection.execute("DROP TABLE IF EXISTS vec_rag_items")
        connection.execute(
            f"CREATE VIRTUAL TABLE vec_rag_items USING vec0("
            f"id TEXT PRIMARY KEY, embedding float[{EMBEDDING_DIM}])"
        )
        connection.commit()


app = FastAPI(title="SharedLLM RAG Service", version="2.0.0", lifespan=lifespan)

app.include_router(info_router)


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    err_msg = f"RAG Error: {type(exc).__name__}: {exc!s}"
    log.error(f"{err_msg}\n{traceback.format_exc()}")
    return JSONResponse(
        status_code=500,
        content={"status": "ERROR", "message": "Internal RAG Error", "detail": str(exc)}
    )


def require_internal(x_internal_secret: str = Header(...)):
    if x_internal_secret != INTERNAL_SECRET:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")


# ─────────────────────────────────────────────────────────────────────────────
# Core storage helpers
# ─────────────────────────────────────────────────────────────────────────────

def _add_item(
    collection: str,
    doc_id: str,
    user_id: str,
    content: str,
    metadata: dict,
    created_at: int,
    indexed_at: str,
):
    """Insert/replace a document into rag_items + vector store + FTS5."""
    user_id = user_id.lower()
    # Carry usage tracking into metadata for backward-visible reuse stats.
    stored_metadata = dict(metadata or {})
    stored_metadata.setdefault("usage_count", 0)
    _conn().execute(
        "INSERT OR REPLACE INTO rag_items"
        "(id, collection_name, user_id, content, metadata, created_at, indexed_at, "
        " usage_count, last_used_at) "
        "VALUES(?, ?, ?, ?, ?, ?, ?, 0, NULL)",
        [
            doc_id,
            collection,
            user_id,
            content,
            json.dumps(stored_metadata),
            created_at,
            indexed_at,
        ],
    )
    vector = embed([content])[0]
    _adapter().add(doc_id, vector, collection, user_id)

    # BM25 keyword index (best effort).
    try:
        _conn().execute("DELETE FROM rag_fts WHERE id = ?", [doc_id])
        _conn().execute(
            "INSERT INTO rag_fts(id, content, collection_name, user_id) VALUES(?, ?, ?, ?)",
            [doc_id, content, collection, user_id],
        )
    except Exception as e:  # pragma: no cover - FTS5 may be disabled
        log.debug(f"FTS5 index skip for {doc_id}: {e}")

    _conn().commit()


def _delete_items(ids: list[str]):
    if not ids:
        return
    placeholders = ",".join("?" * len(ids))
    _conn().execute(f"DELETE FROM rag_items WHERE id IN ({placeholders})", ids)
    _conn().execute(f"DELETE FROM rag_fts WHERE id IN ({placeholders})", ids)
    _conn().execute(f"DELETE FROM vec_store WHERE id IN ({placeholders})", ids)
    # Clean the sqlite-vec virtual table too (numpy fallback uses vec_store above).
    with suppress(Exception):
        _conn().execute(f"DELETE FROM vec_rag_items WHERE id IN ({placeholders})", ids)
    for doc_id in ids:
        with suppress(Exception):
            _adapter().delete(doc_id)
    _conn().commit()


def _bm25_search(collection: str, user_id: str, query: str, k: int) -> list[tuple[str, float]]:
    """Return ``(doc_id, rank)`` ordered best-first, or [] if FTS5 unavailable."""
    try:
        safe = '"' + query.replace('"', " ").strip() + '"'
        rows = _conn().execute(
            "SELECT id, bm25(rag_fts) AS rank FROM rag_fts "
            "WHERE rag_fts MATCH ? AND collection_name = ? "
            "AND (user_id = ? OR user_id = 'default') "
            "ORDER BY rank LIMIT ?",
            [safe, collection, user_id, k],
        ).fetchall()
        return [(r["id"], float(r["rank"])) for r in rows]
    except Exception as e:  # pragma: no cover - FTS5 may be disabled
        log.debug(f"BM25 search unavailable: {e}")
        return []


def _hybrid_search(
    collection: str,
    user_id: str,
    query: str,
    query_vector: list[float],
    k: int,
    alpha: float,
    use_rrf: bool,
) -> list[SearchResultItem]:
    user_id = user_id.lower()

    vec_res = _adapter().search(collection, user_id, query_vector, k * 2)
    bm25_res = _bm25_search(collection, user_id, query, k * 2)

    K_RRF = 60
    vec_scores: dict[str, float] = {}
    for i, (doc_id, _dist) in enumerate(vec_res):
        vec_scores[doc_id] = vec_scores.get(doc_id, 0.0) + 1.0 / (K_RRF + i + 1)
    bm25_scores: dict[str, float] = {}
    for i, (doc_id, _rank) in enumerate(bm25_res):
        bm25_scores[doc_id] = bm25_scores.get(doc_id, 0.0) + 1.0 / (K_RRF + i + 1)

    if not use_rrf and (vec_res or bm25_res):
        # Blend by alpha (dense weight) / (1 - alpha) (BM25 weight).
        fused: dict[str, float] = {}
        for doc_id, s in vec_scores.items():
            fused[doc_id] = fused.get(doc_id, 0.0) + alpha * s
        for doc_id, s in bm25_scores.items():
            fused[doc_id] = fused.get(doc_id, 0.0) + (1.0 - alpha) * s
    else:
        fused = {}
        for doc_id in set(vec_scores) | set(bm25_scores):
            fused[doc_id] = vec_scores.get(doc_id, 0.0) + bm25_scores.get(doc_id, 0.0)

    ranked = sorted(fused.items(), key=lambda x: x[1], reverse=True)[:k]

    results: list[SearchResultItem] = []
    for doc_id, score in ranked:
        row = _conn().execute(
            "SELECT content, metadata, usage_count, last_used_at FROM rag_items WHERE id = ?",
            [doc_id],
        ).fetchone()
        if row:
            meta = json.loads(row["metadata"])
            # Surface usage tracking in the result metadata so callers (and the
            # UI) can see how often a lesson has been reused.
            meta["usage_count"] = row["usage_count"] or 0
            meta["last_used_at"] = row["last_used_at"]
            results.append(
                SearchResultItem(
                    content=row["content"],
                    metadata=meta,
                    score=score,
                )
            )
    # Bump reuse counters for system_learnings hits — this is exactly when a
    # Raven lesson was actually applied during a mission.
    if collection == "system_learnings" and results:
        _bump_usage([doc_id for doc_id, _ in ranked if _item_exists(doc_id)])
    return results


def _item_exists(doc_id: str) -> bool:
    row = _conn().execute("SELECT 1 FROM rag_items WHERE id = ?", [doc_id]).fetchone()
    return row is not None


def _bump_usage(doc_ids: list[str]) -> None:
    """Atomically increment ``usage_count`` and stamp ``last_used_at``."""
    if not doc_ids:
        return
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    try:
        _conn().execute("BEGIN")
        for doc_id in doc_ids:
            _conn().execute(
                "UPDATE rag_items SET usage_count = usage_count + 1, "
                "last_used_at = ? WHERE id = ?",
                [now, doc_id],
            )
        _conn().commit()
    except Exception as e:  # pragma: no cover - best effort counters
        log.debug(f"Usage bump failed: {e}")
        with suppress(Exception):
            _conn().rollback()


def _search_all_collections(
    user_id: str, query: str, k: int, alpha: float, use_rrf: bool
) -> list[SearchResultItem]:
    """Search every collection the user (or the shared ``default`` user) owns.

    The embedding is computed once and reused across collections. Per-collection
    hybrid results are merged, de-duplicated by content, and re-ranked globally
    by fused score so a global "search everything" query returns the best hits
    regardless of which collection they live in.
    """
    user_id = user_id.lower()
    query_vector = embed([query])[0]

    rows = _conn().execute(
        "SELECT DISTINCT collection_name FROM rag_items "
        "WHERE user_id = ? OR user_id = 'default'",
        [user_id],
    ).fetchall()
    collections = [r["collection_name"] for r in rows]

    merged: list[SearchResultItem] = []
    seen_contents: set[str] = set()
    for coll in collections:
        try:
            res = _hybrid_search(coll, user_id, query, query_vector, k, alpha, use_rrf)
        except Exception as e:  # pragma: no cover - isolated per collection
            log.debug(f"Search failed for collection {coll}: {e}")
            res = []
        for item in res:
            if item.content in seen_contents:
                continue
            seen_contents.add(item.content)
            merged.append(item)

    merged.sort(key=lambda x: (x.score or 0.0), reverse=True)
    return merged[:k]


# ─────────────────────────────────────────────────────────────────────────────
# Search / Ingest endpoints (legacy contract preserved)
# ─────────────────────────────────────────────────────────────────────────────

@app.post("/rag/search", response_model=SearchResponse, dependencies=[Depends(require_internal)])
async def search(req: SearchRequest):
    # `collection_name` of "all"/""/None searches across every collection the
    # user (or shared `default`) owns, instead of a single hardcoded one.
    try:
        if req.collection_name in (None, "", "all", "__all__"):
            results = _search_all_collections(
                req.user_id, req.query, req.k, req.alpha, req.use_rrf
            )
        else:
            results = _hybrid_search(
                req.collection_name, req.user_id, req.query,
                embed([req.query])[0], req.k, req.alpha, req.use_rrf
            )
        return SearchResponse(results=results)
    except Exception as e:
        log.error(f"Hybrid search failed: {e}")
        return SearchResponse(results=[])


@app.post("/rag/ingest", dependencies=[Depends(require_internal)])
async def ingest(req: IngestRequest):
    import uuid

    doc_id = str(uuid.uuid4())
    meta = dict(req.metadata)
    meta["user_id"] = req.user_id.lower()
    meta["indexed_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    now = int(time.time())
    try:
        _add_item(req.collection_name, doc_id, req.user_id, req.content, meta, now, meta["indexed_at"])
        return {"status": "SUCCESS", "id": doc_id}
    except Exception as e:
        log.error(f"Ingest failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to ingest document") from e


@app.get("/rag/stats", dependencies=[Depends(require_internal)])
async def get_stats(user_id: str = "default"):
    """Return counts and metadata for collections in the format expected by the UI."""
    user_id = user_id.lower()
    log.info(f"Fetching stats for user_id: {user_id}")
    try:
        collections = ["nextcloud_files", "ha_entities", "system_capabilities", "system_learnings"]
        total_chunks = 0
        coll_chunks_map: dict[str, int] = {}
        coll_docs_map: dict[str, int] = {}
        total_documents = 0
        providers: list[str] = []
        last_indexed = None

        for name in collections:
            target_user = "default" if name == "system_capabilities" else user_id
            rows = _conn().execute(
                "SELECT metadata FROM rag_items WHERE collection_name = ? AND user_id = ?",
                [name, target_user],
            ).fetchall()
            if rows:
                total_chunks += len(rows)
                coll_chunks_map[name] = len(rows)
                providers.append(name.split("_")[0])
                unique_items = set()
                for r in rows:
                    m = json.loads(r["metadata"])
                    item_id = m.get("path") or m.get("friendly_name") or m.get("entity_id")
                    if item_id:
                        unique_items.add(item_id)
                    idx_at = m.get("indexed_at")
                    if idx_at and (last_indexed is None or idx_at > last_indexed):
                        last_indexed = idx_at
                doc_count = len(unique_items) or 1
                total_documents += doc_count
                coll_docs_map[name] = doc_count

        return {
            "status": "SUCCESS",
            "total_chunks": total_chunks,
            "total_documents": total_documents,
            "last_indexed": last_indexed,
            "providers": list(set(providers)),
            "breakdown": {
                name: {
                    "chunks": coll_chunks_map.get(name, 0),
                    "documents": coll_docs_map.get(name, 0),
                }
                for name in collections
            },
        }
    except Exception as e:
        log.error(f"Stats failed: {e}")
        return JSONResponse(status_code=500, content={"status": "ERROR", "message": str(e)})


@app.get("/rag/collection/{collection_name}", dependencies=[Depends(require_internal)])
async def list_collection_documents(collection_name: str, user_id: str = "default", limit: int = 100):
    try:
        target_user = "default" if collection_name == "system_capabilities" else user_id
        rows = _conn().execute(
            "SELECT id, content, metadata FROM rag_items "
            "WHERE collection_name = ? AND user_id = ? LIMIT ?",
            [collection_name, target_user, limit],
        ).fetchall()
        items = [
            {"id": r["id"], "document": r["content"], "metadata": json.loads(r["metadata"])}
            for r in rows
        ]
        return {
            "status": "SUCCESS",
            "collection": collection_name,
            "user_id": target_user,
            "count": len(items),
            "items": items,
        }
    except Exception as e:
        log.error(f"Failed to list collection {collection_name}: {e}")
        return {"status": "ERROR", "message": str(e)}


@app.get("/rag/learning", dependencies=[Depends(require_internal)])
async def list_learnings(user_id: str = "default", limit: int = 200, sort: str = "recent"):
    """List Raven lessons (system_learnings) with reuse stats.

    ``sort`` may be ``recent`` (newest first) or ``reuse`` (most-reused first).
    """
    try:
        target_user = "default"
        order = "usage_count DESC, created_at DESC" if sort == "reuse" else "created_at DESC"
        rows = _conn().execute(
            f"SELECT id, content, metadata, created_at, usage_count, last_used_at "
            f"FROM rag_items WHERE collection_name = ? AND user_id = ? "
            f"ORDER BY {order} LIMIT ?",
            ["system_learnings", target_user, limit],
        ).fetchall()
        items = [
            {
                "id": r["id"],
                "content": r["content"],
                "metadata": json.loads(r["metadata"]),
                "created_at": r["created_at"],
                "usage_count": r["usage_count"] or 0,
                "last_used_at": r["last_used_at"],
            }
            for r in rows
        ]
        return {
            "status": "SUCCESS",
            "collection": "system_learnings",
            "user_id": target_user,
            "count": len(items),
            "items": items,
        }
    except Exception as e:
        log.error(f"Failed to list learnings: {e}")
        return JSONResponse(status_code=500, content={"status": "ERROR", "message": str(e)})


@app.patch("/rag/learning/{doc_id}", dependencies=[Depends(require_internal)])
async def edit_learning(doc_id: str, payload: dict):
    """Edit a Raven lesson's content and/or metadata."""
    try:
        row = _conn().execute(
            "SELECT content, metadata FROM rag_items WHERE id = ?", [doc_id]
        ).fetchone()
        if not row:
            return JSONResponse(status_code=404, content={"status": "ERROR", "message": "Learning not found"})
        meta = json.loads(row["metadata"])
        new_content = payload.get("content", row["content"])
        if payload.get("metadata") is not None:
            meta.update(payload["metadata"])
        if new_content != row["content"]:
            meta["indexed_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        _conn().execute(
            "UPDATE rag_items SET content = ?, metadata = ? WHERE id = ?",
            [new_content, json.dumps(meta), doc_id],
        )
        # Re-embed on content change so future retrieval stays accurate.
        if new_content != row["content"]:
            try:
                vector = embed([new_content])[0]
                _adapter().add(doc_id, vector, "system_learnings", meta.get("user_id", "default"))
                _conn().execute("DELETE FROM rag_fts WHERE id = ?", [doc_id])
                _conn().execute(
                    "INSERT INTO rag_fts(id, content, collection_name, user_id) VALUES(?, ?, ?, ?)",
                    [doc_id, new_content, "system_learnings", meta.get("user_id", "default").lower()],
                )
            except Exception as e:  # pragma: no cover - embedding best effort
                log.warning(f"Re-embed on edit failed for {doc_id}: {e}")
        _conn().commit()
        return {"status": "SUCCESS", "id": doc_id}
    except Exception as e:
        log.error(f"Failed to edit learning {doc_id}: {e}")
        return JSONResponse(status_code=500, content={"status": "ERROR", "message": str(e)})


@app.delete("/rag/learning/{doc_id}", dependencies=[Depends(require_internal)])
async def delete_learning(doc_id: str):
    """Delete a single Raven lesson."""
    try:
        _delete_items([doc_id])
        return {"status": "SUCCESS", "id": doc_id}
    except Exception as e:
        log.error(f"Failed to delete learning {doc_id}: {e}")
        return JSONResponse(status_code=500, content={"status": "ERROR", "message": str(e)})


@app.get("/rag/indexed-paths", dependencies=[Depends(require_internal)])
async def get_indexed_paths(user_id: str = "default"):
    user_id = user_id.lower()
    try:
        rows = _conn().execute(
            "SELECT metadata FROM rag_items WHERE collection_name = 'nextcloud_files' AND user_id = ?",
            [user_id],
        ).fetchall()
        paths = {json.loads(r["metadata"]).get("path") for r in rows if r["metadata"]}
        paths.discard(None)
        return {"status": "SUCCESS", "paths": list(paths)}
    except Exception as e:
        log.error(f"Failed to fetch indexed paths: {e}")
        return JSONResponse(status_code=500, content={"status": "ERROR", "message": str(e)})


@app.post("/rag/sync/files", dependencies=[Depends(require_internal)])
async def sync_files(payload: dict):
    chunks = payload.get("chunks", [])
    user_id = payload.get("user_id", "default").lower()
    collection_name = payload.get("collection_name", "nextcloud_files")

    if not chunks:
        return {"status": "SUCCESS", "count": 0}

    now_ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    now = int(time.time())
    count = 0

    for c in chunks:
        if not isinstance(c, dict):
            continue
        content = c.get("content")
        metadata = c.get("metadata", {})
        if not content:
            continue

        path = metadata.get("path", "unknown")
        chunk_idx = metadata.get("chunk_index", 0)
        path_hash = hashlib.md5(path.encode()).hexdigest()

        cid = (
            f"file:{user_id}:{path_hash}:meta"
            if metadata.get("is_metadata")
            else f"file:{user_id}:{path_hash}:{chunk_idx}"
        )

        meta = {}
        for k, v in metadata.items():
            meta[k] = v if isinstance(v, (str, int, float, bool)) else str(v)
        meta["user_id"] = user_id
        if "indexed_at" not in meta:
            meta["indexed_at"] = now_ts
        if "created_at" not in meta:
            meta["created_at"] = now

        try:
            _add_item(collection_name, cid, user_id, content, meta, now, now_ts)
            count += 1
        except Exception as e:
            log.error(f"File chunk sync failed for {cid}: {e}")

    log.info(f"Synced {count} file chunks for user {user_id} into {collection_name}")
    return {"status": "SUCCESS", "count": count}


@app.post("/rag/purge/{collection_name}", dependencies=[Depends(require_internal)])
async def purge_collection_endpoint(collection_name: str, payload: dict):
    user_id = payload.get("user_id", "default").lower()
    filter_meta = payload.get("filter", {})
    try:
        sql = "SELECT id FROM rag_items WHERE collection_name = ? AND user_id = ?"
        params: list[Any] = [collection_name, user_id]
        if filter_meta:
            for k, v in filter_meta.items():
                sql += " AND json_extract(metadata, ?) = ?"
                params.extend([f"$.{k}", v])
        rows = _conn().execute(sql, params).fetchall()
        ids = [r["id"] for r in rows]
        _delete_items(ids)
        log.info(f"Purged {len(ids)} entries from {collection_name} for user {user_id}")
        return {"status": "SUCCESS", "message": f"Purged entries from {collection_name}"}
    except Exception as e:
        log.error(f"Purge failed: {e}")
        raise HTTPException(status_code=500, detail="Purge failed") from e


@app.get("/rag/purge/{collection_name}", dependencies=[Depends(require_internal)])
async def purge_rag_collection(
    collection_name: str,
    user_id: str,
    filter: str | None = None,
    x_internal_secret: str | None = Header(default=None),
):
    """Purge entries via query parameters (legacy interface)."""
    if filter is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
    require_internal(x_internal_secret or "")
    try:
        user_id = user_id.lower()
        rows = _conn().execute(
            "SELECT id FROM rag_items WHERE collection_name = ? AND user_id = ?",
            [collection_name, user_id],
        ).fetchall()
        ids = [r["id"] for r in rows]
        _delete_items(ids)
        log.info(f"Purged collection {collection_name} for user {user_id}")
        return {"status": "SUCCESS", "message": f"Collection {collection_name} purged for user {user_id}"}
    except Exception as e:
        log.error(f"Purge failed for {collection_name}: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e


# ─────────────────────────────────────────────────────────────────────────────
# Home Assistant sync
# ─────────────────────────────────────────────────────────────────────────────

@app.post("/rag/sync/ha", dependencies=[Depends(require_internal)])
async def sync_ha(payload: dict, user_id: str | None = None):
    entities = payload.get("entities", [])
    resolved_user = (user_id or payload.get("user_id", "default")).lower()
    now = int(time.time())
    now_ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    try:
        existing_rows = _conn().execute(
            "SELECT id FROM rag_items WHERE collection_name = 'ha_entities' AND user_id = ?",
            [resolved_user],
        ).fetchall()
        existing_ids = {r["id"] for r in existing_rows}
    except Exception:
        existing_ids = set()

    new_count = 0
    for e in entities:
        if not isinstance(e, dict):
            continue
        eid = e.get("entity_id", "")
        if not eid:
            continue
        attrs = e.get("attributes", {})
        fname = attrs.get("friendly_name", eid)
        area = attrs.get("area_id") or "unassigned area"
        device_class = attrs.get("device_class", "")
        supported = attrs.get("supported_features", 0)

        dev_ip = attrs.get("_device_ip", "")
        dev_mac = attrs.get("_device_mac", "")
        dev_hostname = attrs.get("_device_hostname", "")
        dev_method = attrs.get("_device_discovery_method", "")
        dev_last_verified = attrs.get("_device_last_verified", 0)
        dev_metadata = attrs.get("_device_metadata", {})

        content = f"Device: {fname} (ID: {eid}) | Area: {area} | Type: {eid.split('.')[0]}"
        if device_class:
            content += f" | Device Class: {device_class}"
        if dev_ip:
            content += f" | IP: {dev_ip}"
        if dev_mac:
            content += f" | MAC: {dev_mac}"
        if dev_hostname:
            content += f" | Hostname: {dev_hostname}"

        cid = f"ha:{eid}"
        created_at = now
        if cid not in existing_ids:
            new_count += 1

        meta = {
            "entity_id": eid,
            "friendly_name": fname,
            "area": area,
            "device_class": device_class,
            "supported_features": str(supported),
            "user_id": resolved_user,
            "type": "ha_entity",
            "domain": eid.split(".")[0],
            "updated_at": now,
            "created_at": created_at,
            "indexed_at": now_ts,
            "device_ip": dev_ip,
            "device_mac": dev_mac,
            "device_hostname": dev_hostname,
            "device_discovery_method": dev_method,
            "device_last_verified": str(dev_last_verified) if dev_last_verified else "",
            "device_metadata": json.dumps(dev_metadata) if dev_metadata else "",
        }
        try:
            _add_item("ha_entities", cid, resolved_user, content, meta, created_at, now_ts)
        except Exception as ex:
            log.error(f"HA entity sync failed for {cid}: {ex}")

    # Orphan cleanup: delete entities no longer present in HA.
    incoming_ids = {f"ha:{e.get('entity_id')}" for e in entities if e.get("entity_id")}
    orphaned_entities = [oid for oid in (existing_ids - incoming_ids) if oid.startswith("ha:")]
    if orphaned_entities:
        _delete_items(orphaned_entities)
        log.info(f"[ha_sync] Removed {len(orphaned_entities)} orphaned entity entries")

    try:
        _add_item(
            "ha_entities",
            f"sync_status:{resolved_user}",
            resolved_user,
            f"Last HA sync for {resolved_user} at {now}. Total: {len(entities)}, "
            f"New: {new_count}, Removed: {len(orphaned_entities)}",
            {
                "type": "sync_status",
                "user_id": resolved_user,
                "timestamp": now,
                "count": len(entities),
                "new_count": new_count,
                "removed_count": len(orphaned_entities),
                "indexed_at": now_ts,
            },
            now,
            now_ts,
        )
    except Exception as ex:
        log.error(f"HA sync_status update failed: {ex}")

    return {
        "status": "SUCCESS",
        "count": len(entities),
        "new_count": new_count,
        "removed_count": len(orphaned_entities),
        "orphaned_entity_ids": orphaned_entities,
    }


@app.get("/rag/ha/status", dependencies=[Depends(require_internal)])
async def get_ha_status(user_id: str = "default"):
    row = _conn().execute(
        "SELECT metadata FROM rag_items WHERE id = ?",
        [f"sync_status:{user_id}"],
    ).fetchone()
    if row:
        return {"status": "SUCCESS", "data": json.loads(row["metadata"])}
    return {"status": "ERROR", "message": "No sync history found"}


@app.get("/rag/ha/new", dependencies=[Depends(require_internal)])
async def get_new_devices(user_id: str = "default", limit: int = 10):
    user_id = user_id.lower()
    last_24h = int(time.time()) - 86400
    rows = _conn().execute(
        "SELECT metadata FROM rag_items "
        "WHERE collection_name = 'ha_entities' AND user_id = ? AND created_at > ? LIMIT ?",
        [user_id, last_24h, limit],
    ).fetchall()
    return {"status": "SUCCESS", "devices": [json.loads(r["metadata"]) for r in rows]}


@app.post("/rag/sync/capabilities", dependencies=[Depends(require_internal)])
async def sync_capabilities(payload: dict):
    capabilities = payload.get("capabilities", [])
    now_ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    now = int(time.time())
    count = 0
    for cap in capabilities:
        name = cap.get("name")
        description = cap.get("description", "")
        schema = cap.get("schema", "")
        type_ = cap.get("type", "tool")
        if not name:
            continue
        cid = f"cap:{type_}:{name}"
        content = f"Capability: {name} | Description: {description} | Schema/Usage: {schema}"
        meta = {
            "name": name,
            "type": type_,
            "user_id": "default",
            "description": description[:200],
            "indexed_at": now_ts,
        }
        try:
            _add_item("system_capabilities", cid, "default", content, meta, now, now_ts)
            count += 1
        except Exception as e:
            log.error(f"Capability sync failed for {cid}: {e}")
    return {"status": "SUCCESS", "count": count}


# ─────────────────────────────────────────────────────────────────────────────
# Section 6: New relational-vector collections + sync endpoints
# ─────────────────────────────────────────────────────────────────────────────

@app.post("/rag/sync/missions", dependencies=[Depends(require_internal)])
async def sync_missions(payload: dict):
    """Ingest a completed agent-loop mission (post-mortem) for self-repair recall."""
    missions = payload.get("missions") or ([payload] if payload.get("mission_id") else [])
    if not missions:
        return {"status": "SUCCESS", "count": 0}

    now_ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    user_id = payload.get("user_id", "default").lower()
    count = 0
    for m in missions:
        mission_id = m.get("mission_id") or m.get("id")
        if not mission_id:
            continue
        task = m.get("task_description", "")
        final_status = m.get("final_status", "UNKNOWN")
        error_summary = m.get("error_summary") or ""
        steps = m.get("steps") or m.get("steps_json") or []
        steps_json = json.dumps(steps) if not isinstance(steps, str) else steps
        content = f"Task: {task}\nStatus: {final_status}\nError: {error_summary}".strip()
        created_at = int(m.get("created_at", time.time()))

        _conn().execute(
            "INSERT OR REPLACE INTO mission_history"
            "(mission_id, task_description, final_status, error_summary, steps_json, "
            "content, user_id, created_at) VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
            [mission_id, task, final_status, error_summary, steps_json, content, user_id, created_at],
        )
        _add_item("mission_history", mission_id, user_id, content, {
            "mission_id": mission_id,
            "final_status": final_status,
            "user_id": user_id,
            "indexed_at": now_ts,
        }, created_at, now_ts)
        count += 1

    return {"status": "SUCCESS", "count": count}


@app.post("/rag/sync/conversations", dependencies=[Depends(require_internal)])
async def sync_conversations(payload: dict):
    """Ingest intercom voice transcripts for conversational memory."""
    utterances = payload.get("utterances") or []
    if not utterances:
        return {"status": "SUCCESS", "count": 0}

    now_ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    user_id = payload.get("user_id", "default").lower()
    count = 0
    for u in utterances:
        utterance_id = u.get("utterance_id") or u.get("id") or str(hashlib.md5(
            f"{u.get('speaker','?')}:{u.get('text_content','')}:{u.get('timestamp',0)}".encode()
        ).hexdigest())
        speaker = u.get("speaker", "unknown")
        text_content = u.get("text_content", "")
        room_id = u.get("room_id", "unknown")
        timestamp = int(u.get("timestamp", time.time()))
        content = f"{speaker} in {room_id}: {text_content}"
        _conn().execute(
            "INSERT OR REPLACE INTO conversation_memory"
            "(utterance_id, speaker, text_content, room_id, content, user_id, timestamp) "
            "VALUES(?, ?, ?, ?, ?, ?, ?)",
            [utterance_id, speaker, text_content, room_id, content, user_id, timestamp],
        )
        _add_item("conversation_memory", utterance_id, user_id, content, {
            "speaker": speaker,
            "room_id": room_id,
            "user_id": user_id,
            "indexed_at": now_ts,
        }, timestamp, now_ts)
        count += 1

    return {"status": "SUCCESS", "count": count}


@app.post("/rag/sync/network", dependencies=[Depends(require_internal)])
async def sync_network(payload: dict):
    """Ingest Docker DNS/service-discovery topology for semantic resolution."""
    containers = payload.get("containers") or []
    if not containers:
        # Allow single-container payloads too.
        if payload.get("container_name"):
            containers = [payload]
        else:
            return {"status": "SUCCESS", "count": 0}

    now_ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    user_id = payload.get("user_id", "default").lower()
    count = 0
    for c in containers:
        container_name = c.get("container_name") or c.get("name")
        if not container_name:
            continue
        ip_address = c.get("ip_address", "")
        exposed_ports = json.dumps(c.get("exposed_ports", c.get("ports", [])))
        discovered_services = json.dumps(c.get("discovered_services", c.get("services", [])))
        network_name = c.get("network_name", "")
        content = (
            f"Container {container_name} on network {network_name} at {ip_address} "
            f"exposes ports {exposed_ports} and services {discovered_services}"
        )
        _conn().execute(
            "INSERT OR REPLACE INTO network_topology"
            "(container_name, ip_address, exposed_ports, discovered_services, "
            "network_name, content, user_id) VALUES(?, ?, ?, ?, ?, ?, ?)",
            [container_name, ip_address, exposed_ports, discovered_services, network_name, content, user_id],
        )
        _add_item("network_topology", container_name, user_id, content, {
            "container_name": container_name,
            "ip_address": ip_address,
            "network_name": network_name,
            "user_id": user_id,
            "indexed_at": now_ts,
        }, int(time.time()), now_ts)
        count += 1

    return {"status": "SUCCESS", "count": count}


@app.post("/rag/sync/telemetry_alerts", dependencies=[Depends(require_internal)])
async def sync_telemetry_alerts(payload: dict):
    """Ingest processed semantic telemetry alerts so Jarvis can recall them."""
    alerts = payload.get("alerts") or ([payload] if payload.get("entity_id") else [])
    if not alerts:
        return {"status": "SUCCESS", "count": 0}

    now_ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    user_id = payload.get("user_id", "default").lower()
    count = 0
    for a in alerts:
        entity_id = a.get("entity_id", "unknown")
        alert_type = a.get("alert_type", "generic")
        severity = a.get("severity", "info")
        text = a.get("content") or a.get("text") or a.get("alert_text") or (
            f"Telemetry Alert: {entity_id} {alert_type} ({severity})"
        )
        alert_id = a.get("alert_id") or f"alert:{entity_id}:{int(time.time()*1000)}"
        created_at = int(a.get("created_at", time.time()))
        _conn().execute(
            "INSERT OR REPLACE INTO telemetry_alerts"
            "(alert_id, entity_id, alert_type, severity, content, user_id, created_at) "
            "VALUES(?, ?, ?, ?, ?, ?, ?)",
            [alert_id, entity_id, alert_type, severity, text, user_id, created_at],
        )
        _add_item("telemetry_alerts", alert_id, user_id, text, {
            "entity_id": entity_id,
            "alert_type": alert_type,
            "severity": severity,
            "user_id": user_id,
            "indexed_at": now_ts,
        }, created_at, now_ts)
        count += 1

    return {"status": "SUCCESS", "count": count}


@app.get("/rag/mission/{mission_id}", dependencies=[Depends(require_internal)])
async def get_mission(mission_id: str):
    row = _conn().execute(
        "SELECT mission_id, task_description, final_status, error_summary, steps_json, created_at "
        "FROM mission_history WHERE mission_id = ?",
        [mission_id],
    ).fetchone()
    if not row:
        return {"status": "ERROR", "message": "Mission not found"}
    return {"status": "SUCCESS", "mission": dict(row)}


START_TIME = time.time()


@app.get("/health")
def health():
    return {
        "status": "ok",
        "service": "rag",
        "git_sha": os.getenv("GIT_SHA", "unknown"),
        "start_time": START_TIME,
        "backend": BACKEND,
        "embedding_dim": EMBEDDING_DIM,
    }
