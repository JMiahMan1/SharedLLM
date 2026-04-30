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
- **Content Indexer**: Classifies repositories, notes, documents, ebooks, images,
  audio, and video into capability-aware index entries.
- **Librarian Engine**: Uses that index to decide which tools can summarize,
  parse, transcribe, preview, or search each item.
- **Future Backends**: Designed to extend to other open-source and proprietary
  file stores without changing downstream consumers.

## Testing & Diagnostics

### Integration Smoke Test
Run the end-to-end test script to verify all services are communicating:
```bash
python3 services/tests/soa_smoke_test.py
```

### Global Error Handling
All services implement a global exception handler that returns detailed tracebacks in the `detail` field of 500 responses, facilitating rapid debugging without manual log diving.

### Forced Re-seeding
If environment variables change in the legacy `.env`, trigger a forced re-seed:
```bash
curl -X POST "http://localhost:8001/api/admin/seed?force=true" -H "X-Internal-Secret: your-secret"
```
