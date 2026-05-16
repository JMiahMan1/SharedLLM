import os
import sys

def _is_testing() -> bool:
    return "PYTEST_CURRENT_TEST" in os.environ or "pytest" in sys.modules

def _required(key: str) -> str:
    val = os.getenv(key)
    if not val:
        if _is_testing():
            return f"__test_placeholder_{key}__"
        import logging
        logging.basicConfig(level="CRITICAL")
        logging.critical(f"FATAL: {key} environment variable is not set. Refusing to start.")
        sys.exit(1)
    return val

def _optional(key: str, default: str = "") -> str:
    return os.getenv(key, default)

# --- Required secrets ---
INTERNAL_SECRET = _required("INTERNAL_SECRET")
FERNET_KEY = _required("FERNET_KEY")

# --- External service endpoints (bootstrap defaults from .env) ---
# Runtime values are fetched from Identity settings; these are fallbacks.
OLLAMA_URL = _required("OLLAMA_URL")
IDENTITY_SVC_URL = _required("IDENTITY_SVC_URL")
EXECUTION_SVC_URL = _required("EXECUTION_SVC_URL")
RAG_SVC_URL = _required("RAG_SVC_URL")
STORAGE_SVC_URL = _required("STORAGE_SVC_URL")
LOGGING_SVC_URL = _required("LOGGING_SVC_URL")
WORKSPACE_RUNTIME_SVC_URL = _required("WORKSPACE_RUNTIME_SVC_URL")

# --- Optional external services ---
CONTROL_PLANE_URL = _optional("CONTROL_PLANE_URL")
SEARXNG_URL = _optional("SEARXNG_URL") or _optional("WHOOGLE_URL")
LLAMA_SERVER_PROXY_URL = _optional("LLAMA_SERVER_PROXY_URL")

# --- Integration endpoints ---
HA_URL = _optional("HA_URL")
HA_TOKEN = _optional("HA_TOKEN")
NEXTCLOUD_URL = _optional("NEXTCLOUD_URL")
NEXTCLOUD_USER = _optional("NEXTCLOUD_USER")
NEXTCLOUD_PASS = _optional("NEXTCLOUD_PASS")
GIT_URL = _optional("GIT_URL")
GIT_USER = _optional("GIT_USER")
GIT_TOKEN = _optional("GIT_TOKEN")

# --- Logging ---
REDIS_URL = _optional("REDIS_URL", "redis://redis:6379/0")
LOG_RETENTION_DAYS = int(_optional("LOG_RETENTION_DAYS", "30"))
LOG_MAX_ENTRIES = int(_optional("LOG_MAX_ENTRIES", "10000"))

# --- Raven autonomous agent ---
RAVEN_MAX_TOTAL_SECONDS = int(_optional("RAVEN_MAX_TOTAL_SECONDS", "1800"))
RAVEN_ITERATION_TIMEOUT = int(_optional("RAVEN_ITERATION_TIMEOUT", "600"))
RAVEN_HEARTBEAT_INTERVAL = int(_optional("RAVEN_HEARTBEAT_INTERVAL", "30"))
RAVEN_HUNG_THRESHOLD = int(_optional("RAVEN_HUNG_THRESHOLD", "600"))
RAVEN_ERROR_THRESHOLD = int(_optional("RAVEN_ERROR_THRESHOLD", "5"))
RAVEN_CHECK_INTERVAL = int(_optional("RAVEN_CHECK_INTERVAL", "300"))

# --- Model mapping (seed defaults; overridden via Identity settings UI) ---
DEFAULT_MODEL = _optional("DEFAULT_MODEL")
ASSISTANT_MODEL = _optional("ASSISTANT_MODEL")
CODING_MODEL = _optional("CODING_MODEL")
LIBRARIAN_MODEL = _optional("LIBRARIAN_MODEL")

# --- TTS ---
DEFAULT_TTS_VOICE = _optional("DEFAULT_TTS_VOICE", "af_heart")

# --- Workspace runtime ---
WORKSPACE_ROOT = _optional("WORKSPACE_ROOT", "/workspaces")
WORKSPACE_REGISTRY_PATH = _optional("WORKSPACE_REGISTRY_PATH", "/data/workspaces.json")
WORKSPACE_DATABASE_URL = _optional("WORKSPACE_DATABASE_URL", "sqlite:////data/workspace.db")
WORKSPACE_RUNTIME_FILE_READ_LIMIT = int(_optional("WORKSPACE_RUNTIME_FILE_READ_LIMIT", "5000"))
WORKSPACE_RUNTIME_PYTEST_TIMEOUT_SECONDS = int(_optional("WORKSPACE_RUNTIME_PYTEST_TIMEOUT_SECONDS", "90"))

# --- Volume backup ---
VOLUME_MANIFEST_PATH = _optional("VOLUME_MANIFEST_PATH", "/data/volumes.json")

# --- Music Assistant (MASS) ---
MASS_CONFIG_ENTRY_ID = _optional("MASS_CONFIG_ENTRY_ID", "")
VOLUME_BACKUP_ROOT = _optional("VOLUME_BACKUP_ROOT", "/data/backups")

# --- DNS ---
DNS_CONF_PATH = _optional("DNS_CONF_PATH", "/etc/dnsmasq.conf")
DNS_POLL_INTERVAL = int(_optional("DNS_POLL_INTERVAL", "30"))
UPSTREAM_DNS = _optional("UPSTREAM_DNS", "8.8.8.8,1.1.1.1")

# --- Misc ---
TIMEZONE = _optional("TIMEZONE", "America/New_York")
GIT_WEBHOOK_SECRET = _optional("GIT_WEBHOOK_SECRET")
ANNOUNCEMENT_BLACKLIST = _optional("ANNOUNCEMENT_BLACKLIST")
LOCAL_NOTES_ROOT = _optional("LOCAL_NOTES_ROOT")
EMBEDDING_MODEL = _optional("EMBEDDING_MODEL", "BAAI/bge-small-en-v1.5")
FAST_PATH_THRESHOLD = float(_optional("FAST_PATH_THRESHOLD", "0.85"))

# --- Runtime paths (container/install dependent) ---
MODELS_DIR = _optional("MODELS_DIR", "/app/models")
TEMP_MEDIA_DIR = _optional("TEMP_MEDIA_DIR", "/tmp/sharedllm_media")
SCRIPTS_DIR = _optional("SCRIPTS_DIR", "/app/scripts")
LEGACY_ENV_PATH = _optional("LEGACY_ENV_PATH", "/app/.env.legacy")

# --- RAG / ChromaDB ---
CHROMA_PERSIST_DIR = _optional("CHROMA_PERSIST_DIR", "/data/chroma_db")

# --- Identity ---
IDENTITY_DATABASE_URL = _optional("IDENTITY_DATABASE_URL", "sqlite:////data/identity.db")

# --- Execution ---
COMPOSE_PROJECT_DIR = _optional("COMPOSE_PROJECT_DIR")
EXECUTION_EXTERNAL_HOST = _optional("EXECUTION_EXTERNAL_HOST")

# --- Gateway ---
OLLAMA_TIMEOUT = int(_optional("OLLAMA_TIMEOUT", "600"))
PHRASEBOOK_PATH = _optional("PHRASEBOOK_PATH")
SYSTEM_IDENTITY = "raven_system"

# --- Git integration aliases ---
GITHUB_URL = _optional("GITHUB_URL") or GIT_URL
GITHUB_USER = _optional("GITHUB_USER") or GIT_USER
GITHUB_TOKEN = _optional("GITHUB_TOKEN") or GIT_TOKEN
GITLAB_URL = _optional("GITLAB_URL")
GITLAB_USER = _optional("GITLAB_USER")
GITLAB_TOKEN = _optional("GITLAB_TOKEN")
AUDIOBOOKSHELF_URL = _optional("AUDIOBOOKSHELF_URL") or _optional("ABS_URL")
AUDIOBOOKSHELF_USER = _optional("AUDIOBOOKSHELF_USER") or _optional("ABS_USER")
AUDIOBOOKSHELF_PASS = _optional("AUDIOBOOKSHELF_PASS") or _optional("ABS_PASS")

# --- Gateway shorthand aliases (for backward compat) ---
IDENTITY_SVC = IDENTITY_SVC_URL
EXECUTION_SVC = EXECUTION_SVC_URL
RAG_SVC = RAG_SVC_URL
STORAGE_SVC = STORAGE_SVC_URL
LOGGING_SVC = LOGGING_SVC_URL
WORKSPACE_RUNTIME_SVC = WORKSPACE_RUNTIME_SVC_URL

CONFIG = {
    "assistant_model": ASSISTANT_MODEL or "",
    "librarian_model": LIBRARIAN_MODEL or "",
    "coding_model": CODING_MODEL or "",
    "mass_config_entry_id": MASS_CONFIG_ENTRY_ID,
}
