# SharedLLM SOA Architecture

This directory contains the microservices refactor of the SharedLLM system.

## Services

### 1. Gateway (`services/gateway`)
- **Role**: Entry point for all chat and device requests.
- **Intent Engine**: Uses a semantic router (`sentence-transformers`) to classify user queries into intents (`turn_on`, `turn_off`, `set_brightness`, etc.).
- **Fast Path**: High-confidence commands bypass the LLM and execute directly on the Execution Bridge for sub-second latency.
- **Smart Context Injection**: For queries handled by the LLM (Slow Path), the Gateway fetches real-time device states and attributes from Home Assistant and injects them into the system prompt.
- **Proxying**: Transparently proxies Ollama/OpenAI requests for OpenWebUI compatibility.

### 2. Execution Bridge (`services/execution`)
- **Role**: Stateless wrapper for third-party APIs (Home Assistant, Nextcloud, etc.).
- **Discovery**: Provides a `/discovery/entities` endpoint for the Gateway to fetch real-time hardware state.
- **Control**: Handles domain-specific execution (Lights, Media, Announcements).

### 3. Identity Service (`services/identity`)
- **Role**: Manages user profiles and secure credential resolution.
- **Resolution**: Maps `voice_id`, `rag_user`, or `device_id` to decrypted HA/Nextcloud credentials.

## Future Vision: Omni-Source Expansion

### 4. Storage Bridge (`services/storage`)
- **Provider Layer**: Normalizes multiple file stores behind a shared interface.
- **Initial Backend**: Nextcloud via WebDAV.
- **Writeback**: Supports explicit provider writeback so local authoritative
  workspace changes can be reflected into a designated provider folder.
- **Content Indexer**: Classifies repositories, notes, documents, ebooks, images,
  audio, and video into capability-aware index entries.
- **Librarian Engine**: Uses that index to decide which tools can summarize,
  parse, transcribe, preview, or search each item.
- **Future Backends**: Designed to extend to other open-source and proprietary
  file stores without changing downstream consumers.

### 5. Workspace Runtime (`services/workspace_runtime`)
- **Role**: Sandboxed local workspace runtime for code, notes, documents, and related workspace tasks.
- **Capabilities**: Resolves registered workspaces, reads files safely, reports
  `git status`, returns diffs, stages and commits local changes, scans the
  designated provider folder, syncs selected files back to that provider path,
  and runs targeted `pytest` commands.
- **Boundary**: Uses mounted local workspaces as the authoritative source for
  code state rather than provider-synced snapshots, while provider sync is done
  explicitly through the Storage provider abstraction.

### 6. Geo Service (`services/geo`)
- **Role**: Life360-style family location / mapping layer. Self-hosted, privacy-respecting replacement for Google Maps location history and Life360 sharing on the de-Googled phone.
- **Backend**: Wraps **Home Assistant** (already running) — reads `person`, `device_tracker`, and `zone` entity states over the HA REST API (HA_URL / HA_TOKEN resolved at boot from Identity), exposes them as GeoJSON for a MapLibre client, and accepts location pushes via the HA `device_tracker.see` service.
- **Map rendering**: OSM is the map *data* (no serious FOSS rival); **MapLibre GL JS** is the open *renderer*. Tile/style source: Protomaps (PMTiles) or OpenMapTiles, self-hostable.
- **Endpoints**: `GET /people` (GeoJSON of people/trackers), `GET /zones` (geofences), `GET /people/{id}/history` (trip replay from HA Recorder), `POST /people/{id}/see` (push location, internal-secret guarded).
- **Upgrade path**: Traccar (Apache-2) is the documented drop-in if HA's sharing/geofence UX proves insufficient (HA has a `traccar_server` integration).
- **Design doc**: `docs/GEO_SERVICE.md` (and `S26-Setup/geo-service/README.md`).

## Credential Resolution Architecture

The `.env` file is a **seed-only** artifact. It must NEVER be read directly by any service, test, or script at runtime.

### Rule: Identity Is the Single Source of Truth

| Component | .env Access | Runtime Credentials |
|-----------|-------------|---------------------|
| **Identity Service** | ✅ Reads `.env` **only** during initial seed (`/api/admin/seed`) | Stores encrypted credentials in its database |
| **All other services** | ❌ Never reads `.env` | Resolves credentials via `POST /api/resolve` to Identity |
| **Tests** | ❌ Never reads `.env` | Uses mocks or `PYTEST_CURRENT_TEST` placeholders |

### How It Works

1. **Seeding**: On first boot (or forced re-seed), Identity reads integration URLs/tokens from `.env` and stores them encrypted in its SQLite database.
2. **Resolution**: Any service needing credentials calls Identity's `/api/resolve` endpoint with a `rag_user` identifier, passing the `X-Internal-Secret` header. Identity returns decrypted `ha_url`, `ha_token`, `nextcloud_*`, etc.
3. **Runtime**: Services use resolved credentials for API calls. The `.env` file is irrelevant after seeding.

### Example

```python
# ✅ Correct: Resolve credentials from Identity
creds = await resolve_internal_user("default")
ha_url = creds["ha_url"]
ha_token = creds["ha_token"]

# ❌ Wrong: Read from config.py / .env
from config import HA_URL, HA_TOKEN  # Only for seed fallback, never production
```

### Forced Re-seeding

If environment variables change in the legacy `.env`, trigger a forced re-seed:
```bash
curl -X POST "http://localhost:8001/api/admin/seed?force=true" -H "X-Internal-Secret: your-secret"
```

## Testing & Diagnostics

### Integration Smoke Test
Run the end-to-end test script to verify all services are communicating:
```bash
python3 services/tests/soa_smoke_test.py
```

### Global Error Handling
All services implement a global exception handler that returns detailed tracebacks in the `detail` field of 500 responses, facilitating rapid debugging without manual log diving.
