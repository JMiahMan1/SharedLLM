# Librarian Indexer Specification

This document defines the core logic and interface requirements for the Librarian Indexer component.

## 1. Classification Engine
The indexer must categorize every storage entry into a rich metadata model.

### A. File Classification (`FILE_RULES`)
*   **Static Mapping**: Extensions (e.g., `.md`, `.txt`, `.pdf`) must be mapped to specific `item_type`, `subtype`, and `capabilities`.
*   **Fallback Logic**: 
    *   If no rule exists, detect `binary` vs `document` based on `content-type` or suffix.
    *   Unknown text files should default to `full_text` capability.
    *   Unknown binary files should default to `metadata_only`.

### B. Directory Classification
*   **Heuristics**: Folders should be classified by their contents (e.g., a folder with >3 `.md` files is a `document_collection`).
*   **Structure Scan**: Must identify structural roles (e.g., `notes_vault` if `.obsidian` is present).

## 2. Extraction & Chunking
*   **Stream Processing**: Content is fetched via the provider's `get_content` method.
*   **Chunking**:
    *   Standard Size: 1000 characters.
    *   Standard Overlap: 200 characters.
*   **Metadata Injection**: Every chunk must carry its source path, name, and sequence index.

## 3. Checkpointing (`CheckpointManager`)
*   **Efficiency**: Must skip extraction if the file's `mtime` hasn't changed since the last successful index.
*   **Persistence**: Checkpoints must be saved to `index_checkpoint.json`.
*   **Robustness**: Must automatically create the checkpoint directory if it is missing.

## 4. Operational Controls
*   **Pause/Resume**: The extraction loop must respect a global `INDEXER_PAUSED` flag, enabling the system to prioritize LLM inference over background indexing.
*   **Background Execution**: Intensive indexing tasks must run in a background thread/task to avoid blocking the API's responsiveness.

## 5. Interface Alignment
The indexer outputs `ContentIndexItem` objects and lists of chunk dictionaries:
```python
{
    "content": "...",
    "metadata": {
        "path": "/...",
        "name": "...",
        "chunk_index": 0,
        "item_type": "...",
        "subtype": "..."
    }
}
```
