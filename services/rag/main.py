# services/rag/main.py
"""
Microservice 4: Context & RAG Service
Manages ChromaDB for vector search and ingestion.
"""
import os
import sys
import json
import logging
import time
import hashlib
sys.path.insert(0, os.path.dirname(__file__))

from config import INTERNAL_SECRET, CHROMA_PERSIST_DIR, EMBEDDING_MODEL
from typing import Optional
from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends, HTTPException, Header, Request, status
from fastapi.responses import JSONResponse
import chromadb
from chromadb.config import Settings
from chromadb.utils import embedding_functions
import traceback

try:
    from .schemas import SearchRequest, SearchResponse, SearchResultItem, IngestRequest
except ImportError:
    from schemas import SearchRequest, SearchResponse, SearchResultItem, IngestRequest

log = logging.getLogger("rag")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s")

CHROMA_DIR = CHROMA_PERSIST_DIR

# Global clients
chroma_client = None
embedding_fn = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global chroma_client, embedding_fn
    log.info(f"Initializing RAG Service. Chroma DB dir: {CHROMA_DIR}")
    
    os.makedirs(CHROMA_DIR, exist_ok=True)
    chroma_client = chromadb.PersistentClient(path=CHROMA_DIR, settings=Settings(anonymized_telemetry=False))
    embedding_fn = embedding_functions.SentenceTransformerEmbeddingFunction(model_name=EMBEDDING_MODEL)
    
    log.info("RAG Service Ready.")
    yield
    log.info("RAG Service shutting down.")

app = FastAPI(title="SharedLLM RAG Service", version="1.0.0", lifespan=lifespan)

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    err_msg = f"RAG Error: {type(exc).__name__}: {str(exc)}"
    log.error(f"{err_msg}\n{traceback.format_exc()}")
    return JSONResponse(
        status_code=500,
        content={"status": "ERROR", "message": "Internal RAG Error", "detail": str(exc)}
    )

def require_internal(x_internal_secret: str = Header(...)):
    if x_internal_secret != INTERNAL_SECRET:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")

def get_collection(name: str):
    try:
        return chroma_client.get_or_create_collection(
            name=name,
            embedding_function=embedding_fn
        )
    except Exception as e:
        log.error(f"Failed to get collection {name}: {e}")
        raise HTTPException(status_code=500, detail="Database error")


def _freeze_for_hash(value):
    if isinstance(value, dict):
        return tuple(sorted((str(k), _freeze_for_hash(v)) for k, v in value.items()))
    if isinstance(value, list):
        return tuple(_freeze_for_hash(item) for item in value)
    if isinstance(value, set):
        return tuple(sorted(_freeze_for_hash(item) for item in value))
    return value

@app.post("/rag/search", response_model=SearchResponse, dependencies=[Depends(require_internal)])
async def search(req: SearchRequest):
    collection = get_collection(req.collection_name)
    
    where_filter = {
        "$or": [
            {"user_id": req.user_id},
            {"user_id": "default"}
        ]
    }
    
    try:
        vector_results = collection.query(
            query_texts=[req.query],
            n_results=req.k * 2,
            where=where_filter
        )
        
        keyword_results = collection.query(
            query_texts=[req.query],
            n_results=req.k * 2,
            where=where_filter,
            where_document={"$contains": req.query}
        )

        K_RRF = 60
        scores = {}
        
        def process_results(results):
            if not results or not results["documents"] or not results["documents"][0]:
                return
            docs = results["documents"][0]
            metas = results["metadatas"][0] if results.get("metadatas") else [{}] * len(docs)
            for i, (doc, meta) in enumerate(zip(docs, metas)):
                key = (
                    doc,
                    tuple(
                        sorted(
                            (str(k), _freeze_for_hash(v)) for k, v in meta.items()
                        )
                    ),
                )
                scores[key] = scores.get(key, 0) + (1.0 / (K_RRF + i + 1))

        process_results(vector_results)
        process_results(keyword_results)
        
        sorted_results = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        top_k = sorted_results[:req.k]
        
        response_items = []
        for (doc, meta_tuple), score in top_k:
            response_items.append(SearchResultItem(content=doc, metadata=dict(meta_tuple)))
                
        return SearchResponse(results=response_items)
    except Exception as e:
        log.error(f"Hybrid search failed: {e}")
        return SearchResponse(results=[])

@app.post("/rag/purge/{collection_name}")
def purge_rag_collection(
    collection_name: str,
    user_id: str,
    filter: dict = {},
    x_internal_secret: Optional[str] = Header(default=None)
):
    """Purge entries via query parameters (legacy interface)."""
    require_internal(x_internal_secret)
    try:
        user_id = user_id.lower()
        coll = chroma_client.get_collection(name=collection_name, embedding_function=embedding_fn)
        where_filter = {"user_id": user_id}
        if filter:
            where_filter.update(filter)
        coll.delete(where=where_filter)
        log.info(f"Purged collection {collection_name} for user {user_id} with filter {where_filter}")
        return {"status": "SUCCESS", "message": f"Collection {collection_name} purged for user {user_id}"}
    except Exception as e:
        log.error(f"Purge failed for {collection_name}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/rag/stats", dependencies=[Depends(require_internal)])
async def get_stats(user_id: str = "default"):
    """Return counts and metadata for collections in the format expected by the UI."""
    user_id = user_id.lower()
    log.info(f"Fetching stats for user_id: {user_id}")
    try:
        collections = ["nextcloud_files", "ha_entities", "system_capabilities", "system_learnings"]
        total_chunks = 0
        coll_chunks_map = {}
        coll_docs_map = {}
        total_documents = 0
        providers = []
        last_indexed = None

        for name in collections:
            coll = chroma_client.get_or_create_collection(name=name, embedding_function=embedding_fn)
            
            # For system_capabilities, we always use the 'default' user_id
            # For others, we use the provided user_id
            target_user = "default" if name == "system_capabilities" else user_id
            
            # Query all entries for this user to get counts and documents
            results = coll.get(where={"user_id": target_user}, include=["metadatas"])
            if results and results["ids"]:
                total_chunks += len(results["ids"])
                coll_chunks_map[name] = len(results["ids"])
                providers.append(name.split('_')[0])
                
                if results["metadatas"]:
                    unique_items = set()
                    for m in results["metadatas"]:
                        item_id = m.get("path") or m.get("friendly_name") or m.get("entity_id")
                        if item_id:
                            unique_items.add(item_id)
                        
                        # Track last indexed timestamp
                        idx_at = m.get("indexed_at")
                        if idx_at:
                            if not last_indexed or idx_at > last_indexed:
                                last_indexed = idx_at
                    
                    doc_count = len(unique_items)
                    
                    if not unique_items and len(results["ids"]) > 0:
                        doc_count = 1
                    
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
                    "documents": coll_docs_map.get(name, 0)
                } for name in collections
            }
        }
    except Exception as e:
        log.error(f"Stats failed: {e}")
        return JSONResponse(status_code=500, content={"status": "ERROR", "message": str(e)})

@app.get("/rag/collection/{collection_name}", dependencies=[Depends(require_internal)])
async def list_collection_documents(collection_name: str, user_id: str = "default", limit: int = 100):
    """Retrieve documents and metadata from a specific collection for a user."""
    try:
        collection = chroma_client.get_or_create_collection(name=collection_name, embedding_function=embedding_fn)
        target_user = "default" if collection_name == "system_capabilities" else user_id
        
        results = collection.get(
            where={"user_id": target_user},
            limit=limit,
            include=["documents", "metadatas"]
        )
        
        items = []
        if results and results["ids"]:
            for i in range(len(results["ids"])):
                items.append({
                    "id": results["ids"][i],
                    "document": results["documents"][i],
                    "metadata": results["metadatas"][i]
                })
        
        return {
            "status": "SUCCESS",
            "collection": collection_name,
            "user_id": target_user,
            "count": len(items),
            "items": items
        }
    except Exception as e:
        log.error(f"Failed to list collection {collection_name}: {e}")
        return {"status": "ERROR", "message": str(e)}

@app.get("/rag/indexed-paths", dependencies=[Depends(require_internal)])
async def get_indexed_paths(user_id: str = "default"):
    """Return a list of all paths currently indexed for a user."""
    user_id = user_id.lower()
    try:
        collection = chroma_client.get_or_create_collection(name="nextcloud_files", embedding_function=embedding_fn)
        results = collection.get(where={"user_id": user_id}, include=["metadatas"])
        if results and results["metadatas"]:
            paths = {m.get("path") for m in results["metadatas"] if m.get("path")}
            return {"status": "SUCCESS", "paths": list(paths)}
        return {"status": "SUCCESS", "paths": []}
    except Exception as e:
        log.error(f"Failed to fetch indexed paths: {e}")
        return JSONResponse(status_code=500, content={"status": "ERROR", "message": str(e)})

@app.post("/rag/ingest", dependencies=[Depends(require_internal)])
async def ingest(req: IngestRequest):
    collection = get_collection(req.collection_name)
    import uuid
    doc_id = str(uuid.uuid4())
    # Enforce user_id in metadata for privacy
    meta = req.metadata.copy()
    meta["user_id"] = req.user_id.lower()
    # Add timestamp
    meta["indexed_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    
    try:
        collection.add(
            documents=[req.content],
            metadatas=[meta],
            ids=[doc_id]
        )
        return {"status": "SUCCESS", "id": doc_id}
    except Exception as e:
        log.error(f"Ingest failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to ingest document")

@app.post("/rag/sync/files", dependencies=[Depends(require_internal)])
async def sync_files(payload: dict):
    chunks = payload.get("chunks", [])
    user_id = payload.get("user_id", "default").lower()
    collection_name = payload.get("collection_name", "nextcloud_files")
    collection = get_collection(collection_name)
    
    if not chunks:
        return {"status": "SUCCESS", "count": 0}
        
    ids = []
    docs = []
    metas = []
    
    now_ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    
    for c in chunks:
        if not isinstance(c, dict): continue
        content = c.get("content")
        metadata = c.get("metadata", {})
        if not content:
            continue
            
        path = metadata.get("path", "unknown")
        chunk_idx = metadata.get("chunk_index", 0)
        path_hash = hashlib.md5(path.encode()).hexdigest()
        
        if metadata.get("is_metadata"):
            cid = f"file:{user_id}:{path_hash}:meta"
        else:
            cid = f"file:{user_id}:{path_hash}:{chunk_idx}"
        
        meta = {}
        for k, v in metadata.items():
            if isinstance(v, (str, int, float, bool)):
                meta[k] = v
            else:
                meta[k] = str(v)
        meta["user_id"] = user_id
        # Always ensure indexed_at is present
        if "indexed_at" not in meta:
            meta["indexed_at"] = now_ts
        
        ids.append(cid)
        docs.append(content)
        metas.append(meta)
        
    if docs:
        try:
            collection.upsert(
                ids=ids,
                documents=docs,
                metadatas=metas
            )
            log.info(f"Synced {len(docs)} file chunks for user {user_id} into {collection_name}")
            return {"status": "SUCCESS", "count": len(docs)}
        except Exception as e:
            log.error(f"File Sync failed: {e}")
            raise HTTPException(status_code=500, detail="Sync failed")
            
    return {"status": "SUCCESS", "count": 0}

@app.post("/rag/purge/{collection_name}", dependencies=[Depends(require_internal)])
async def purge_collection_endpoint(collection_name: str, payload: dict):
    user_id = payload.get("user_id", "default").lower()
    filter_meta = payload.get("filter", {})
    collection = get_collection(collection_name)
    where_filter = {"user_id": user_id}
    if filter_meta:
        where_filter = {"$and": [{"user_id": user_id}, filter_meta]}
    try:
        collection.delete(where=where_filter)
        log.info(f"Purged entries from {collection_name} for user {user_id}")
        return {"status": "SUCCESS", "message": f"Purged entries from {collection_name}"}
    except Exception as e:
        log.error(f"Purge failed: {e}")
        raise HTTPException(status_code=500, detail="Purge failed")

ACTIVE_STATES = {"on", "playing", "idle", "standby", "home", "cooling", "heating", "drying", "cleaning"}

@app.post("/rag/sync/ha", dependencies=[Depends(require_internal)])
async def sync_ha(payload: dict, user_id: Optional[str] = None):
    entities = payload.get("entities", [])
    # Prioritize query param, then payload, then default to 'default'
    resolved_user = (user_id or payload.get("user_id", "default")).lower()
    collection = get_collection("ha_entities")
    now = int(time.time())
    now_ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    
    try:
        existing = collection.get(where={"user_id": resolved_user})
        existing_ids = set(existing["ids"]) if existing and "ids" in existing else set()
    except:
        existing_ids = set()

    ids = []
    docs = []
    metas = []
    new_count = 0
    
    for e in entities:
        if not isinstance(e, dict): continue
        eid = e.get("entity_id", "")
        if not eid: continue
        state = e.get("state", "unknown")
        attrs = e.get("attributes", {})
        fname = attrs.get("friendly_name", eid)
        area = attrs.get("area_id") or "unassigned area"
        device_class = attrs.get("device_class", "")
        supported = attrs.get("supported_features", 0)
        
        # Device registry enrichment
        dev_ip = attrs.get("_device_ip", "")
        dev_mac = attrs.get("_device_mac", "")
        dev_hostname = attrs.get("_device_hostname", "")
        dev_method = attrs.get("_device_discovery_method", "")
        dev_last_verified = attrs.get("_device_last_verified", 0)
        dev_metadata = attrs.get("_device_metadata", {})
        
        # Build content string (semantic search text)
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
        else:
            created_at = now # Simplified
            
        ids.append(cid)
        docs.append(content)
        metas.append({
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
            # Device registry fields (for structured filtering)
            "device_ip": dev_ip,
            "device_mac": dev_mac,
            "device_hostname": dev_hostname,
            "device_discovery_method": dev_method,
            "device_last_verified": str(dev_last_verified) if dev_last_verified else "",
            "device_metadata": json.dumps(dev_metadata) if dev_metadata else "",
        })
    
    if docs:
        try:
            collection.upsert(ids=ids, documents=docs, metadatas=metas)
            
            # ── Orphan cleanup: delete entries that no longer exist in HA ──
            incoming_ids = set(ids)
            orphaned_ids = existing_ids - incoming_ids
            # Exclude non-entity entries (sync_status, etc.)
            orphaned_entities = [oid for oid in orphaned_ids if oid.startswith("ha:")]
            if orphaned_entities:
                collection.delete(ids=orphaned_entities)
                log.info(f"[ha_sync] Removed {len(orphaned_entities)} orphaned entity entries: {orphaned_entities[:5]}...")
            
            collection.upsert(
                ids=[f"sync_status:{resolved_user}"],
                documents=[f"Last HA sync for {resolved_user} at {now}. Total: {len(docs)}, New: {new_count}, Removed: {len(orphaned_entities)}"],
                metadatas=[{"type": "sync_status", "user_id": resolved_user, "timestamp": now, "count": len(docs), "new_count": new_count, "removed_count": len(orphaned_entities), "indexed_at": now_ts}]
            )
            return {"status": "SUCCESS", "count": len(docs), "new_count": new_count, "removed_count": len(orphaned_entities), "orphaned_entity_ids": orphaned_entities}
        except Exception as e:
            log.error(f"HA Sync failed: {e}")
            raise HTTPException(status_code=500, detail="Sync failed")
    return {"status": "SUCCESS", "count": 0, "new_count": 0}

@app.get("/rag/ha/status", dependencies=[Depends(require_internal)])
async def get_ha_status(user_id: str = "default"):
    collection = get_collection("ha_entities")
    try:
        res = collection.get(ids=[f"sync_status:{user_id}"])
        if res and res["metadatas"]:
            return {"status": "SUCCESS", "data": res["metadatas"][0]}
        return {"status": "ERROR", "message": "No sync history found"}
    except Exception as e:
        return {"status": "ERROR", "message": str(e)}

@app.get("/rag/ha/new", dependencies=[Depends(require_internal)])
async def get_new_devices(user_id: str = "default", limit: int = 10):
    collection = get_collection("ha_entities")
    try:
        last_24h = int(time.time()) - 86400
        res = collection.get(
            where={"$and": [{"user_id": user_id}, {"created_at": {"$gt": last_24h}}]},
            limit=limit
        )
        return {"status": "SUCCESS", "devices": res["metadatas"] if res else []}
    except Exception as e:
        return {"status": "ERROR", "message": str(e)}

@app.post("/rag/sync/capabilities", dependencies=[Depends(require_internal)])
async def sync_capabilities(payload: dict):
    capabilities = payload.get("capabilities", [])
    collection = get_collection("system_capabilities")
    ids = []
    docs = []
    metas = []
    now_ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    
    for cap in capabilities:
        name = cap.get("name")
        description = cap.get("description", "")
        schema = cap.get("schema", "")
        type_ = cap.get("type", "tool")
        if not name: continue
        cid = f"cap:{type_}:{name}"
        content = f"Capability: {name} | Description: {description} | Schema/Usage: {schema}"
        ids.append(cid)
        docs.append(content)
        metas.append({
            "name": name,
            "type": type_,
            "user_id": "default",
            "description": description[:200],
            "indexed_at": now_ts
        })
    if docs:
        try:
            collection.upsert(ids=ids, documents=docs, metadatas=metas)
            return {"status": "SUCCESS", "count": len(docs)}
        except Exception as e:
            log.error(f"Capability Sync failed: {e}")
            raise HTTPException(status_code=500, detail="Sync failed")
    return {"status": "SUCCESS", "count": 0}


@app.get("/health")
def health():
    return {"status": "ok", "service": "rag"}
