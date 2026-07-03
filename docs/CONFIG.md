# SharedLLM Configuration

## Configuration Architecture

SharedLLM uses a **three-layer configuration model**:

1. **Special Variables** (`.env` file): `INTERNAL_SECRET` and `FERNET_KEY` are required at startup for authentication/encryption. Validated at import time.
2. **Bootstrap Variables** (`.env` file): `IDENTITY_SVC_URL` is read to contact Identity service (defaults to `http://identity:8001`).
3. **Runtime Phase** (Identity service): All other configuration values are fetched from Identity service via `resolve_runtime_config()` at service startup.

**.env is seed-only.** It is never consulted for runtime values after the service has started. Special variables may be updated in `.env` if changed via the Identity UI.

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

### Special Variables (Required at Bootstrap)

These variables are used for authentication/encryption and must be set in `.env`:

| Variable | Description |
| ----------- | ------------- |
| `INTERNAL_SECRET` | Service-to-service authentication token (required) |
| `FERNET_KEY` | Encryption key for stored credentials (required) |

If either is missing, the service refuses to start with a fatal error.

If these values are changed via the Identity UI, the `.env` file is automatically updated to match.

### Required Variables (Bootstrap)

| Variable | Description |
| ----------- | ------------- |
| `IDENTITY_SVC_URL` | Identity service URL (defaults to `http://identity:8001`) |

If `IDENTITY_SVC_URL` is missing, the service uses the default value.

### Runtime Variables (Optional)

All other configuration is stored in the Identity service's database and fetched at startup:

| Setting Key | Config Variable | Description |
| ------------- | --------------- | ------------- |
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
| `redis_url` | `REDIS_URL` | Redis connection string (infrastructure-dependent) |
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
| `log_retention_days` | `LOG_RETENTION_DAYS` | Log retention in days |
| `log_max_entries` | `LOG_MAX_ENTRIES` | Maximum log entries |
| `raven_max_total_seconds` | `RAVEN_MAX_TOTAL_SECONDS` | Raven total timeout |
| `raven_iteration_timeout` | `RAVEN_ITERATION_TIMEOUT` | Raven iteration timeout |
| `raven_heartbeat_interval` | `RAVEN_HEARTBEAT_INTERVAL` | Raven heartbeat interval |
| `raven_hung_threshold` | `RAVEN_HUNG_THRESHOLD` | Raven hung threshold |
| `raven_error_threshold` | `RAVEN_ERROR_THRESHOLD` | Raven error threshold |
| `raven_check_interval` | `RAVEN_CHECK_INTERVAL` | Raven check interval |
| `default_model` | `DEFAULT_MODEL` | Default LLM model |
| `workspace_root` | `WORKSPACE_ROOT` | Workspace root path |
| `workspace_runtime_root` | `WORKSPACE_RUNTIME_ROOT` | Workspace runtime root |
| `audiobookshelf_url` | `AUDIOBOOKSHELF_URL` | Audiobookshelf URL |
| `audiobookshelf_user` | `AUDIOBOOKSHELF_USER` | Audiobookshelf username |
| `audiobookshelf_pass` | `AUDIOBOOKSHELF_PASS` | Audiobookshelf password |
| `upstream_dns` | `UPSTREAM_DNS` | Upstream DNS server |
| `dns_poll_interval` | `DNS_POLL_INTERVAL` | DNS poll interval |

### Runtime Resolution

`resolve_runtime_config()` is called at service startup. It:

1. Fetches all settings from `GET {IDENTITY_SVC_URL}/api/settings`
2. Maps each setting key to the corresponding module-level variable
3. Performs type coercion for numeric values (int, float)
4. Logs success or warning on failure

**Runtime variables are optional.** If Identity is unavailable at startup, variables retain their empty-string or default values. Services should handle missing values gracefully at runtime.

### Special Variables Sync

If `INTERNAL_SECRET` or `FERNET_KEY` are changed via the Identity UI, they are automatically synced to the `.env` file. This ensures all services have access to the current values.

## How Services Use Config

Each service imports from `services/config.py`:

```python
from config import INTERNAL_SECRET, IDENTITY_SVC_URL, resolve_runtime_config

@app.on_event("startup")
async def startup():
    await resolve_runtime_config()
    # Now all config variables are populated from Identity
```

## Why This Architecture?

1. **Single source of truth**: Identity holds all runtime configuration
2. **Hot-reloadable**: Settings can be changed via UI and picked up on next restart
3. **Secure**: Secrets (tokens, passwords) are encrypted in Identity's database
4. **Container-safe**: No need to rebuild images or restart with new env vars
5. **Audit trail**: All config changes go through Identity's API

## .env File Purpose

The `.env` file exists **only for bootstrapping**:

- Seeding initial `INTERNAL_SECRET`, `FERNET_KEY`, and `IDENTITY_SVC_URL`
- Docker Compose uses it to inject these values into containers
- Special variables may be updated if changed via Identity UI

**Never add new runtime variables to `.env` for runtime configuration.** If a service needs a new setting, add it to:

1. `services/config.py` (with a default value)
2. `resolve_runtime_config()` settings map
3. The Identity settings UI or API

## Testing with Seeded Values

For testing (e.g., E2E integration tests), create a `test.env` file with hardcoded values:

```env
INTERNAL_SECRET=test-secret-ci
FERNET_KEY=g13l5bpIeVaVe4ri66RE0bPYpB9IjCYdObQAKJU2Z14=
IDENTITY_SVC_URL=http://identity:8001
EXECUTION_SVC_URL=http://execution:8012
HA_URL=https://ha.sumemail.com:8095
WORKSPACE_ROOT=/workspaces
```

Mount this `.env` file in the Identity Service container to seed initial values into the database.

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
