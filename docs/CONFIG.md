# SharedLLM Configuration

## Centralized Config

All environment variables are defined in a single source of truth: `services/config.py`.

No service should use `os.getenv()` with hardcoded defaults. Required variables fail fast at startup.

### Required Variables

These **must** be set in `.env` or the container environment. Services will refuse to start if missing:

| Variable | Description |
|----------|-------------|
| `INTERNAL_SECRET` | Service-to-service auth token |
| `FERNET_KEY` | Encryption key for stored credentials |
| `OLLAMA_URL` | Ollama inference server URL |
| `IDENTITY_SVC_URL` | Identity service URL |
| `EXECUTION_SVC_URL` | Execution bridge URL |
| `RAG_SVC_URL` | RAG/ChromaDB service URL |
| `STORAGE_SVC_URL` | Storage service URL |
| `LOGGING_SVC_URL` | Logging service URL |
| `WORKSPACE_RUNTIME_SVC_URL` | Workspace runtime URL |
| `SEARXNG_URL` | Web search engine URL |

### Optional Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `CONTROL_PLANE_URL` | `""` | Docker control plane service |
| `LLAMA_SERVER_PROXY_URL` | `""` | LLaMA server for large GGUF models |
| `HA_URL` | `""` | Home Assistant URL |
| `HA_TOKEN` | `""` | Home Assistant long-lived token |
| `NEXTCLOUD_URL` | `""` | Nextcloud instance URL |
| `NEXTCLOUD_USER` | `""` | Nextcloud username |
| `NEXTCLOUD_PASS` | `""` | Nextcloud password |
| `GIT_URL` | `""` | Git remote URL |
| `GIT_USER` | `""` | Git username |
| `GIT_TOKEN` | `""` | Git personal access token |
| `REDIS_URL` | `redis://redis:6379/0` | Redis connection string |
| `TIMEZONE` | `America/New_York` | System timezone |
| `EMBEDDING_MODEL` | `BAAI/bge-small-en-v1.5` | Embedding model name |
| `DEFAULT_TTS_VOICE` | `af_heart` | Default TTS voice |
| `FAST_PATH_THRESHOLD` | `0.85` | Semantic router confidence threshold |

### Runtime Paths

| Variable | Default | Description |
|----------|---------|-------------|
| `MODELS_DIR` | `/app/models` | Directory for Kokoro TTS models |
| `TEMP_MEDIA_DIR` | `/tmp/sharedllm_media` | Temp directory for media files |
| `SCRIPTS_DIR` | `/app/scripts` | Scripts directory |
| `LEGACY_ENV_PATH` | `/app/.env.legacy` | Legacy .env file path |
| `WORKSPACE_ROOT` | `/workspaces` | Workspace root directory |
| `WORKSPACE_REGISTRY_PATH` | `/data/workspaces.json` | Workspace registry file |
| `WORKSPACE_DATABASE_URL` | `sqlite:////data/workspace.db` | Workspace runtime database |
| `VOLUME_MANIFEST_PATH` | `/data/volumes.json` | Docker volume manifest |
| `VOLUME_BACKUP_ROOT` | `/data/backups` | Volume backup root |
| `CHROMA_PERSIST_DIR` | `/data/chroma_db` | ChromaDB persistent storage |
| `IDENTITY_DATABASE_URL` | `sqlite:////data/identity.db` | Identity service database |
| `DNS_CONF_PATH` | `/etc/dnsmasq.conf` | dnsmasq config path |

### Raven Autonomous Agent

| Variable | Default | Description |
|----------|---------|-------------|
| `RAVEN_MAX_TOTAL_SECONDS` | `1800` | Max total mission time (30 min) |
| `RAVEN_ITERATION_TIMEOUT` | `600` | Max per-iteration time (10 min) |
| `RAVEN_HEARTBEAT_INTERVAL` | `30` | Heartbeat check interval |
| `RAVEN_HUNG_THRESHOLD` | `600` | Time before mission considered hung |
| `RAVEN_ERROR_THRESHOLD` | `5` | Max errors before mission abort |
| `RAVEN_CHECK_INTERVAL` | `300` | Interval between mission checks (5 min) |

### Logging

| Variable | Default | Description |
|----------|---------|-------------|
| `LOG_RETENTION_DAYS` | `30` | Days to retain logs |
| `LOG_MAX_ENTRIES` | `10000` | Max log entries in Redis |

### Model Mapping

These are seed defaults. Runtime values are managed via the Identity settings UI:

| Variable | Description |
|----------|-------------|
| `DEFAULT_MODEL` | Default LLM model |
| `ASSISTANT_MODEL` | General assistant model |
| `CODING_MODEL` | Code/coding model |
| `LIBRARIAN_MODEL` | Document/knowledge model |

## How Services Use Config

Each service imports from `services/config.py`:

```python
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from config import INTERNAL_SECRET, EXECUTION_SVC_URL, REDIS_URL
```

Required variables trigger `sys.exit(1)` if unset. Optional variables return their default or empty string.

## .env vs Identity Settings

- `.env` is for **bootstrap secrets and endpoints** only
- Runtime values (models, thresholds, paths) are managed via the **Identity Settings UI**
- Services fetch runtime values from Identity at startup; config provides fallbacks
