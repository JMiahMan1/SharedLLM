# Storage Service

The Storage Service acts as a bridge between SharedLLM and external data stores like NextCloud. It provides listing, searching, and deep content indexing capabilities.

## Features

### 1. Unified Provider Interface
Supports multiple storage providers (currently NextCloud) via a common `StorageProvider` abstraction.

### 2. Deep Content Indexing
The `/index/full` endpoint performs an end-to-end knowledge ingestion:
1.  **Structure Scan**: Identifies all files and directories recursively.
2.  **Classification**: Categorizes files (e.g., Markdown, PDF, Source Code) using `indexer.py`.
3.  **Content Extraction**: Downloads and reads text-based files.
4.  **Semantic Chunking**: Splits large files into overlapping snippets (default 1000 chars, 200 overlap).
5.  **RAG Synchronization**: Pushes chunks to the RAG service for vector search.

### 3. Checkpointing & Performance
*   **Progress Tracking**: Uses `/data/index_checkpoint.json` to store hashes/mtimes of indexed files. It will skip unchanged files on subsequent runs.
*   **Resource Prioritization**: Includes `/index/pause` and `/index/resume` endpoints. The Gateway automatically pauses indexing during heavy LLM inference to ensure maximum performance for user interactions.

### 4. Automated Cleanup
Uses a `session_id` strategy during indexing. Any files previously in the RAG service that are no longer present in the latest indexing session are automatically purged.

## API Endpoints

| Endpoint | Method | Description |
| :--- | :--- | :--- |
| `/health` | GET | Service health check. |
| `/providers/list` | POST | List entries from a provider path. |
| `/index/scan` | POST | Classify files without extracting content. |
| `/index/full` | POST | Full scan, extract, and sync to RAG. |
| `/index/pause` | POST | Pause active indexing jobs. |
| `/index/resume` | POST | Resume paused indexing jobs. |

## Triggering an Index
You can trigger a full library index via chat:
*   *"Index my library"*
*   *"Scan my nextcloud for new files"*
*   *"Update my knowledge base"*

## Development & Testing
Run unit tests locally:
```bash
export PYTHONPATH=$PYTHONPATH:$(pwd)
pytest tests/test_indexer.py tests/test_api.py
```
