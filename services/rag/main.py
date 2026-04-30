# services/rag/main.py
"""
Microservice 4: Context & RAG Service
Manages ChromaDB for vector search and ingestion.
"""
import os
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends, HTTPException, Header, status
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

@app.post("/rag/sync/ha", dependencies=[Depends(require_internal)])
async def sync_ha(payload: dict):
    """
    Enriches and indexes HA entities for RAG.
    Ported from legacy ha_ingest.py
    """
    entities = payload.get("entities", [])
    user_id = payload.get("user_id", "admin")
    collection = get_collection("ha_entities")
    
    ids = []
    docs = []
    metas = []
    
    for e in entities:
        eid = e.get("entity_id", "")
        if not eid: continue
        
        state = e.get("state", "unknown")
        attrs = e.get("attributes", {})
        fname = attrs.get("friendly_name", eid)
        area = attrs.get("area_id", "unknown")
        
        # Enrichment: Create a descriptive string for the vector DB
        content = f"The {fname} ({eid}) is in the {area} and is currently {state}."
        if "brightness" in attrs:
            content += f" It supports brightness (current: {attrs['brightness']})."
        if "current_temperature" in attrs:
            content += f" The current temperature is {attrs['current_temperature']}."
            
        ids.append(f"ha:{eid}")
        docs.append(content)
        metas.append({
            "entity_id": eid,
            "friendly_name": fname,
            "area": area,
            "user_id": user_id,
            "type": "ha_entity"
        })
    
    if docs:
        try:
            # Clear old and add new (or upsert)
            collection.upsert(
                ids=ids,
                documents=docs,
                metadatas=metas
            )
            log.info(f"Synced {len(docs)} HA entities for user {user_id}")
            return {"status": "SUCCESS", "count": len(docs)}
        except Exception as e:
            log.error(f"HA Sync failed: {e}")
            raise HTTPException(status_code=500, detail="Sync failed")
    
    return {"status": "SUCCESS", "count": 0}

@app.get("/health")
def health():
    return {"status": "ok", "service": "rag"}
