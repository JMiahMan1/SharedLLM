import logging
import os
import sys

from services.common.ha_redirect import redirect as _ha_redirect

# Configuration module - handles bootstrap and runtime config resolution
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
# During a key rotation (scripts/rotate_fernet_key.sh) the key being replaced, comma separated
# if more than one: values sealed with it still decrypt while they are re-encrypted. Empty otherwise.
FERNET_KEY_PREVIOUS = _optional("FERNET_KEY_PREVIOUS")

# --- Network mode: a container detects its network at runtime and selects the
# correct service-URL set. BRIDGE_* = docker bridge network, HOST_* = host
# network (execution service). Defaults to "bridge".
NETWORK_MODE = os.getenv("NETWORK_MODE", "bridge").lower()

def _net_url(name: str, default: str = "") -> str:
    """Resolve a service URL for the current network mode.

    Reads ``{NETWORK_MODE}_{name}_SVC_URL`` (the network-aware BRIDGE_*/HOST_*
    set defined in .env) only. No fallback to legacy ``{name}_SVC_URL``: if the
    appropriate network-mode variable is not set the caller receives ``default``
    and must cope with it.

    An unset variable is only worth a warning when there is no default to fall
    back to. Every caller below passes the address that is right for the compose
    network, so warning "Service connectivity may fail" for them was fourteen
    lines a day of a problem that has never happened (measured 2026-10-27) - and
    a log that cries wolf is worse than a quiet one. The debug line keeps the
    fact for anyone reading a service's log closely.
    """
    val = os.getenv(f"{NETWORK_MODE.upper()}_{name}_SVC_URL")
    if not val:
        if default:
            logging.debug(
                "%s_%s_SVC_URL is not set; using the built-in default %s",
                NETWORK_MODE.upper(), name, default,
            )
        else:
            logging.warning(
                "WARNING: %s_%s_SVC_URL is not set in the environment and there is no "
                "default. Service connectivity may fail.",
                NETWORK_MODE.upper(), name,
            )
    return val or default

# --- Identity service endpoint (bootstrap only) ---
IDENTITY_SVC_URL = _net_url("IDENTITY", "http://identity:8001")

# --- Runtime config: fetched from Identity service at startup ---
# These are module-level placeholders; populated by resolve_runtime_config()
LLM_URL = ""
EXECUTION_SVC_URL = _net_url("EXECUTION", "http://host.docker.internal:8003")
RAG_SVC_URL = _net_url("RAG", "http://rag:8004")
STORAGE_SVC_URL = _net_url("STORAGE", "http://storage:8005")
LOGGING_SVC_URL = _net_url("LOGGING", "http://logging:8006")
WORKSPACE_RUNTIME_SVC_URL = _net_url("WORKSPACE_RUNTIME", "http://workspace_runtime:8007")
GEO_SVC_URL = _net_url("GEO", "http://geo:8009")
TELEMETRY_SVC_URL = _net_url("TELEMETRY", "http://telemetry:11438")
BIBLE_SVC_URL = _net_url("BIBLE", "http://bible:8010")
HEALTH_SVC_URL = _net_url("HEALTH", "http://health:8011")
GARDEN_SVC_URL = _net_url("GARDEN", "http://garden:8012")
CONTROL_PLANE_URL = os.getenv("CONTROL_PLANE_URL")
SEARXNG_URL = os.getenv(f"{NETWORK_MODE.upper()}_SEARXNG_URL") or os.getenv("SEARXNG_URL", "")
# A retired HA host answers through homehub (services/common/ha_redirect.py).
HA_URL = _ha_redirect(os.getenv("HA_URL"))
# homehub itself. The stack's own login (INTERNAL_SECRET) is only ever sent
# here: HA_URL may point at a real Home Assistant, which must never receive it.
# No default: blank (here and in the homehub_url setting) means the HomeHub
# integration is off, and nothing tries to reach it. See integrations().
HOMEHUB_URL = (os.getenv("HOMEHUB_URL") or "").strip().rstrip("/")
#: Self-hosted OSRM for snapping trip routes to roads (services/geo/map_match.py).
#: Unset: routes are served as raw breadcrumbs.
OSRM_URL = os.getenv("OSRM_URL")
#: The foot network (paths, trails, sidewalks) that workout routes snap to.
OSRM_FOOT_URL = os.getenv("OSRM_FOOT_URL")
#: Self-hosted Nominatim (place names <-> coordinates). Unset, not ready, or
#: no answer (outside its regions): the public nominatim.openstreetmap.org.
NOMINATIM_URL = os.getenv("NOMINATIM_URL")
HA_TOKEN = os.getenv("HA_TOKEN")
NEXTCLOUD_URL = os.getenv("NEXTCLOUD_URL")
NEXTCLOUD_USER = os.getenv("NEXTCLOUD_USER")
NEXTCLOUD_PASS = os.getenv("NEXTCLOUD_PASS")
GIT_URL = os.getenv("GIT_URL")
GIT_USER = os.getenv("GIT_USER")
GIT_TOKEN = os.getenv("GIT_TOKEN")
REDIS_URL = os.getenv(f"{NETWORK_MODE.upper()}_REDIS_URL") or os.getenv("REDIS_URL", "redis://redis:6379/0")

#: Redis key an @Jarvis question is claimed under by execution's Talk send
#: (which answers it). The gateway's Talk monitor, which answers questions
#: typed in other Talk apps, skips claimed messages so nobody gets two answers.
JARVIS_TALK_CLAIM_KEY = "jarvis:talk:answered:{token}:{message_id}"
LOG_RETENTION_DAYS = 30
#: Default per-user quota for generated artifacts, in bytes. Filled from the
#: Identity ``artifact_local_quota_bytes`` setting at startup. Deliberately no
#: hardcoded fallback: an unset value blocks artifact creation with a clear
#: configuration error instead of silently applying a made-up limit.
ARTIFACT_LOCAL_QUOTA_BYTES = 0
# Per-collection retention for time-bound RAG collections, as JSON days
# ({"telemetry_alerts": 30}). Deliberately no code default: a retention
# policy is an operator decision, and blank means keep everything -- the
# maintenance purge must never delete on a guessed schedule.
RAG_RETENTION_DAYS = os.getenv("RAG_RETENTION_DAYS")
# The home's devices in RAG (home_devices): a row that no sync has refreshed for
# this many hours, and that no search has returned in that time either, is
# removed (a user whose sync stopped, who lost access, or was removed). The
# sweep runs when RAG starts and then every HOME_DEVICE_SWEEP_MINUTES.
HOME_DEVICE_STALE_HOURS = max(1, _safe_int("HOME_DEVICE_STALE_HOURS", 72))
HOME_DEVICE_SWEEP_MINUTES = max(1, _safe_int("HOME_DEVICE_SWEEP_MINUTES", 60))
LOG_MAX_ENTRIES = 10000
# Hard wall-clock cap for one Raven mission. Must stay in sync with the
# Identity seed (services/identity/models.py) and orchestrator._DEFAULTS;
# agent_loop.py prefers the DB setting and falls back to this.
RAVEN_MAX_TOTAL_SECONDS = 1800
RAVEN_ITERATION_TIMEOUT = 600
RAVEN_HEARTBEAT_INTERVAL = 30
RAVEN_HUNG_THRESHOLD = 600
RAVEN_ERROR_THRESHOLD = 5
RAVEN_CHECK_INTERVAL = 300
# Admin alerts (docs/PUSH.md, admin row): errors in the last 5 minutes that
# make a spike, repeats of one error that make a spike, the free space below
# which a data disk is "nearly full" (percent), and how long a desktop GPU
# service must stay unreachable before admins hear about it (minutes).
ALERT_ERROR_SPIKE_THRESHOLD = _safe_int("ALERT_ERROR_SPIKE_THRESHOLD", 20)
ALERT_ERROR_REPEAT_THRESHOLD = _safe_int("ALERT_ERROR_REPEAT_THRESHOLD", 10)
ALERT_DISK_FREE_PERCENT = _safe_int("ALERT_DISK_FREE_PERCENT", 10)
ALERT_DESKTOP_DOWN_MINUTES = _safe_int("ALERT_DESKTOP_DOWN_MINUTES", 10)
# Public HTTPS names whose certificates admins hear about before they expire
# (comma-separated host names, e.g. "jarvis.example.com,games.example.com";
# empty watches nothing), and how many days ahead the warning comes.
ALERT_TLS_HOSTS = os.getenv("ALERT_TLS_HOSTS", "")
ALERT_TLS_DAYS = _safe_int("ALERT_TLS_DAYS", 14)
DEFAULT_MODEL = os.getenv("DEFAULT_MODEL")
ASSISTANT_MODEL = os.getenv("ASSISTANT_MODEL")
CODING_MODEL = os.getenv("CODING_MODEL")
LIBRARIAN_MODEL = os.getenv("LIBRARIAN_MODEL")
DEFAULT_TTS_VOICE = os.getenv("DEFAULT_TTS_VOICE")
# alpaca's audio server, which speaks every voice Jarvis uses (Kokoro and Piper);
# the speech_server_url setting (Admin > Raven > Spoken replies) overrides it.
SPEECH_SERVER_URL = (os.getenv("SPEECH_SERVER_URL") or os.getenv("KOKORO_GPU_URL") or "").strip().rstrip("/")
WORKSPACE_ROOT = os.getenv("WORKSPACE_ROOT")
WORKSPACE_REGISTRY_PATH = os.getenv("WORKSPACE_REGISTRY_PATH")
WORKSPACE_DATABASE_URL = os.getenv("WORKSPACE_DATABASE_URL")
WORKSPACE_RUNTIME_FILE_READ_LIMIT = _safe_int("WORKSPACE_RUNTIME_FILE_READ_LIMIT", 5000)
WORKSPACE_RUNTIME_PYTEST_TIMEOUT_SECONDS = _safe_int("WORKSPACE_RUNTIME_PYTEST_TIMEOUT_SECONDS", 90)
# Nextcloud-backed workspaces: how often the runtime runs a background two-way
# sync (0 turns the background loop off; manual syncs still work), and the
# per-request WebDAV timeout.
WORKSPACE_NEXTCLOUD_SYNC_INTERVAL_SECONDS = _safe_int("WORKSPACE_NEXTCLOUD_SYNC_INTERVAL_SECONDS", 300)
WORKSPACE_NEXTCLOUD_TIMEOUT_SECONDS = _safe_int("WORKSPACE_NEXTCLOUD_TIMEOUT_SECONDS", 120)
# Largest single multipart upload (all files in one request) the runtime accepts.
WORKSPACE_UPLOAD_MAX_BYTES = _safe_int("WORKSPACE_UPLOAD_MAX_BYTES", 1073741824)
VOLUME_MANIFEST_PATH = os.getenv("VOLUME_MANIFEST_PATH")
MASS_CONFIG_ENTRY_ID = os.getenv("MASS_CONFIG_ENTRY_ID")
VOLUME_BACKUP_ROOT = os.getenv("VOLUME_BACKUP_ROOT")
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
IDENTITY_DATABASE_URL = os.getenv("IDENTITY_DATABASE_URL")


def _phone_country_code(key: str) -> str:
    """The household's country calling code ("1", "44"), or "" when unset.

    A phone number saved without a ``+`` is completed with it, so every
    number the server keeps is E.164. Set but not a calling code is a
    configuration mistake, and refused at startup rather than guessed around.
    """
    raw = (os.getenv(key) or "").strip().lstrip("+")
    if raw and not (raw.isdigit() and 1 <= len(raw) <= 3):
        raise ValueError(f"{key} must be a country calling code such as 1 or 44, not {raw!r}")
    return raw


HOUSEHOLD_PHONE_COUNTRY_CODE = _phone_country_code("HOUSEHOLD_PHONE_COUNTRY_CODE")


def _csv_env(key: str, default: tuple[str, ...]) -> list[str]:
    """A comma-separated env var as a list, with a deterministic default."""
    raw = os.getenv(key)
    if raw is None:
        return list(default)
    return [item.strip() for item in raw.split(",") if item.strip()]


# Browser origins allowed to call Identity directly. The web app normally goes
# through Caddy on its own origin (so no CORS is involved), but the Vite dev
# server and the Android WebView call Identity across origins. Never "*":
# Identity answers with decrypted credentials, and a wildcard lets any site a
# signed-in browser visits read them.
# The household's own public origin is not a code default: it comes from
# JARVIS_HOST (or set IDENTITY_CORS_ORIGINS outright).
_IDENTITY_CORS_DEFAULTS = (
    "http://localhost",
    "https://localhost",
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:8080",
    "http://127.0.0.1:8080",
)
IDENTITY_CORS_ORIGINS = [
    origin
    for origin in _csv_env(
        "IDENTITY_CORS_ORIGINS",
        tuple(o for o in ((os.getenv("JARVIS_HOST") or "").strip().rstrip("/"),) if o) + _IDENTITY_CORS_DEFAULTS,
    )
    # A wildcard would re-open the finding this setting exists to close.
    if origin != "*"
]
BIBLE_DATABASE_URL = os.getenv("BIBLE_DATABASE_URL")
# Bible study settings. Deliberately no code defaults: an unset BLB base URL
# must produce a visible "configure this" state in the devotional card, not a
# deep link guessed at some host that may not be blb.org's.
BLB_BASE_URL = os.getenv("BLB_BASE_URL")
# Root of the Nextcloud library, e.g. /Books/Text. Blank refuses to browse
# rather than guessing a shelf, because a wrong guess reports itself as an
# empty library and reads like "we have no Bibles".
CALIBRE_LIBRARY_PATH = os.getenv("CALIBRE_LIBRARY_PATH")
BIBLE_DEVOTIONAL_DIR = os.getenv("BIBLE_DEVOTIONAL_DIR")
# The translation whose verses are embedded for meaning search. Unset: the
# installed translation whose wording is most modern (see bible/meaning.py).
BIBLE_MEANING_VERSION = os.getenv("BIBLE_MEANING_VERSION")
# Where uploaded Bibles and fetched provider text are staged. No code default:
# the admin import page refuses to accept a file until an operator says where
# they want it kept, rather than scattering copies across the container.
BIBLE_IMPORT_DIR = os.getenv("BIBLE_IMPORT_DIR")
BIBLE_BOOKS_PATH = os.getenv("BIBLE_BOOKS_PATH")
# Key for the online translation providers declared in corpus_manifest.json.
# Deliberately no default: without it the provider reports that it is
# unconfigured and the admin page says which setting to set.
BIBLE_API_KEY = os.getenv("BIBLE_API_KEY")
# Where a translation provider's fetched chapters are kept between runs. Left
# unset it is derived from BIBLE_DATABASE_URL so a chapter is fetched once and
# never again; set it to put the cache somewhere else (a volume that survives a
# database rebuild is the point).
BIBLE_PROVIDER_CACHE = os.getenv("BIBLE_PROVIDER_CACHE")
# The most *new* HTTP calls one provider import may make, counted against the
# chapters that are not already cached. No code default: an operator on a metered
# plan decides their own ceiling, and an unset value means no ceiling.
BIBLE_PROVIDER_CALL_BUDGET = os.getenv("BIBLE_PROVIDER_CALL_BUDGET")
COMPOSE_PROJECT_DIR = os.getenv("COMPOSE_PROJECT_DIR")
EXECUTION_EXTERNAL_HOST = os.getenv("EXECUTION_EXTERNAL_HOST")
# Jarvis's own public address (e.g. https://jarvis.example.com). Companion
# devices paired from Jarvis (the watch) are given this URL to call it on.
# Unset, they get whatever address the user reached Jarvis at while pairing.
JARVIS_HOST = os.getenv("JARVIS_HOST")
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

# --- Alpaca backends SharedLLM calls directly (external to this compose) ---
# The SD backend (port 8081) and the arcade shelf (5001) are declared in
# gateway/config.py; the podcast backend is declared HERE as well because the
# execution handlers need it and they import from services.config, not from the
# gateway config. Port 5000 is alpaca's dashboard, which fronts the audio-server
# (8082) behind /api/audio/* and /api/podcast/*. Speaker identification no
# longer uses alpaca at all: see services/execution/voiceprint.py.
# No default on purpose: unset, the podcast handlers fail naming this variable
# instead of probing a guessed host.
ALPACA_WEB_URL = (os.getenv("ALPACA_WEB_URL") or "").rstrip("/")

# --- ntfy push server (docs/PUSH.md) ---
# Telemetry publishes notices and the gateway proxies phone streams through it
# as the ntfy user `jarvis`, whose access token is NTFY_TOKEN (.env only).
# Blank token: nothing is published and the phone stream answers 503.
NTFY_URL = (os.getenv("NTFY_URL") or "http://ntfy:80").rstrip("/")
NTFY_TOKEN = os.getenv("NTFY_TOKEN", "")


# --- Gateway shorthand aliases ---
IDENTITY_SVC = IDENTITY_SVC_URL
EXECUTION_SVC = EXECUTION_SVC_URL
RAG_SVC = RAG_SVC_URL
STORAGE_SVC = STORAGE_SVC_URL
LOGGING_SVC = LOGGING_SVC_URL
WORKSPACE_RUNTIME_SVC = WORKSPACE_RUNTIME_SVC_URL
TELEMETRY_SVC = TELEMETRY_SVC_URL
HEALTH_SVC = HEALTH_SVC_URL
GARDEN_SVC = GARDEN_SVC_URL

#: Settings whose blank value means "off", so a blank row in Identity clears the
#: .env value instead of being ignored. Only the integration switches: most
#: other settings are seeded blank on purpose (a blank there means "use .env"),
#: so treating every blank row as a clear would wipe working .env values. These
#: keys are never seeded, so a row for one only exists because someone saved it.
_BLANK_CLEARS = frozenset({"HA_URL", "HA_TOKEN", "HOMEHUB_URL"})

_INT_SETTINGS = frozenset({
    "LOG_RETENTION_DAYS", "LOG_MAX_ENTRIES", "ARTIFACT_LOCAL_QUOTA_BYTES", "RAVEN_MAX_TOTAL_SECONDS",
    "RAVEN_ITERATION_TIMEOUT", "RAVEN_HEARTBEAT_INTERVAL",
    "RAVEN_HUNG_THRESHOLD", "RAVEN_ERROR_THRESHOLD", "RAVEN_CHECK_INTERVAL",
    "DNS_POLL_INTERVAL", "WORKSPACE_RUNTIME_FILE_READ_LIMIT",
    "WORKSPACE_RUNTIME_PYTEST_TIMEOUT_SECONDS", "OLLAMA_TIMEOUT",
    "ALERT_ERROR_SPIKE_THRESHOLD", "ALERT_ERROR_REPEAT_THRESHOLD",
    "ALERT_DISK_FREE_PERCENT", "ALERT_DESKTOP_DOWN_MINUTES", "ALERT_TLS_DAYS",
})


def _apply_settings(settings: dict, settings_map: dict[str, str]) -> None:
    """Write Identity's settings over the .env values, once, at startup.

    A blank value is skipped (the .env value stands), except for the
    integration switches in ``_BLANK_CLEARS``: a ``homehub_url`` or ``ha_url``
    saved blank turns that integration off even when .env names one.
    """
    for setting_key, var_name in settings_map.items():
        if setting_key not in settings:
            continue
        value = settings[setting_key]
        if not (value.strip() if isinstance(value, str) else value):
            if var_name in _BLANK_CLEARS:
                globals()[var_name] = ""
            continue
        if var_name in _INT_SETTINGS:
            value = int(float(value))
        elif var_name == "FAST_PATH_THRESHOLD":
            value = float(value)
        elif var_name == "HA_URL":
            # Like the env value: a retired HA host answers through homehub.
            value = _ha_redirect(str(value).strip())
        elif var_name == "HOMEHUB_URL":
            value = str(value).strip().rstrip("/")
        globals()[var_name] = value


def homehub_enabled() -> bool:
    """Is the HomeHub integration on? (HOMEHUB_URL, resolved once at startup.)"""
    return bool(HOMEHUB_URL)


def home_assistant_enabled() -> bool:
    """Is the Home Assistant integration on? (HA_URL, resolved once at startup.)"""
    return bool(HA_URL)


def any_home_hub() -> bool:
    """Is either home integration (HomeHub or Home Assistant) on?"""
    return homehub_enabled() or home_assistant_enabled()


def integrations() -> dict[str, bool]:
    """Which optional home integrations are on: the one source of truth.

    An integration that is off is never contacted: no requests, no retries,
    no "is down" notices. Both come from this module's startup values (.env,
    then Identity's settings), never from the environment per call.
    """
    return {"homehub": homehub_enabled(), "home_assistant": home_assistant_enabled()}


def _log_integrations() -> None:
    off = [name for name, on in integrations().items() if not on]
    if off:
        log.info("Integrations off (not configured, never contacted): %s", ", ".join(off))


#: True once runtime config has been loaded from Identity (see keep_resolving_runtime_config).
RUNTIME_CONFIG_LOADED = False


async def resolve_runtime_config(
    max_retries: int = 5, retry_delay: float = 2.0, timeout: float = 10.0, quiet: bool = False,
) -> bool:
    """Fetch all runtime configuration from Identity service.

    The .env file is seed-only; Identity holds the authoritative runtime values.
    Call this at service startup before handling any requests. Returns True once
    the settings are applied, False when Identity could not be read (the .env
    seed values stay in effect; keep_resolving_runtime_config retries).
    """
    global RUNTIME_CONFIG_LOADED
    if _is_testing():
        return True

    import aiohttp

    # Service URLs are network-aware: Identity stores the BRIDGE_*/HOST_* pair
    # per service (seeded from .env, editable in the UI Settings page), and
    # each service reads the set matching its own NETWORK_MODE. The plain keys
    # (e.g. "rag_svc_url") are legacy bridge-mode defaults and must not be used
    # by host-network services.
    settings_map = {
        "llm_local_url": "LLM_URL",
        # These are Identity *setting keys*, which are lowercase by convention
        # (host_searxng_url, bridge_searxng_url; see services/identity/seed.py),
        # so NETWORK_MODE is interpolated lowercased here on purpose. Do NOT
        # "fix" these to .upper(): the env-var lookups above use uppercase
        # because that is how .env spells them (HOST_SEARXNG_URL), and the two
        # namespaces genuinely differ.
        f"{NETWORK_MODE}_execution_svc_url": "EXECUTION_SVC_URL",
        f"{NETWORK_MODE}_rag_svc_url": "RAG_SVC_URL",
        f"{NETWORK_MODE}_storage_svc_url": "STORAGE_SVC_URL",
        f"{NETWORK_MODE}_logging_svc_url": "LOGGING_SVC_URL",
        f"{NETWORK_MODE}_workspace_runtime_svc_url": "WORKSPACE_RUNTIME_SVC_URL",
        f"{NETWORK_MODE}_bible_svc_url": "BIBLE_SVC_URL",
        f"{NETWORK_MODE}_health_svc_url": "HEALTH_SVC_URL",
        f"{NETWORK_MODE}_searxng_url": "SEARXNG_URL",
        "geo_svc_url": "GEO_SVC_URL",
        "blb_base_url": "BLB_BASE_URL",
        "bible_devotional_dir": "BIBLE_DEVOTIONAL_DIR",
        "bible_import_dir": "BIBLE_IMPORT_DIR",
        "bible_books_path": "BIBLE_BOOKS_PATH",
        "bible_provider_cache": "BIBLE_PROVIDER_CACHE",
        "bible_provider_call_budget": "BIBLE_PROVIDER_CALL_BUDGET",
        "control_plane_url": "CONTROL_PLANE_URL",
        "ha_url": "HA_URL",
        "ha_token": "HA_TOKEN",
        "homehub_url": "HOMEHUB_URL",
        "calibre_library_path": "CALIBRE_LIBRARY_PATH",
        "nextcloud_url": "NEXTCLOUD_URL",
        "nextcloud_user": "NEXTCLOUD_USER",
        "nextcloud_pass": "NEXTCLOUD_PASS",
        "git_url": "GIT_URL",
        "git_user": "GIT_USER",
        "git_token": "GIT_TOKEN",
        # redis_url is infrastructure-dependent (Docker vs host networking), so read it from env only
        "log_retention_days": "LOG_RETENTION_DAYS",
        "log_max_entries": "LOG_MAX_ENTRIES",
        "artifact_local_quota_bytes": "ARTIFACT_LOCAL_QUOTA_BYTES",
        "rag_retention_days": "RAG_RETENTION_DAYS",
        "raven_max_total_seconds": "RAVEN_MAX_TOTAL_SECONDS",
        "raven_iteration_timeout": "RAVEN_ITERATION_TIMEOUT",
        "raven_heartbeat_interval": "RAVEN_HEARTBEAT_INTERVAL",
        "raven_hung_threshold": "RAVEN_HUNG_THRESHOLD",
        "raven_error_threshold": "RAVEN_ERROR_THRESHOLD",
        "raven_check_interval": "RAVEN_CHECK_INTERVAL",
        "alert_error_spike_threshold": "ALERT_ERROR_SPIKE_THRESHOLD",
        "alert_error_repeat_threshold": "ALERT_ERROR_REPEAT_THRESHOLD",
        "alert_disk_free_percent": "ALERT_DISK_FREE_PERCENT",
        "alert_desktop_down_minutes": "ALERT_DESKTOP_DOWN_MINUTES",
        "alert_tls_hosts": "ALERT_TLS_HOSTS",
        "alert_tls_days": "ALERT_TLS_DAYS",
        "default_model": "DEFAULT_MODEL",
        "assistant_model": "ASSISTANT_MODEL",
        "coding_model": "CODING_MODEL",
        "librarian_model": "LIBRARIAN_MODEL",
        "default_tts_voice": "DEFAULT_TTS_VOICE",
        "speech_server_url": "SPEECH_SERVER_URL",
        # The key the Settings voice picker saves (Admin > Raven > Spoken replies).
        "system_default_tts_voice": "DEFAULT_TTS_VOICE",
        "workspace_root": "WORKSPACE_ROOT",
        "workspace_runtime_root": "WORKSPACE_RUNTIME_ROOT",
        "models_dir": "MODELS_DIR",
        "phrasebook_path": "PHRASEBOOK_PATH",
        "announcement_blacklist": "ANNOUNCEMENT_BLACKLIST",
        "gateway_internal_url": "GATEWAY_INTERNAL_URL",
        "mass_config_entry_id": "MASS_CONFIG_ENTRY_ID",
        "audiobookshelf_url": "AUDIOBOOKSHELF_URL",
        "audiobookshelf_user": "AUDIOBOOKSHELF_USER",
        "audiobookshelf_pass": "AUDIOBOOKSHELF_PASS",
        "upstream_dns": "UPSTREAM_DNS",
        "dns_poll_interval": "DNS_POLL_INTERVAL",
        "embedding_model": "EMBEDDING_MODEL",
        "fast_path_threshold": "FAST_PATH_THRESHOLD",
        "temp_media_dir": "TEMP_MEDIA_DIR",
        "execution_external_host": "EXECUTION_EXTERNAL_HOST",
    }

    import asyncio

    max_retries = max(1, max_retries)
    for attempt in range(max_retries):
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout)) as client:
                resp = await client.get(
                    f"{IDENTITY_SVC_URL}/api/settings",
                    headers={"X-Internal-Secret": INTERNAL_SECRET}
                )
                if resp.status != 200:
                    log.warning(f"Failed to fetch runtime config from Identity (HTTP {resp.status})")
                    return False

                settings = {s["key"]: s["value"] for s in await resp.json()}

                _apply_settings(settings, settings_map)

                log.info("Runtime configuration loaded from Identity service")
                _log_integrations()

                # Update shorthand aliases (set at import time, stale after runtime resolve)
                globals()["EXECUTION_SVC"] = globals()["EXECUTION_SVC_URL"]
                globals()["IDENTITY_SVC"] = globals()["IDENTITY_SVC_URL"]
                globals()["RAG_SVC"] = globals()["RAG_SVC_URL"]
                globals()["STORAGE_SVC"] = globals()["STORAGE_SVC_URL"]
                globals()["LOGGING_SVC"] = globals()["LOGGING_SVC_URL"]
                globals()["WORKSPACE_RUNTIME_SVC"] = globals()["WORKSPACE_RUNTIME_SVC_URL"]
                globals()["GEO_SVC"] = globals()["GEO_SVC_URL"]
                globals()["BIBLE_SVC"] = globals()["BIBLE_SVC_URL"]
                globals()["HEALTH_SVC"] = globals()["HEALTH_SVC_URL"]

                # Check if special variables were updated in UI and sync to .env
                _sync_special_vars_to_env()

                RUNTIME_CONFIG_LOADED = True
                return True
        except Exception as e:
            reason = f"{type(e).__name__}: {e}"
            if attempt < max_retries - 1:
                log.warning(f"Identity not ready (attempt {attempt+1}/{max_retries}): {reason}")
                await asyncio.sleep(retry_delay)
            elif not quiet:
                log.warning(f"Failed to resolve runtime config from Identity after {max_retries} attempts: {reason}")
    return False


async def keep_resolving_runtime_config(
    first_delay: float = 2.0, max_delay: float = 60.0, sleep=None,
) -> None:
    """Retry resolve_runtime_config with backoff until Identity answers.

    For a service that started while Identity was away (an update swapping it,
    a restart): it keeps serving on its .env seed values, reports itself
    degraded, and picks up the real settings as soon as Identity is back,
    instead of giving up after a few attempts and never loading them.
    """
    import asyncio

    sleep = sleep or asyncio.sleep
    delay = first_delay
    attempts = 0
    log.warning("Runtime config not loaded from Identity yet; retrying in the background with backoff")
    while True:
        await sleep(delay)
        attempts += 1
        if await resolve_runtime_config(max_retries=1, timeout=5.0, quiet=True):
            log.warning(f"Runtime config loaded from Identity after {attempts} background attempt(s)")
            return
        delay = min(max_delay, delay * 2)


def _sync_special_vars_to_env():
    """Sync special variables (INTERNAL_SECRET, FERNET_KEY) from database to .env file.

    If these values were changed in the UI, update the .env file to match.
    This ensures the values are available for services that read from .env.
    """
    import os

    import dotenv

    # Try to find and update .env file
    # Search in common locations
    env_paths = [
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".env"),  # project root
        os.path.join(os.getcwd(), ".env"),  # current directory
    ]

    for env_path in env_paths:
        if os.path.exists(env_path):
            # Load current .env values
            env_vals = dotenv.dotenv_values(env_path)

            # Check if special vars in database differ from .env
            changes_made = False
            for var in ["INTERNAL_SECRET", "FERNET_KEY"]:
                db_value = globals().get(var)
                env_value = env_vals.get(var)

                if db_value and env_value and db_value != env_value:
                    log.info(f"Special variable {var} differs between database and .env. Updating .env.")
                    changes_made = True
                    break

            if changes_made:
                # Re-write .env with updated values (preserve comments and order)
                _update_env_file(env_path, {"INTERNAL_SECRET": globals().get("INTERNAL_SECRET"), "FERNET_KEY": globals().get("FERNET_KEY")})
                log.info("Special variables synced to .env file")
            break


def _update_env_file(filepath: str, updates: dict):
    """Update specific keys in a .env file while preserving comments and structure."""
    try:
        with open(filepath) as f:
            lines = f.readlines()

        updated = False
        new_lines = []
        for line in lines:
            for key in updates:
                if line.strip().startswith(f"{key}="):
                    new_lines.append(f"{key}={updates[key]}\n")
                    updated = True
                    break
            else:
                new_lines.append(line)

        if updated:
            with open(filepath, "w") as f:
                f.writelines(new_lines)
    except Exception as e:
        log.error(f"Failed to update .env file {filepath}: {e}")

CONFIG = {
    "assistant_model": ASSISTANT_MODEL or "",
    "librarian_model": LIBRARIAN_MODEL or "",
    "coding_model": CODING_MODEL or "",
    "mass_config_entry_id": MASS_CONFIG_ENTRY_ID or "",
}

# --- Special variables required for application startup ---
# These are used for authentication/encryption and must be set in .env
# If changed in UI, update .env to match the database values
_SPECIAL_REQUIRED = ["INTERNAL_SECRET", "FERNET_KEY"]

_MISSING_SPECIAL: list[str] = []
for _v in _SPECIAL_REQUIRED:
    if not os.getenv(_v):
        _MISSING_SPECIAL.append(_v)

if _MISSING_SPECIAL and not _is_testing():
    logging.basicConfig(level="CRITICAL")
    logging.critical(
        f"FATAL: Missing special required environment variables: {', '.join(_MISSING_SPECIAL)}\n"
        f"These variables (INTERNAL_SECRET, FERNET_KEY) are required for authentication/encryption.\n"
        "They must be set in .env and kept in sync with Identity Service database.\n"
        "If changed in the UI, update .env to match the database values."
    )
    sys.exit(1)

# --- Note: IDENTITY_SVC_URL has a default for the Identity service bootstrap ---
