# Storage Service Architecture

This document provides a detailed breakdown of the internal structure, communication patterns, and data models of the Storage Service.

## 📁 File Manifest & Responsibilities

### 1. `main.py` (Service Entry Point)
*   **Role**: Orchestrates the FastAPI application and defines the public API.
*   **Communication**:
    *   **Inbound**: Receives requests from the Gateway for listing, searching, and indexing.
    *   **Outbound**: Calls the RAG service (`/rag/sync/files` and `/rag/purge`) to synchronize knowledge.
*   **Endpoints**:
    *   `/health`: System status.
    *   `/index/full`: The primary "Librarian" workflow (Scan -> Extract -> Chunk -> RAG).
    *   `/nextcloud/search`: Keyword search and broad listing compatibility.
    *   `/index/pause` / `/index/resume`: Resource management toggles.

### 2. `indexer.py` (Intelligence Layer)
*   **Role**: Handles the classification and processing of storage items.
*   **Key Functions**:
    *   `build_content_index`: Converts raw entries into `ContentIndexItem`s with assigned roles (e.g., "Documentation", "Ebook").
    *   `extract_and_chunk_contents`: The heart of the knowledge system. Reads text, splits it into overlapping windows, and yields chunks.
    *   `CheckpointManager`: Handles persistent state to ensure we don't re-process unchanged files.

### 3. `providers.py` (Abstraction Layer)
*   **Role**: Defines the `StorageProvider` interface.
*   **Function**: Allows the indexer and API to remain agnostic of the underlying cloud provider. It uses a `build_provider` factory to instantiate specific clients (like NextCloud).

### 4. `nextcloud_client.py` (Implementation Layer)
*   **Role**: Wraps `easywebdav` and `requests` to talk to NextCloud.
*   **Capabilities**: Recursive listing, file downloading, and direct text content streaming via HTTP Basic Auth.

### 5. `models.py` (Data Layer)
*   **Role**: Defines the schema for all internal and external data structures using Pydantic.

---

## 🔄 Communication & Data Flow

### The "Deep Indexing" Sequence
1.  **Gateway** sends a POST to `/index/full`.
2.  **Storage** builds a `NextcloudStorageProvider`.
3.  **Storage** lists all files (`list_entries`).
4.  **Indexer** classifies files and checks the `index_checkpoint.json`.
5.  **Indexer** fetches content for new/changed files via `get_content`.
6.  **Indexer** chunks the text.
7.  **Storage** pushes chunks to **RAG Service** (`/rag/sync/files`) with a unique `session_id`.
8.  **Storage** tells **RAG Service** to purge any entries for this user that *don't* have the new `session_id`.

---

## 📊 Data Models

### `StorageEntry`
*   **Provided by**: `models.py`
*   **Data**: `path`, `name`, `is_dir`, `size`, `mtime`, `content_type`.
*   **Usage**: The raw representation of a file on a remote system.

### `ContentIndexItem`
*   **Provided by**: `indexer.py` (via `models.py`)
*   **Data**: Adds `subtype` (e.g., `git_repo`), `role`, `extractable_capabilities` (e.g., `full_text`), and `recommended_tools`.
*   **Usage**: Used by the Librarian to decide how to interact with a file.

### `KnowledgeChunk`
*   **Provided by**: `indexer.py` output
*   **Data**: `content` (string), `metadata` (path, name, user_id, session_id).
*   **Usage**: The final unit stored in the vector database for RAG.
