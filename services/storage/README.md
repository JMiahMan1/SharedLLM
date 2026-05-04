# Storage Service

The Storage service is the read-only bridge between SharedLLM and external file
stores such as Nextcloud. Its current responsibilities are:

- list provider entries
- search provider entries by filename terms
- classify discovered items for indexing
- extract text content for supported file types
- sync extracted chunks to the RAG service

It does not currently edit files, write back to Nextcloud, manage git state, or
act as the canonical runtime for repository operations.

## Current API

| Endpoint | Method | Description |
| :--- | :--- | :--- |
| `/health` | GET | Service health check. |
| `/providers/list` | POST | List entries from a provider path. |
| `/providers/search` | POST | Search entries by query string. |
| `/index/full` | POST | Start a background scan, extraction, and RAG sync job. |
| `/index/pause` | POST | Pause active indexing work for up to 60 seconds. |
| `/index/resume` | POST | Resume indexing work immediately. |

## Provider Support

Current provider support:

- `nextcloud` via WebDAV

The provider interface is intentionally narrow today:

- `list_entries(path, recursive)`
- `get_content(path)`

## Indexing Behavior

`/index/full` schedules a background task that:

1. Lists entries from the provider.
2. Filters common noise paths such as `.git`, `node_modules`, and virtualenvs.
3. Classifies files and folders into `ContentIndexItem` records.
4. Extracts text for supported content types.
5. Chunks extracted text for RAG sync.
6. Pushes chunks to the RAG service and purges stale chunks by `session_id`.

The endpoint returns immediately with:

```json
{
  "status": "SUCCESS",
  "message": "Indexing started in background."
}
```

## Repo-Aware Limitation

The service can discover repository-shaped folders as storage content, but the
actual source of truth for code edits should remain a local git checkout. For
active coding workflows, use a mapped workspace and run file edits, tests,
diffs, and git operations against that checkout rather than against synced
Nextcloud copies.

## Tests

Relevant tests live under:

- `services/tests/test_storage.py`
- `services/tests/test_storage_advanced.py`
