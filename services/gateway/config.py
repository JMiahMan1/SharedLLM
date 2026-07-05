"""
Gateway configuration.

NO .env imports for service URLs or credentials.
All runtime settings come from Identity service at runtime.
.env is seed-only (used only by Identity's /api/admin/seed endpoint).

Only INTERNAL_SECRET is read from the environment (set by docker-compose).
Service URLs default to Docker DNS names but are overridable via Identity settings.
"""
import os

# Import runtime-resolved values from services.config (updated by resolve_runtime_config)
try:
    from services.config import (
        IDENTITY_SVC_URL,
        EXECUTION_SVC_URL,
        RAG_SVC_URL,
        STORAGE_SVC_URL,
        LOGGING_SVC_URL,
        WORKSPACE_RUNTIME_SVC_URL,
        CONTROL_PLANE_URL,
        OLLAMA_URL,
        REDIS_URL,
        SEARXNG_URL,
        LLAMA_SERVER_PROXY_URL,
    )
except ImportError:
    # Fallback to environment variables if services.config is not available
    IDENTITY_SVC_URL = os.getenv("IDENTITY_SVC_URL", "http://identity:8001")
    EXECUTION_SVC_URL = os.getenv("EXECUTION_SVC_URL")
    RAG_SVC_URL = os.getenv("RAG_SVC_URL")
    STORAGE_SVC_URL = os.getenv("STORAGE_SVC_URL")
    LOGGING_SVC_URL = os.getenv("LOGGING_SVC_URL")
    WORKSPACE_RUNTIME_SVC_URL = os.getenv("WORKSPACE_RUNTIME_SVC_URL")
    CONTROL_PLANE_URL = os.getenv("CONTROL_PLANE_URL")
    OLLAMA_URL = os.getenv("OLLAMA_URL")
    REDIS_URL = os.getenv("REDIS_URL")
    SEARXNG_URL = os.getenv("SEARXNG_URL")
    LLAMA_SERVER_PROXY_URL = os.getenv("LLAMA_SERVER_PROXY_URL")

# Use runtime-resolved values (or fallback to env vars)
IDENTITY_SVC = IDENTITY_SVC_URL
EXECUTION_SVC = EXECUTION_SVC_URL
RAG_SVC = RAG_SVC_URL
STORAGE_SVC = STORAGE_SVC_URL
LOGGING_SVC = LOGGING_SVC_URL
WORKSPACE_RUNTIME_SVC = WORKSPACE_RUNTIME_SVC_URL
CONTROL_PLANE_URL = CONTROL_PLANE_URL
OLLAMA_URL = OLLAMA_URL
REDIS_URL = REDIS_URL
SEARXNG_URL = SEARXNG_URL
LLAMA_SERVER_PROXY_URL = LLAMA_SERVER_PROXY_URL


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


# --- Inter-service auth (set by docker-compose) ---
INTERNAL_SECRET = os.getenv("INTERNAL_SECRET", "")

# --- Constants (not user-configurable) ---
SYSTEM_IDENTITY = "raven_system"

# --- Raven agent limits (overridable via Identity settings) ---
RAVEN_MAX_TOTAL_SECONDS = _safe_int("RAVEN_MAX_TOTAL_SECONDS", 1800)
RAVEN_ITERATION_TIMEOUT = _safe_int("RAVEN_ITERATION_TIMEOUT", 600)
RAVEN_HEARTBEAT_INTERVAL = _safe_int("RAVEN_HEARTBEAT_INTERVAL", 30)
RAVEN_HUNG_THRESHOLD = _safe_int("RAVEN_HUNG_THRESHOLD", 600)
RAVEN_CHECK_INTERVAL = _safe_int("RAVEN_CHECK_INTERVAL", 300)
RAVEN_ERROR_THRESHOLD = _safe_int("RAVEN_ERROR_THRESHOLD", 5)

# --- ABS / media timeouts ---
ABS_TIMEOUT = _safe_int("ABS_TIMEOUT", 30)

# --- Misc ---
FAST_PATH_THRESHOLD = _safe_float("FAST_PATH_THRESHOLD", 0.85)
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL")
PHRASEBOOK_PATH = os.getenv("PHRASEBOOK_PATH")
TIMEZONE = os.getenv("TIMEZONE")  # Resolved at runtime from Identity settings

# --- CONFIG dict for backward compat (resolved at runtime from Identity) ---
CONFIG = {
    "assistant_model": os.getenv("ASSISTANT_MODEL"),
    "librarian_model": os.getenv("LIBRARIAN_MODEL"),
    "coding_model": os.getenv("CODING_MODEL"),
}
