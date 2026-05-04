# Storage Service

The Storage service is the provider bridge between SharedLLM and external file
stores such as Nextcloud. Its current responsibilities are:

- list provider entries
- search provider entries by filename terms
- write content back to supported provider paths
- classify discovered items for indexing
- extract text content for supported file types
- sync extracted chunks to the RAG service

It does not manage git state or act as the canonical runtime for repository
operations. Local workspaces remain authoritative for active code changes.

## Current API

| Endpoint | Method | Description |
| :--- | :--- | :--- |
| `/health` | GET | Service health check. |
| `/providers/list` | POST | List entries from a provider path. |
| `/providers/search` | POST | Search entries by query string. |
| `/providers/write` | POST | Write text content to a provider path. |
| `/index/full` | POST | Start a background scan, extraction, and RAG sync job. |
| `/index/pause` | POST | Pause active indexing work for up to 60 seconds. |
| `/index/resume` | POST | Resume indexing work immediately. |

## Provider Support

Current provider support:

- `nextcloud` via WebDAV

The provider interface is intentionally narrow today:

- `list_entries(path, recursive)`
- `get_content(path)`
- `write_content(path, content, create_parents, verify)`

## Writeback Behavior

`/providers/write` is the thin writeback surface used by `workspace_runtime`
when a local authoritative file must also be reflected into a designated
provider folder.

For `nextcloud`, the current implementation:

1. ensures parent directories exist with WebDAV `MKCOL`
2. uploads text content with `PUT`
3. optionally re-reads the file to verify the written content matches

This is intentionally modular: other providers such as Google Drive or OneDrive
should implement the same provider method rather than introducing provider
logic into `workspace_runtime`.

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
actual source of truth for code edits remains a local git checkout. For active
coding workflows, use a mapped workspace and run file edits, tests, diffs, and
git operations against that checkout, then explicitly sync selected outputs back
to the designated provider path.

## Tests

Relevant tests live under:

- `services/tests/test_storage.py`
- `services/tests/test_storage_advanced.py`
