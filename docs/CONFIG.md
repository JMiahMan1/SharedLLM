# SharedLLM Configuration

## Configuration Architecture

SharedLLM uses a **two-phase configuration model**:

1. **Bootstrap Phase** (`.env` file): Only `INTERNAL_SECRET` and `IDENTITY_SVC_URL` are read from the environment at startup. These are the minimum required to contact the Identity service.
2. **Runtime Phase** (Identity service): All other configuration values are fetched from the Identity service's settings store via `resolve_runtime_config()` at service startup.

**.env is seed-only.** It is never consulted for runtime values after the service has started.

## How It Works

### Bootstrap Variables (from `.env`)

| Variable | Description |
| ----------- | ------------- |
| `INTERNAL_SECRET` | Service-to-service authentication token (required) |
| `IDENTITY_SVC_URL` | Identity service URL (defaults to `http://identity:8001`) |

### Runtime Variables (from Identity Settings)

All other configuration is stored in the Identity service's database and fetched at startup:

| Setting Key | Config Variable | Description |
| ------------- | --------------- | ------------- |
| `fernet_key` | `FERNET_KEY` | Encryption key for stored credentials |
| `llm_local_url` | `OLLAMA_URL` | Ollama inference server URL |
| `execution_svc_url` | `EXECUTION_SVC_URL` | Execution bridge URL |
| `rag_svc_url` | `RAG_SVC_URL` | RAG/ChromaDB service URL |
| `storage_svc_url` | `STORAGE_SVC_URL` | Storage service URL |
| `logging_svc_url` | `LOGGING_SVC_URL` | Logging service URL |
| `workspace_runtime_svc_url` | `WORKSPACE_RUNTIME_SVC_URL` | Workspace runtime URL |
| `control_plane_url` | `CONTROL_PLANE_URL` | Docker control plane service |
| `searxng_url` | `SEARXNG_URL` | Web search engine URL |
| `ha_url` | `HA_URL` | Home Assistant URL |
| `ha_token` | `HA_TOKEN` | Home Assistant long-lived token |
| `nextcloud_url` | `NEXTCLOUD_URL` | Nextcloud instance URL |
| `nextcloud_user` | `NEXTCLOUD_USER` | Nextcloud username |
| `nextcloud_pass` | `NEXTCLOUD_PASS` | Nextcloud password |
| `git_url` | `GIT_URL` | Git remote URL |
| `git_user` | `GIT_USER` | Git username |
| `git_token` | `GIT_TOKEN` | Git personal access token |
| `redis_url` | `REDIS_URL` | Redis connection string |
| `assistant_model` | `ASSISTANT_MODEL` | General assistant model |
| `coding_model` | `CODING_MODEL` | Code/coding model |
| `librarian_model` | `LIBRARIAN_MODEL` | Document/knowledge model |
| `default_tts_voice` | `DEFAULT_TTS_VOICE` | Default TTS voice |
| `mass_config_entry_id` | `MASS_CONFIG_ENTRY_ID` | Music Assistant config entry |
| `abs_url` | `ABS_URL` | Audiobookshelf URL |
| `abs_api_key` | `ABS_API_KEY` | Audiobookshelf API key |
| `embedding_model` | `EMBEDDING_MODEL` | Embedding model name |
| `fast_path_threshold` | `FAST_PATH_THRESHOLD` | Semantic router confidence |
| `execution_external_host` | `EXECUTION_EXTERNAL_HOST` | External host for execution |

### Constants (hardcoded defaults)

These are not configurable via Identity; they are container/install paths:

| Variable | Default | Description |
| ----------- | --------- | ------------- |
| `MODELS_DIR` | `/app/models` | Kokoro TTS models directory |
| `TEMP_MEDIA_DIR` | `/tmp/sharedllm_media` | Temp media directory |
| `WORKSPACE_ROOT` | `/workspaces` | Workspace root |
| `CHROMA_PERSIST_DIR` | `/data/chroma_db` | ChromaDB storage |

## How Services Use Config

Each service imports from `services/config.py`:

```python
from config import INTERNAL_SECRET, IDENTITY_SVC_URL, resolve_runtime_config

@app.on_event("startup")
async def startup():
    await resolve_runtime_config()
    # Now all config variables are populated from Identity
```

### Required Variables

Only `INTERNAL_SECRET` is required at bootstrap. If missing, the service refuses to start with a fatal error.

### Runtime Resolution

`resolve_runtime_config()` is called at service startup. It:

1. Fetches all settings from `GET {IDENTITY_SVC_URL}/api/settings`
2. Maps each setting key to the corresponding module-level variable
3. Performs type coercion for numeric values (int, float)
4. Logs success or warning on failure

If Identity is unavailable at startup, variables retain their empty-string or default values. Services should handle missing values gracefully at runtime.

## Why This Architecture?

1. **Single source of truth**: Identity holds all runtime configuration
2. **Hot-reloadable**: Settings can be changed via UI and picked up on next restart
3. **Secure**: Secrets (tokens, passwords) are encrypted in Identity's database
4. **Container-safe**: No need to rebuild images or restart with new env vars
5. **Audit trail**: All config changes go through Identity's API

## .env File Purpose

The `.env` file exists **only for bootstrapping**:

- Seeding the initial `INTERNAL_SECRET`
- Providing `IDENTITY_SVC_URL` so the service can contact Identity
- Docker Compose uses it to inject these two values into containers

**Never add new variables to `.env` for runtime configuration.** If a service needs a new setting, add it to:

1. `services/config.py` (with a default value)
2. `resolve_runtime_config()` settings map
3. The Identity settings UI or API
