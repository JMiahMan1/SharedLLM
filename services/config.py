import os
import sys
import logging

log = logging.getLogger(__name__)

def _is_testing() -> bool:
    return "PYTEST_CURRENT_TEST" in os.environ or "pytest" in sys.modules

def _required(key: str) -> str:
    val = os.getenv(key)
    if not val:
        if _is_testing():
            return f"__test_placeholder_{key}__"
        logging.basicConfig(level="CRITICAL")
        logging.critical(f"FATAL: {key} environment variable is not set. Refusing to start.")
        sys.exit(1)
    return val

def _optional(key: str, default: str = "") -> str:
    return os.getenv(key, default)

# --- Required secrets (only these come from .env for bootstrapping) ---
INTERNAL_SECRET = _required("INTERNAL_SECRET")

# --- Identity service endpoint (bootstrap only) ---
IDENTITY_SVC_URL = _optional("IDENTITY_SVC_URL", "http://identity:8001")

# --- Runtime config: fetched from Identity service at startup ---
# These are module-level placeholders; populated by resolve_runtime_config()
FERNET_KEY = ""
OLLAMA_URL = ""
EXECUTION_SVC_URL = "http://execution:8003"
RAG_SVC_URL = "http://rag:8004"
STORAGE_SVC_URL = "http://storage:8005"
LOGGING_SVC_URL = "http://logging:8006"
WORKSPACE_RUNTIME_SVC_URL = "http://workspace_runtime:8007"
CONTROL_PLANE_URL = ""
SEARXNG_URL = ""
HA_URL = ""
HA_TOKEN = ""
NEXTCLOUD_URL = ""
NEXTCLOUD_USER = ""
NEXTCLOUD_PASS = ""
GIT_URL = ""
GIT_USER = ""
GIT_TOKEN = ""
REDIS_URL = "redis://redis:6379/0"
LOG_RETENTION_DAYS = 30
LOG_MAX_ENTRIES = 10000
RAVEN_MAX_TOTAL_SECONDS = 1800
RAVEN_ITERATION_TIMEOUT = 600
RAVEN_HEARTBEAT_INTERVAL = 30
RAVEN_HUNG_THRESHOLD = 600
RAVEN_ERROR_THRESHOLD = 5
RAVEN_CHECK_INTERVAL = 300
DEFAULT_MODEL = ""
ASSISTANT_MODEL = ""
CODING_MODEL = ""
LIBRARIAN_MODEL = ""
DEFAULT_TTS_VOICE = "af_heart"
WORKSPACE_ROOT = "/workspaces"
WORKSPACE_REGISTRY_PATH = "/data/workspaces.json"
WORKSPACE_DATABASE_URL = "sqlite:////data/workspace.db"
WORKSPACE_RUNTIME_FILE_READ_LIMIT = 5000
WORKSPACE_RUNTIME_PYTEST_TIMEOUT_SECONDS = 90
VOLUME_MANIFEST_PATH = "/data/volumes.json"
MASS_CONFIG_ENTRY_ID = ""
VOLUME_BACKUP_ROOT = "/data/backups"
ABS_URL = ""
ABS_API_KEY = ""
DNS_CONF_PATH = "/etc/dnsmasq.conf"
DNS_POLL_INTERVAL = 30
UPSTREAM_DNS = "8.8.8.8,1.1.1.1"
TIMEZONE = "America/New_York"
GIT_WEBHOOK_SECRET = ""
ANNOUNCEMENT_BLACKLIST = ""
LOCAL_NOTES_ROOT = ""
EMBEDDING_MODEL = "BAAI/bge-small-en-v1.5"
FAST_PATH_THRESHOLD = 0.85
MODELS_DIR = "/app/models"
TEMP_MEDIA_DIR = "/tmp/sharedllm_media"
SCRIPTS_DIR = "/app/scripts"
LEGACY_ENV_PATH = "/app/.env.legacy"
CHROMA_PERSIST_DIR = "/data/chroma_db"
IDENTITY_DATABASE_URL = "sqlite:////data/identity.db"
COMPOSE_PROJECT_DIR = ""
EXECUTION_EXTERNAL_HOST = ""
OLLAMA_TIMEOUT = 600
PHRASEBOOK_PATH = ""
GITHUB_URL = ""
GITHUB_USER = ""
GITHUB_TOKEN = ""
GITLAB_URL = ""
GITLAB_USER = ""
GITLAB_TOKEN = ""
AUDIOBOOKSHELF_URL = ""
AUDIOBOOKSHELF_USER = ""
AUDIOBOOKSHELF_PASS = ""

# --- Gateway shorthand aliases ---
IDENTITY_SVC = IDENTITY_SVC_URL
EXECUTION_SVC = EXECUTION_SVC_URL
RAG_SVC = RAG_SVC_URL
STORAGE_SVC = STORAGE_SVC_URL
LOGGING_SVC = LOGGING_SVC_URL
WORKSPACE_RUNTIME_SVC = WORKSPACE_RUNTIME_SVC_URL

async def resolve_runtime_config():
    """Fetch all runtime configuration from Identity service.
    
    The .env file is seed-only; Identity holds the authoritative runtime values.
    Call this at service startup before handling any requests.
    """
    global FERNET_KEY, OLLAMA_URL, EXECUTION_SVC_URL, RAG_SVC_URL
    global STORAGE_SVC_URL, LOGGING_SVC_URL, WORKSPACE_RUNTIME_SVC_URL
    global CONTROL_PLANE_URL, SEARXNG_URL, HA_URL, HA_TOKEN
    global NEXTCLOUD_URL, NEXTCLOUD_USER, NEXTCLOUD_PASS
    global GIT_URL, GIT_USER, GIT_TOKEN, REDIS_URL
    global LOG_RETENTION_DAYS, LOG_MAX_ENTRIES
    global RAVEN_MAX_TOTAL_SECONDS, RAVEN_ITERATION_TIMEOUT
    global RAVEN_HEARTBEAT_INTERVAL, RAVEN_HUNG_THRESHOLD
    global RAVEN_ERROR_THRESHOLD, RAVEN_CHECK_INTERVAL
    global DEFAULT_MODEL, ASSISTANT_MODEL, CODING_MODEL, LIBRARIAN_MODEL
    global DEFAULT_TTS_VOICE, WORKSPACE_ROOT, MASS_CONFIG_ENTRY_ID
    global ABS_URL, ABS_API_KEY, UPSTREAM_DNS, DNS_POLL_INTERVAL
    global EMBEDDING_MODEL, FAST_PATH_THRESHOLD, EXECUTION_EXTERNAL_HOST
    global AUDIOBOOKSHELF_URL, AUDIOBOOKSHELF_USER, AUDIOBOOKSHELF_PASS
    
    if _is_testing():
        return
    
    import httpx
    
    settings_map = {
        "fernet_key": "FERNET_KEY",
        "llm_local_url": "OLLAMA_URL",
        "execution_svc_url": "EXECUTION_SVC_URL",
        "rag_svc_url": "RAG_SVC_URL",
        "storage_svc_url": "STORAGE_SVC_URL",
        "logging_svc_url": "LOGGING_SVC_URL",
        "workspace_runtime_svc_url": "WORKSPACE_RUNTIME_SVC_URL",
        "control_plane_url": "CONTROL_PLANE_URL",
        "searxng_url": "SEARXNG_URL",
        "ha_url": "HA_URL",
        "ha_token": "HA_TOKEN",
        "nextcloud_url": "NEXTCLOUD_URL",
        "nextcloud_user": "NEXTCLOUD_USER",
        "nextcloud_pass": "NEXTCLOUD_PASS",
        "git_url": "GIT_URL",
        "git_user": "GIT_USER",
        "git_token": "GIT_TOKEN",
        "redis_url": "REDIS_URL",
        "log_retention_days": "LOG_RETENTION_DAYS",
        "log_max_entries": "LOG_MAX_ENTRIES",
        "raven_max_total_seconds": "RAVEN_MAX_TOTAL_SECONDS",
        "raven_iteration_timeout": "RAVEN_ITERATION_TIMEOUT",
        "raven_heartbeat_interval": "RAVEN_HEARTBEAT_INTERVAL",
        "raven_hung_threshold": "RAVEN_HUNG_THRESHOLD",
        "raven_error_threshold": "RAVEN_ERROR_THRESHOLD",
        "raven_check_interval": "RAVEN_CHECK_INTERVAL",
        "default_model": "DEFAULT_MODEL",
        "assistant_model": "ASSISTANT_MODEL",
        "coding_model": "CODING_MODEL",
        "librarian_model": "LIBRARIAN_MODEL",
        "default_tts_voice": "DEFAULT_TTS_VOICE",
        "workspace_root": "WORKSPACE_ROOT",
        "mass_config_entry_id": "MASS_CONFIG_ENTRY_ID",
        "abs_url": "ABS_URL",
        "abs_api_key": "ABS_API_KEY",
        "audiobookshelf_url": "AUDIOBOOKSHELF_URL",
        "audiobookshelf_user": "AUDIOBOOKSHELF_USER",
        "audiobookshelf_pass": "AUDIOBOOKSHELF_PASS",
        "upstream_dns": "UPSTREAM_DNS",
        "dns_poll_interval": "DNS_POLL_INTERVAL",
        "embedding_model": "EMBEDDING_MODEL",
        "fast_path_threshold": "FAST_PATH_THRESHOLD",
        "execution_external_host": "EXECUTION_EXTERNAL_HOST",
    }
    
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{IDENTITY_SVC_URL}/api/settings",
                headers={"X-Internal-Secret": INTERNAL_SECRET}
            )
            if resp.status_code != 200:
                log.warning(f"Failed to fetch runtime config from Identity (HTTP {resp.status_code})")
                return
            
            settings = {s["key"]: s["value"] for s in resp.json()}
            
            for setting_key, var_name in settings_map.items():
                if setting_key in settings and settings[setting_key]:
                    value = settings[setting_key]
                    # Type coercion for known numeric settings
                    if var_name in ("LOG_RETENTION_DAYS", "LOG_MAX_ENTRIES", "RAVEN_MAX_TOTAL_SECONDS",
                                    "RAVEN_ITERATION_TIMEOUT", "RAVEN_HEARTBEAT_INTERVAL",
                                    "RAVEN_HUNG_THRESHOLD", "RAVEN_ERROR_THRESHOLD", "RAVEN_CHECK_INTERVAL",
                                    "DNS_POLL_INTERVAL", "WORKSPACE_RUNTIME_FILE_READ_LIMIT",
                                    "WORKSPACE_RUNTIME_PYTEST_TIMEOUT_SECONDS", "OLLAMA_TIMEOUT"):
                        value = int(float(value))
                    elif var_name == "FAST_PATH_THRESHOLD":
                        value = float(value)
                    globals()[var_name] = value
            
            log.info("Runtime configuration loaded from Identity service")
    except Exception as e:
        log.warning(f"Failed to resolve runtime config from Identity: {e}")

CONFIG = {
    "assistant_model": ASSISTANT_MODEL or "",
    "librarian_model": LIBRARIAN_MODEL or "",
    "coding_model": CODING_MODEL or "",
    "mass_config_entry_id": MASS_CONFIG_ENTRY_ID,
}
