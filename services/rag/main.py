# services/rag/main.py
"""
Microservice 4: Context & RAG Service
Manages ChromaDB for vector search and ingestion.
"""
import os
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends, HTTPException, Header, Request, status
import chromadb
from chromadb.config import Settings
from chromadb.utils import embedding_functions

try:
    from .schemas import SearchRequest, SearchResponse, SearchResultItem, IngestRequest
except ImportError:
    from schemas import SearchRequest, SearchResponse, SearchResultItem, IngestRequest

log = logging.getLogger("rag")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s")

INTERNAL_SECRET = os.getenv("INTERNAL_SECRET", "change-me-in-production")
CHROMA_DIR = os.getenv("CHROMA_PERSIST_DIR", "/data/chroma_db")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2")

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

from fastapi.responses import JSONResponse
import traceback

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
        # Get or create collection
        return chroma_client.get_or_create_collection(
            name=name,
            embedding_function=embedding_fn
        )
    except Exception as e:
        log.error(f"Failed to get collection {name}: {e}")
        raise HTTPException(status_code=500, detail="Database error")

@app.post("/rag/search", response_model=SearchResponse, dependencies=[Depends(require_internal)])
async def search(req: SearchRequest):
    collection = get_collection(req.collection_name)
    
    # Metadata filter for privacy (user's own data or default shared data)
    where_filter = {
        "$or": [
            {"user_id": req.user_id},
            {"user_id": "default"}
        ]
    }
    
    try:
        results = collection.query(
            query_texts=[req.query],
            n_results=req.k,
            where=where_filter
        )
        
        response_items = []
        if results and results["documents"] and len(results["documents"]) > 0:
            docs = results["documents"][0]
            metas = results["metadatas"][0] if results.get("metadatas") else [{}] * len(docs)
            for doc, meta in zip(docs, metas):
                response_items.append(SearchResultItem(content=doc, metadata=meta))
                
        return SearchResponse(results=response_items)
    except Exception as e:
        log.error(f"Search failed: {e}")
        return SearchResponse(results=[])

@app.get("/rag/stats", dependencies=[Depends(require_internal)])
async def get_stats(user_id: str = "default"):
    """Return counts and metadata for collections."""
    try:
        collections = ["nextcloud_files", "ha_entities"]
        stats = {}
        for name in collections:
            coll = chroma_client.get_or_create_collection(name=name, embedding_function=embedding_fn)
            count = coll.count()
            # Get 5 latest entries to show what's indexed
            latest = coll.get(limit=5, include=["metadatas"])
            stats[name] = {
                "count": count,
                "latest_previews": [m.get("path", m.get("friendly_name", "unknown")) for m in latest["metadatas"]] if latest["metadatas"] else []
            }
        return {"status": "SUCCESS", "stats": stats}
    except Exception as e:
        log.error(f"Stats failed: {e}")
        return JSONResponse(status_code=500, content={"status": "ERROR", "message": str(e)})

@app.post("/rag/ingest", dependencies=[Depends(require_internal)])
async def ingest(req: IngestRequest):
    collection = get_collection(req.collection_name)
    
    import uuid
    doc_id = str(uuid.uuid4())
    
    # Enforce user_id in metadata for privacy
    meta = req.metadata.copy()
    meta["user_id"] = req.user_id
    
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
    """
    Ingests file chunks for a specific provider/user.
    """
    chunks = payload.get("chunks", [])
    user_id = payload.get("user_id", "admin")
    collection_name = payload.get("collection_name", "nextcloud_files")
    collection = get_collection(collection_name)
    
    if not chunks:
        return {"status": "SUCCESS", "count": 0}
        
    ids = []
    docs = []
    metas = []
    
    import hashlib
    for c in chunks:
        if not isinstance(c, dict): continue
        content = c.get("content")
        metadata = c.get("metadata", {})
        if not content:
            continue
            
        path = metadata.get("path", "unknown")
        chunk_idx = metadata.get("chunk_index", 0)
        
        # Unique ID per chunk
        # Using hash of path to avoid special character issues in IDs
        path_hash = hashlib.md5(path.encode()).hexdigest()
        cid = f"file:{user_id}:{path_hash}:{chunk_idx}"
        
        # Enforce user_id in meta
        meta = metadata.copy()
        meta["user_id"] = user_id
        
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

@app.post("/rag/purge", dependencies=[Depends(require_internal)])
async def purge(payload: dict):
    """
    Purges entries from a collection based on a filter.
    """
    collection_name = payload.get("collection_name")
    user_id = payload.get("user_id")
    filter_meta = payload.get("filter", {})
    
    if not collection_name or not user_id:
        raise HTTPException(status_code=400, detail="collection_name and user_id required")
        
    collection = get_collection(collection_name)
    
    # Always enforce user_id for safety
    conditions = [{"user_id": user_id}]
    for k, v in filter_meta.items():
        conditions.append({k: v})
        
    where_filter = {"$and": conditions} if len(conditions) > 1 else conditions[0]
        
    try:
        collection.delete(where=where_filter)
        log.info(f"Purged entries from {collection_name} for user {user_id} with filter {filter_meta}")
        return {"status": "SUCCESS", "message": f"Purged entries from {collection_name}"}
    except Exception as e:
        log.error(f"Purge failed: {e}")
        raise HTTPException(status_code=500, detail="Purge failed")

@app.post("/rag/sync/ha", dependencies=[Depends(require_internal)])
async def sync_ha(payload: dict):
    """
    Enriches and indexes HA entities for RAG.
    Tracks new vs updated entities.
    """
    entities = payload.get("entities", [])
    user_id = payload.get("user_id", "admin")
    collection = get_collection("ha_entities")
    
    import time
    now = int(time.time())
    
    # Get existing IDs to find new ones
    try:
        existing = collection.get(where={"user_id": user_id})
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
        
        content = f"Device: {fname} (ID: {eid}) | Area: {area} | Current State: {state}."
        if "brightness" in attrs:
            # Convert 0-255 to percentage
            bright_pct = round((attrs['brightness'] / 255) * 100)
            content += f" Brightness is at {bright_pct}% ({attrs['brightness']}/255)."
        if "current_temperature" in attrs:
            content += f" Temperature: {attrs['current_temperature']}."
        if "unit_of_measurement" in attrs:
            content += f" {attrs['unit_of_measurement']}."
            
        cid = f"ha:{eid}"
        if cid not in existing_ids:
            new_count += 1
            created_at = now
        else:
            # Preserve created_at if possible
            created_at = now # Simplified
            
        ids.append(cid)
        docs.append(content)
        metas.append({
            "entity_id": eid,
            "friendly_name": fname,
            "area": area,
            "user_id": user_id,
            "type": "ha_entity",
            "updated_at": now,
            "created_at": created_at
        })
    
    if docs:
        try:
            collection.upsert(
                ids=ids,
                documents=docs,
                metadatas=metas
            )
            # Store sync status in a special meta entry
            collection.upsert(
                ids=[f"sync_status:{user_id}"],
                documents=[f"Last HA sync for {user_id} at {now}. Total: {len(docs)}, New: {new_count}"],
                metadatas=[{"type": "sync_status", "user_id": user_id, "timestamp": now, "count": len(docs), "new_count": new_count}]
            )
            log.info(f"Synced {len(docs)} HA entities for user {user_id} ({new_count} new)")
            return {"status": "SUCCESS", "count": len(docs), "new_count": new_count}
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
        # Search for items updated/created recently
        # Note: Chroma '$gt' filters work on metadata
        import time
        last_24h = int(time.time()) - 86400
        res = collection.get(
            where={"$and": [{"user_id": user_id}, {"created_at": {"$gt": last_24h}}]},
            limit=limit
        )
        return {"status": "SUCCESS", "devices": res["metadatas"] if res else []}
    except Exception as e:
        return {"status": "ERROR", "message": str(e)}

@app.get("/health")
def health():
    return {"status": "ok", "service": "rag"}
