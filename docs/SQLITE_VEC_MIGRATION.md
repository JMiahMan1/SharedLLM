# Migration Blueprint: Replacing ChromaDB with `sqlite-vec`

This document defines the technical design, database schemas, code changes, and execution steps to replace ChromaDB with `sqlite-vec` in the `SharedLLM` RAG service.

---

## 1. Architectural Context & Decision

* **Status:** Proposed
* **Impacted Service:** `sharedllm_rag` (Microservice 4)
* **Goal:** Reduce RAG container RAM usage on the remote host (Intel N150 CPU / 16GB RAM) by removing ChromaDB and replacing it with a native SQLite database powered by the `sqlite-vec` C extension.

### Memory & CPU Analysis on Host (`192.168.2.205`)
* **ChromaDB overhead:** Currently consumes **1.355 GiB** of RAM to maintain HNSW graph indices in memory.
* **sqlite-vec overhead:** SQLite processes vectors on-disk and in-memory using small page caches, reducing container memory usage to **~150–200 MiB** (reclaiming **~1.1 GiB** of host RAM).
* **SIMD support:** The host's **Intel N150 CPU** has native `avx`, `avx2`, and `avx_vnni` flags. `sqlite-vec` compiles to native AVX2 instructions, accelerating vector distance calculations.

---

## 2. Database Schema Design

All vector and metadata content will be stored in `/data/rag.db`.

```sql
-- 1. Standard metadata table
CREATE TABLE IF NOT EXISTS rag_items (
    id TEXT PRIMARY KEY,
    collection_name TEXT NOT NULL,
    user_id TEXT NOT NULL,
    content TEXT NOT NULL,
    metadata TEXT NOT NULL, -- JSON-serialized dictionary
    created_at INTEGER NOT NULL,
    indexed_at TEXT NOT NULL
);

-- Index for relational pre-filtering
CREATE INDEX IF NOT EXISTS idx_rag_items_lookup 
ON rag_items(collection_name, user_id);

-- 2. Virtual vector table for sqlite-vec (using 384-dimensional embeddings)
CREATE VIRTUAL TABLE vec_rag_items USING vec0(
    id TEXT PRIMARY KEY,
    embedding float[384]
);
```

### Search Query Logic
To search the database, perform a SQL join. By filtering on `collection_name` and `user_id` *first*, SQLite's B-Tree narrows down the search space before running the vector distance math:
```sql
SELECT 
    i.id,
    i.content, 
    i.metadata, 
    v.distance
FROM vec_rag_items v
JOIN rag_items i ON v.id = i.id
WHERE i.collection_name = :collection
  AND (i.user_id = :user_id OR i.user_id = 'default')
ORDER BY vec_distance_cosine(v.embedding, :query_vector)
LIMIT :k;
```

---

## 3. Code Modifications

### A. Dependency Changes
Modify [services/rag/requirements.txt](file:///home/jeremiah/Summers%20Drive/Code/SharedLLM/services/rag/requirements.txt):
```diff
-chromadb
-langchain-chroma
+sqlite-vec
```

Remove compiler build toolchains (like cargo and building tools for HNSW C++ dependencies) from [docker/Dockerfile.base](file:///home/jeremiah/Summers%20Drive/Code/SharedLLM/docker/Dockerfile.base) to slim down base image layers.

### B. Database Initialization (`services/rag/db.py`)
Create a helper to manage connection loading and concurrency configurations:
```python
# services/rag/db.py
import sqlite3
import sqlite_vec

def get_db_connection(db_path: str = "/data/rag.db") -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    
    # 1. Enable WAL Mode (Concurrently read while writing)
    conn.execute("PRAGMA journal_mode=WAL;")
    # 2. Prevent database-locked operational errors by waiting up to 10s
    conn.execute("PRAGMA busy_timeout=10000;")
    conn.execute("PRAGMA foreign_keys=ON;")
    
    # 3. Load sqlite-vec extension
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    
    return conn
```

### C. RAG Adapter Pattern & Fallback Interface
Encapsulate vector search under an Adapter interface in [services/rag/main.py](file:///home/jeremiah/Summers%20Drive/Code/SharedLLM/services/rag/main.py):

```python
from abc import ABC, abstractmethod

class VectorStoreAdapter(ABC):
    @abstractmethod
    async def add(self, collection: str, doc_id: str, content: str, embedding: list[float], metadata: dict):
        pass

    @abstractmethod
    async def search(self, collection: str, user_id: str, query_vector: list[float], k: int) -> list[dict]:
        pass

    @abstractmethod
    async def delete(self, collection: str, user_id: str, filter_dict: dict = None):
        pass
```

Implement `SqliteVecAdapter` using `db.get_db_connection()`.
If `sqlite_vec` loading fails at startup (e.g., if Python's `sqlite3` was compiled without extension support), log a warning and fall back to standard keyword search (FTS5) or pure Python `numpy` similarity metrics.

---

## 4. One-Time Data Migration Script

Save the following as a script (e.g., `scripts/migrate_chroma_to_sqlite.py`) and execute it. It reads raw embeddings directly from ChromaDB and saves them to SQLite without re-calculating the embeddings.

```python
import json
import sqlite3
import chromadb
import sqlite_vec
from sqlite_vec import serialize_float32

CHROMA_DIR = "/data/chroma_db"
SQLITE_DB = "/data/rag.db"

def migrate():
    print("Starting migration from ChromaDB to sqlite-vec...")
    chroma_client = chromadb.PersistentClient(path=CHROMA_DIR)
    
    conn = sqlite3.connect(SQLITE_DB)
    sqlite_vec.load(conn)
    
    # Create Tables
    conn.execute("""
        CREATE TABLE IF NOT EXISTS rag_items (
            id TEXT PRIMARY KEY,
            collection_name TEXT NOT NULL,
            user_id TEXT NOT NULL,
            content TEXT NOT NULL,
            metadata TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            indexed_at TEXT NOT NULL
        )
    """)
    conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS vec_rag_items USING vec0(id TEXT PRIMARY KEY, embedding float[384])")
    
    collections = ["nextcloud_files", "ha_entities", "system_capabilities", "system_learnings"]
    for name in collections:
        try:
            coll = chroma_client.get_collection(name=name)
            data = coll.get(include=["documents", "metadatas", "embeddings"])
            
            if not data or not data["ids"]:
                print(f"Collection '{name}' is empty. Skipping.")
                continue
                
            print(f"Migrating {len(data['ids'])} items from '{name}'...")
            for doc_id, doc, meta, emb in zip(data["ids"], data["documents"], data["metadatas"], data["embeddings"]):
                user_id = meta.get("user_id", "default").lower()
                created_at = meta.get("created_at", 0)
                indexed_at = meta.get("indexed_at", "")
                
                conn.execute(
                    "INSERT OR REPLACE INTO rag_items VALUES (?, ?, ?, ?, ?, ?, ?)",
                    [doc_id, name, user_id, doc, json.dumps(meta), created_at, indexed_at]
                )
                conn.execute(
                    "INSERT OR REPLACE INTO vec_rag_items VALUES (?, ?)",
                    [doc_id, serialize_float32(emb)]
                )
            conn.commit()
            print(f"Collection '{name}' migrated successfully.")
        except Exception as e:
            print(f"Failed to migrate collection '{name}': {e}")
            
    print("Migration complete!")

if __name__ == "__main__":
    migrate()
```

---

## 6. Telemetry Semantic Summary Integration

High-frequency, raw time-series telemetry data (e.g. power logs, battery volts) should remain in the **Identity Service** for rendering graphs. To allow Jarvis to search and recall telemetry events, we will ingest **processed semantic summaries** into the RAG database under a new collection.

### A. Integration Roadmap
1. **Define a New Collection:** Register `"telemetry_alerts"` in the active RAG collections list.
2. **Generate Natural Language Alerts:**
   When the telemetry worker in the execution or identity service detects a threshold crossing (e.g., connectivity loss, high power draw, fast battery drain), it compiles the event into a text summary:
   * **Alert Content:** `"Telemetry Alert: robot_vacuum battery dropped 90% in 5 minutes at 2026-07-12T19:00:00Z."`
   * **Metadata:** `{"entity_id": "vacuum.robot", "alert_type": "battery_drain", "severity": "high", "user_id": "admin"}`
3. **Ingest to RAG:**
   Call `/rag/ingest` with the generated alert text and metadata.

### B. SQLite Relational Search Example
Because SQLite is relational, we can easily join the alert search results with our device registry or run target lookups:
```sql
SELECT 
    i.content, 
    i.indexed_at,
    v.distance
FROM vec_rag_items v
JOIN rag_items i ON v.id = i.id
WHERE i.collection_name = 'telemetry_alerts'
  AND (i.user_id = :user_id OR i.user_id = 'default')
ORDER BY vec_distance_cosine(v.embedding, :query_vector)
LIMIT :k;
```

---

## 7. Verification Plan

1. **Verify Extension Loading:**
   Verify `sqlite-vec` can be successfully loaded in the Python context of the `sharedllm_rag` container.
2. **Execute Local Unit Tests:**
   Ensure `pytest services/rag/tests/` passes cleanly.
3. **Verify Deployment & Re-sync:**
   Confirm integration contracts between Gateway, Storage, and RAG services hold up under the new backend.
