# SharedLLM Tools

This directory contains utility scripts for debugging, testing, and managing the SharedLLM application.

## Directory Structure

### `diagnostics/`

Scripts to inspect the state of the system, database, and integrations.

- `inspect_entity.py`: Dump ChromaDB metadata for a specific entity or search query.
- `inspect_ma_players.py`: targeted inspection of Music Assistant player attributes.
- `list_devices_by_group.py`: List all devices in a specific group.
- `inspect_all_players.py`: Dump all media players found in ChromaDB.

### `tests/`

Scripts to verify system functionality.

- `runner.py`: **primary test entry point**. Orchestrates specific suites or all tests.
  - Usage: `python -m app.tests.runner [--test MediaTests] [--url http://...]`
- `test_media_playback.py`: Comprehensive test suite for Media Control
  (NL queries -> Intent -> Execution).
- `test_ha_connectivity.py`: Verify connection to Home Assistant API.
- `test_ma_types.py`: Test specific Music Assistant media types (artist/track/etc).
- `test_volume.py`: Test volume control INTENT resolution (not execution).

### `utils/`

Helper scripts for ops and maintenance.

- `deploy_remote.sh`: Deploy code to the remote production server.
- `fetch_logs.py`: Fetch and filter logs from the remote container.
- `reset_chroma_db.py`: **DANGER** Wipes and resets the ChromaDB vector store.

### `legacy/`

Old or reference code.
