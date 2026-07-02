"""
Gateway configuration.

NO .env imports for service URLs or credentials.
All runtime settings come from Identity service at runtime.
.env is seed-only (used only by Identity's /api/admin/seed endpoint).

Only INTERNAL_SECRET is read from the environment (set by docker-compose).
Service URLs default to Docker DNS names but are overridable via Identity settings.
"""
import os

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

# --- Default service URLs (Docker DNS) — overridable via Identity settings ---
# These are import-time defaults. Runtime code should use get_all_settings() from orchestrator.
IDENTITY_SVC = os.getenv("IDENTITY_SVC_URL", "http://identity:8001")
EXECUTION_SVC = os.getenv("EXECUTION_SVC_URL", "http://execution:8003")
RAG_SVC = os.getenv("RAG_SVC_URL", "http://rag:8004")
STORAGE_SVC = os.getenv("STORAGE_SVC_URL", "http://storage:8005")
LOGGING_SVC = os.getenv("LOGGING_SVC_URL", "http://logging:8006")
WORKSPACE_RUNTIME_SVC = os.getenv("WORKSPACE_RUNTIME_SVC_URL", "http://workspace_runtime:8007")
CONTROL_PLANE_URL = os.getenv("CONTROL_PLANE_URL", "http://control_plane:8008")
OLLAMA_URL = os.getenv("OLLAMA_URL", "")  # .env is seed-only; runtime resolved from Identity settings
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
SEARXNG_URL = os.getenv("SEARXNG_URL", "")
LLAMA_SERVER_PROXY_URL = os.getenv("LLAMA_SERVER_PROXY_URL", "")
OLLAMA_TIMEOUT = 600.0

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
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "nomic-ai/nomic-embed-text-v1.5")
PHRASEBOOK_PATH = os.getenv("PHRASEBOOK_PATH", "")
TIMEZONE = os.getenv("TIMEZONE", "")  # Resolved at runtime from Identity settings

# --- CONFIG dict for backward compat (resolved at runtime from Identity) ---
CONFIG = {
    "assistant_model": "",
    "librarian_model": "",
    "coding_model": "",
}
