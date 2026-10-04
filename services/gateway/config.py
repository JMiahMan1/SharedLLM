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
from services.config import (
    CONTROL_PLANE_URL,
    EXECUTION_SVC_URL,
    GEO_SVC_URL,
    IDENTITY_SVC_URL,
    LOGGING_SVC_URL,
    OLLAMA_URL,
    RAG_SVC_URL,
    REDIS_URL,
    SEARXNG_URL,
    STORAGE_SVC_URL,
    WORKSPACE_RUNTIME_SVC_URL,
)

LLAMA_SERVER_PROXY_URL = os.getenv("LLAMA_SERVER_PROXY_URL")

# Telemetry scheduling service (report jobs, queue, alpaca admission).
TELEMETRY_SVC = os.getenv("TELEMETRY_SVC_URL", "http://telemetry:11438").rstrip("/")

# alpaca Stable Diffusion image backend (exposed to external clients as a tool).
ALPACA_SD_URL = os.getenv("ALPACA_SD_URL", "http://jeremiah-home-desktop.local:8081")

# alpaca Arcade game shelf (JSON cards + playable HTML games, port 5001).
ALPACA_ARCADE_URL = os.getenv("ALPACA_ARCADE_URL", "http://jeremiah-home-desktop.local:5001").rstrip("/")
# URL the browser uses to open a game (defaults to the internal URL for LAN installs).
ALPACA_ARCADE_PUBLIC_URL = os.getenv("ALPACA_ARCADE_PUBLIC_URL", ALPACA_ARCADE_URL).rstrip("/")

# alpaca audio server (TTS + music generation). No default on purpose: an unset
# value fails with a clear message instead of probing a guessed host.
ALPACA_AUDIO_URL = (os.getenv("ALPACA_AUDIO_URL") or "").rstrip("/")

# alpaca dashboard (port 5000). This is the *front door* for most of the audio
# work: it owns /api/audio/* and /api/podcast/*, and it is what knows about the
# podcast mixer and the OpenVoice voice profiles, so prefer it over talking to
# the audio server directly.
#
# Speaker identification is the ONE exception and goes to ALPACA_AUDIO_URL
# instead: it needs OpenVoice's reference encoder, which only exists in the
# audio-server container because it is the only one with torch. An earlier
# version of this comment said the dashboard hosted identification too, which
# would have led a reader to move that call to port 5000 and 404 it.
ALPACA_WEB_URL = os.getenv("ALPACA_WEB_URL", "http://jeremiah-home-desktop.local:5000").rstrip("/")

# Use runtime-resolved values (or fallback to env vars)
IDENTITY_SVC = IDENTITY_SVC_URL
EXECUTION_SVC = EXECUTION_SVC_URL
GEO_SVC = GEO_SVC_URL
RAG_SVC = RAG_SVC_URL
STORAGE_SVC = STORAGE_SVC_URL
LOGGING_SVC = LOGGING_SVC_URL
WORKSPACE_RUNTIME_SVC = WORKSPACE_RUNTIME_SVC_URL
CONTROL_PLANE_URL = CONTROL_PLANE_URL
OLLAMA_URL = OLLAMA_URL
OLLAMA_TIMEOUT = 300.0
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
# Hard wall-clock cap for a single Raven mission. Kept deliberately low (30 min)
# so a model that loops without making distinct progress — e.g. re-running the
# same failing step with slightly varied command text to dodge the literal-string
# loop detector — is force-terminated well before it can saturate RAG/Execution
# for an hour. Overridable via RAVEN_MAX_TOTAL_SECONDS env if a legitimately long
# mission needs more time. Mirrors services/config.py's 1800s default.
RAVEN_MAX_TOTAL_SECONDS = _safe_int("RAVEN_MAX_TOTAL_SECONDS", 1800)
RAVEN_ITERATION_TIMEOUT = _safe_int("RAVEN_ITERATION_TIMEOUT", 600)
RAVEN_HEARTBEAT_INTERVAL = _safe_int("RAVEN_HEARTBEAT_INTERVAL", 30)
RAVEN_HUNG_THRESHOLD = _safe_int("RAVEN_HUNG_THRESHOLD", 600)
RAVEN_CHECK_INTERVAL = _safe_int("RAVEN_CHECK_INTERVAL", 300)
RAVEN_ERROR_THRESHOLD = _safe_int("RAVEN_ERROR_THRESHOLD", 5)

# --- ABS / media timeouts ---
# Audiobookshelf is a separate host reached over the network, so the first call
# pays DNS + TLS + its own request time: measured cold at 5.26s, which the old
# 5s budget cut off and reported as "ABS unavailable" even though ABS answers in
# ~0.2s once warm. 12s leaves room for a cold call and still lands below the
# browser's 15s axios timeout, so the server answers first with a real notice.
ABS_TIMEOUT = _safe_int("ABS_TIMEOUT", 12)

# --- Phase 2: env-configurable poll intervals (capped backoff / fallback) ---
# /api/ps has no push/event API, so the Ollama slot wait still polls — but with
# an exponential backoff capped at OLLAMA_SLOT_POLL_MAX instead of a fixed 1s.
OLLAMA_SLOT_POLL_INTERVAL = _safe_float("OLLAMA_SLOT_POLL_INTERVAL", 1.0)
OLLAMA_SLOT_POLL_MAX = _safe_float("OLLAMA_SLOT_POLL_MAX", 5.0)
# The SSE job-status stream waits on a Redis pub/sub channel for status changes
# and only falls back to a direct GET every JOB_STATUS_POLL_INTERVAL seconds.
JOB_STATUS_POLL_INTERVAL = _safe_float("JOB_STATUS_POLL_INTERVAL", 5.0)

# --- Phase 3: shared in-memory cache TTLs (env-configurable) ---
# Global settings and resolved identity are fetched on every chat; cache them
# for SETTINGS_CACHE_TTL / IDENTITY_CACHE_TTL seconds. The TTL bounds the
# exposure window of cached identity (which includes tokens) and guarantees
# eventual consistency even if an invalidation call is missed.
SETTINGS_CACHE_TTL = _safe_float("SETTINGS_CACHE_TTL", 30.0)
IDENTITY_CACHE_TTL = _safe_float("IDENTITY_CACHE_TTL", 30.0)

# --- Phase 3: Redis-backed entity/device cache TTLs (env-configurable) ---
HA_STATE_CACHE_TTL = _safe_int("HA_STATE_CACHE_TTL", 60)
MEDIA_DEVICE_CACHE_TTL = _safe_int("MEDIA_DEVICE_CACHE_TTL", 604800)  # 7 days

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
