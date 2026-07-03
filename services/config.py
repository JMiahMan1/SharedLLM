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

def _safe_int(key: str, default: int) -> int:
    val = os.getenv(key)
    if val is None or val.strip() == "":
        return default
    try:
        return int(float(val))
    except ValueError:
        return default

def _safe_float(key: str, default: float) -> float:
    val = os.getenv(key)
    if val is None or val.strip() == "":
        return default
    try:
        return float(val)
    except ValueError:
        return default

# --- Required secrets (only these come from .env for bootstrapping) ---
INTERNAL_SECRET = _required("INTERNAL_SECRET")
FERNET_KEY = _required("FERNET_KEY")

# --- Identity service endpoint (bootstrap only) ---
IDENTITY_SVC_URL = _required("IDENTITY_SVC_URL")

# --- Runtime config: fetched from Identity service at startup ---
# These are module-level placeholders; populated by resolve_runtime_config()
OLLAMA_URL = ""
EXECUTION_SVC_URL = os.getenv("EXECUTION_SVC_URL")
RAG_SVC_URL = os.getenv("RAG_SVC_URL")
STORAGE_SVC_URL = os.getenv("STORAGE_SVC_URL")
LOGGING_SVC_URL = os.getenv("LOGGING_SVC_URL")
WORKSPACE_RUNTIME_SVC_URL = os.getenv("WORKSPACE_RUNTIME_SVC_URL")
CONTROL_PLANE_URL = os.getenv("CONTROL_PLANE_URL")
SEARXNG_URL = os.getenv("SEARXNG_URL")
HA_URL = os.getenv("HA_URL")
HA_TOKEN = os.getenv("HA_TOKEN")
NEXTCLOUD_URL = os.getenv("NEXTCLOUD_URL")
NEXTCLOUD_USER = os.getenv("NEXTCLOUD_USER")
NEXTCLOUD_PASS = os.getenv("NEXTCLOUD_PASS")
GIT_URL = os.getenv("GIT_URL")
GIT_USER = os.getenv("GIT_USER")
GIT_TOKEN = os.getenv("GIT_TOKEN")
REDIS_URL = os.getenv("REDIS_URL")
LOG_RETENTION_DAYS = 30
LOG_MAX_ENTRIES = 10000
RAVEN_MAX_TOTAL_SECONDS = 1800
RAVEN_ITERATION_TIMEOUT = 600
RAVEN_HEARTBEAT_INTERVAL = 30
RAVEN_HUNG_THRESHOLD = 600
RAVEN_ERROR_THRESHOLD = 5
RAVEN_CHECK_INTERVAL = 300
DEFAULT_MODEL = os.getenv("DEFAULT_MODEL")
ASSISTANT_MODEL = os.getenv("ASSISTANT_MODEL")
CODING_MODEL = os.getenv("CODING_MODEL")
LIBRARIAN_MODEL = os.getenv("LIBRARIAN_MODEL")
DEFAULT_TTS_VOICE = os.getenv("DEFAULT_TTS_VOICE")
WORKSPACE_ROOT = os.getenv("WORKSPACE_ROOT")
WORKSPACE_REGISTRY_PATH = os.getenv("WORKSPACE_REGISTRY_PATH")
WORKSPACE_DATABASE_URL = os.getenv("WORKSPACE_DATABASE_URL")
WORKSPACE_RUNTIME_FILE_READ_LIMIT = _safe_int("WORKSPACE_RUNTIME_FILE_READ_LIMIT", 5000)
WORKSPACE_RUNTIME_PYTEST_TIMEOUT_SECONDS = _safe_int("WORKSPACE_RUNTIME_PYTEST_TIMEOUT_SECONDS", 90)
VOLUME_MANIFEST_PATH = os.getenv("VOLUME_MANIFEST_PATH")
MASS_CONFIG_ENTRY_ID = os.getenv("MASS_CONFIG_ENTRY_ID")
VOLUME_BACKUP_ROOT = os.getenv("VOLUME_BACKUP_ROOT")
ABS_URL = os.getenv("ABS_URL")
ABS_API_KEY = os.getenv("ABS_API_KEY")
DNS_CONF_PATH = os.getenv("DNS_CONF_PATH")
DNS_POLL_INTERVAL = _safe_int("DNS_POLL_INTERVAL", 30)
UPSTREAM_DNS = os.getenv("UPSTREAM_DNS")
TIMEZONE = os.getenv("TIMEZONE")
GIT_WEBHOOK_SECRET = os.getenv("GIT_WEBHOOK_SECRET")
ANNOUNCEMENT_BLACKLIST = os.getenv("ANNOUNCEMENT_BLACKLIST")
LOCAL_NOTES_ROOT = os.getenv("LOCAL_NOTES_ROOT")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL")
FAST_PATH_THRESHOLD = _safe_float("FAST_PATH_THRESHOLD", 0.85)
MODELS_DIR = os.getenv("MODELS_DIR")
TEMP_MEDIA_DIR = os.getenv("TEMP_MEDIA_DIR")
SCRIPTS_DIR = os.getenv("SCRIPTS_DIR")
LEGACY_ENV_PATH = os.getenv("LEGACY_ENV_PATH")
CHROMA_PERSIST_DIR = os.getenv("CHROMA_PERSIST_DIR")
IDENTITY_DATABASE_URL = os.getenv("IDENTITY_DATABASE_URL")
COMPOSE_PROJECT_DIR = os.getenv("COMPOSE_PROJECT_DIR")
EXECUTION_EXTERNAL_HOST = os.getenv("EXECUTION_EXTERNAL_HOST")
OLLAMA_TIMEOUT = _safe_int("OLLAMA_TIMEOUT", 600)
PHRASEBOOK_PATH = os.getenv("PHRASEBOOK_PATH")
GATEWAY_INTERNAL_URL = os.getenv("GATEWAY_INTERNAL_URL")
GITHUB_URL = os.getenv("GITHUB_URL")
GITHUB_USER = os.getenv("GITHUB_USER")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GITLAB_URL = os.getenv("GITLAB_URL")
GITLAB_USER = os.getenv("GITLAB_USER")
GITLAB_TOKEN = os.getenv("GITLAB_TOKEN")
AUDIOBOOKSHELF_URL = os.getenv("AUDIOBOOKSHELF_URL")
AUDIOBOOKSHELF_USER = os.getenv("AUDIOBOOKSHELF_USER")
AUDIOBOOKSHELF_PASS = os.getenv("AUDIOBOOKSHELF_PASS")

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
    if _is_testing():
        return
    
    import httpx
    
    settings_map = {
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
        # redis_url is infrastructure-dependent (Docker vs host networking) — read from env only
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
        "workspace_runtime_root": "WORKSPACE_RUNTIME_ROOT",
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
    
    import asyncio
    
    max_retries = 5
    for attempt in range(max_retries):
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
                
                # Update shorthand aliases (set at import time, stale after runtime resolve)
                globals()["EXECUTION_SVC"] = globals()["EXECUTION_SVC_URL"]
                globals()["IDENTITY_SVC"] = globals()["IDENTITY_SVC_URL"]
                globals()["RAG_SVC"] = globals()["RAG_SVC_URL"]
                globals()["STORAGE_SVC"] = globals()["STORAGE_SVC_URL"]
                globals()["LOGGING_SVC"] = globals()["LOGGING_SVC_URL"]
                globals()["WORKSPACE_RUNTIME_SVC"] = globals()["WORKSPACE_RUNTIME_SVC_URL"]
                return
        except Exception as e:
            if attempt < max_retries - 1:
                log.warning(f"Identity not ready (attempt {attempt+1}/{max_retries}): {e}")
                await asyncio.sleep(2)
            else:
                log.warning(f"Failed to resolve runtime config from Identity after {max_retries} attempts: {e}")

CONFIG = {
    "assistant_model": ASSISTANT_MODEL or "",
    "librarian_model": LIBRARIAN_MODEL or "",
    "coding_model": CODING_MODEL or "",
    "mass_config_entry_id": MASS_CONFIG_ENTRY_ID,
}

# --- Validation: critical runtime paths that must exist ---
_MISSING_REQUIRED: list[str] = []
for _v in ("EXECUTION_SVC_URL", "HA_URL", "HA_TOKEN", "WORKSPACE_ROOT", "TEMP_MEDIA_DIR", "MODELS_DIR", "DEFAULT_TTS_VOICE"):
    if not os.getenv(_v):
        _MISSING_REQUIRED.append(_v)

if _MISSING_REQUIRED and not _is_testing():
    logging.basicConfig(level="CRITICAL")
    logging.critical(
        f"FATAL: Missing required environment variables: {', '.join(_MISSING_REQUIRED)}\n"
        "These must be set in .env and seeded via Identity Service.\n"
        "See README.md for first-run setup instructions."
    )
    sys.exit(1)
