# RAG Vector Store: SQLite + `sqlite-vec`

Reference for the RAG service's storage (`services/rag/db.py`,
`services/rag/store.py`). The ChromaDB → `sqlite-vec` migration this file was
written to plan is **done**: `services/rag/requirements.txt` has `sqlite-vec`
and no ChromaDB. (The file name is kept because `services/rag/main.py` cites
it.)

## Why

ChromaDB cost too much RAM on the server (Intel N150, 16 GB) for what RAG
needs. One SQLite file with the `sqlite-vec` extension does exact KNN, keeps
the relational data next to the vectors, and needs no separate process.

## Where

- Database: `RAG_DB_PATH` (default `/data/rag.db`), WAL mode, 10 s busy
  timeout.
- Embeddings: fastembed, default model `nomic-ai/nomic-embed-text-v1.5`
  (768-d). The **dimension is detected at startup** from the active model,
  never hard-coded; the model name is stored in `rag_meta`, and a model change
  triggers a full re-embed from `rag_items` (the source of truth).

## Tables

| Table | What |
|---|---|
| `rag_items` | Every document: `id`, `collection_name`, `user_id`, `content`, `metadata` (JSON), timestamps, usage/applied counters, lesson columns (`rule`, `root_cause`, `outcome`, `confidence`, `supersedes`) |
| `vec_rag_items` | `vec0(id text primary key, embedding float[dim], collection_name text, user_id text)`: one vector per item; collection and user are metadata columns so KNN is filtered in the same query |
| `rag_fts` | FTS5 keyword index (BM25) used alongside the vector search; maintained on write, best effort |
| `vec_store` | Plain-table vectors used only by the numpy fallback |
| `mission_history`, `conversation_memory`, `network_topology`, `telemetry_alerts` | Relational-vector use cases (post-mortems, intercom memory, service topology, alert summaries) |
| `rag_meta` | Key/value, e.g. the embedding model that built the index |

`id` is the vec0 **primary key**: as a plain metadata column, the
`DELETE … WHERE id = ?` in every upsert scanned all vectors (~50 ms at 4k
rows) and a 700-entity Home Assistant sync blocked the event loop for over a
minute. Older tables (no metadata columns, or `id` not the key) are rebuilt in
place at startup, keeping their vectors (`_migrate_vec_rag_items_schema`).

## Search

```sql
SELECT id, distance FROM vec_rag_items
WHERE embedding MATCH ? AND collection_name = ? AND user_id IN (?, 'default')
ORDER BY distance LIMIT ?
```

`'default'` rows are shared (e.g. protocol lessons). Results are joined back
to `rag_items` for content and metadata, and combined with BM25 keyword hits
from `rag_fts`.

## Adapters (`store.py`)

`build_adapter(conn)` returns `SqliteVecAdapter` when the extension loads, else
`NumpyVecAdapter` (brute-force cosine over `vec_store`). `/health` reports
which one is active (`backend: sqlite-vec | numpy`).

## Writes

- Single items: `_add_item()` upserts `rag_items`, the vector and the FTS row.
- Batch endpoints (`/rag/sync/devices`, file chunks, capabilities) embed in a
  worker thread in cost-bounded batches (`embed_batched`) and **commit once**
  per batch, so a large sync costs one disk sync, not one per item.

## Row owners

`rag_items.id` is the table's primary key, so an id must be unique per owner:

- Home devices (collection `home_devices`): `dev:{source}:{user}:{entity_id}`,
  where `source` is the home hub (`homehub` or `home_assistant`), also stored in
  the row's metadata. The gateway's background loop syncs every user's devices
  from each hub every ~5 min; unchanged text keeps its vector and only metadata
  is refreshed, and a sync from one hub never removes another hub's rows. Each
  user and hub also has a `sync_status:{source}:{user}` row. Rows nobody
  refreshes or uses age out (see `docs/BACKGROUND_JOBS.md`, device index
  upkeep). History: the id was `ha:{entity_id}` for everyone (each user's sync
  replaced the previous user's rows), then `ha:{user}:{entity_id}` in a
  collection called `ha_entities`. RAG moves rows in that form to the new
  collection and id once at startup, keeping their vectors and counters
  (`_migrate_legacy_device_rows`, logged with counts). The old collection name
  is still accepted wherever a caller names a collection and means
  `home_devices`.
- Nextcloud and Calibre chunks are stored under the **Nextcloud username**
  (storage indexes with the provider's credentials), not the Jarvis username.
- `system_capabilities` and `system_learnings` live under `default` and are
  shared; search and stats always include them.

## Knowledge Hub routes (gateway)

| Route | Who | Notes |
| --- | --- | --- |
| `GET /api/search` | signed in | Searches the caller's Jarvis + Nextcloud rows and `default`; each hit has `snippet` and `collection`. |
| `GET /api/storage/stats` | signed in | Counts for both usernames; shared `system_*` collections counted once. |
| `GET /api/storage/status` | signed in | Indexer state and the running crawl (`crawl.phase`, `done/total`, `error`). |
| `POST /api/storage/list`, `/index` | signed in | Uses the caller's Nextcloud credentials; indexing runs only when started (no schedule). |
| `POST /api/storage/purge/{collection}` | signed in | Deletes the caller's rows (optionally narrowed by a `filter`) under both usernames; returns `removed`. |
| `GET /api/storage/learning` | signed in | Raven lessons. |
| `PATCH`/`DELETE /api/storage/learning/{id}` | admin | RAG refuses ids outside `system_learnings` (404). |
| `GET /api/storage/collection/{name}` | admin | Admin page's raw collection inspector. |

## Tests

`services/rag/tests/`: schema migrations (`test_rag_search.py`), batch
embedding, single-commit and per-user device syncs (`test_ha_sync_embed_batches.py`), hubs
kept apart, the rename, references and the staleness sweep (`test_home_devices_index.py`), cost
budget, curriculum. The `sqlite_vec`-dependent tests skip where the extension
is not installed.
