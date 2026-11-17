# services/telemetry/config.py
import os

from services.config import (
    GEO_SVC_URL,
    IDENTITY_SVC_URL,
    _net_url,
)
from services.config import INTERNAL_SECRET as INTERNAL_SECRET
from services.config import REDIS_URL as REDIS_URL

GATEWAY_INTERNAL_URL = (
    os.getenv("GATEWAY_INTERNAL_URL") or _net_url("GATEWAY", "http://gateway:11435")
).rstrip("/")
AUTOMATION_SVC_URL = (
    os.getenv("AUTOMATION_SVC_URL") or _net_url("AUTOMATION", "http://automation:11437")
).rstrip("/")

#: The device hub: the Common Sense Media app check is its integration (GET /hub/integrations/{domain}).
HOMEHUB_URL = os.getenv("HOMEHUB_URL", "http://host.docker.internal:8123").rstrip("/")

GEO_SVC = (GEO_SVC_URL or "").rstrip("/")
IDENTITY_SVC = (IDENTITY_SVC_URL or "").rstrip("/")

# Scheduler poll cadence. Short enough that a report starts close to its
# configured time, long enough not to hammer Redis.
SCHEDULER_TICK_SECONDS = 30
WORKER_TICK_SECONDS = 20

# When Alpaca is busy we defer the run without consuming an attempt, so a report
# never competes with an in-flight voice/assistant task.
BUSY_DEFER_SECONDS = 300
MAX_ATTEMPTS = 5
RETRY_BASE_SECONDS = 60
RETRY_MAX_SECONDS = 3600
JOB_LOCK_TTL_SECONDS = 900

# Ask Alpaca's shared slot queue to hold the request rather than fail when a
# slot frees up shortly after our pre-check.
LLM_QUEUE_TIMEOUT_SECONDS = 180
LLM_TIMEOUT_SECONDS = 300

NOTIFICATION_HISTORY = 50

# App vetting (services/telemetry/app_catalog.py). Read once at startup.
def _env_bool(name: str, default: bool) -> bool:
    raw = (os.getenv(name) or "").strip().lower()
    if not raw:
        return default
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    raise RuntimeError(f"{name} must be true or false, not {raw!r}")


#: Whether the server looks apps up on their public Google Play listing (read
#: only, no account; only the package name is sent). Off: listings show
#: "lookups are switched off" and apps are judged on their permissions alone.
APP_CATALOG_ENABLED = _env_bool("APP_CATALOG_ENABLED", True)
#: A listing (and its review) is looked up again after this many days.
APP_CATALOG_TTL_DAYS = int(os.getenv("APP_CATALOG_TTL_DAYS", "7"))
#: At least this long between two requests to Google Play.
APP_CATALOG_MIN_INTERVAL_S = float(os.getenv("APP_CATALOG_MIN_INTERVAL_S", "15"))
#: After Google refuses or fails, wait this long, doubling up to the maximum.
APP_CATALOG_BACKOFF_BASE_S = int(os.getenv("APP_CATALOG_BACKOFF_BASE_S", "600"))
APP_CATALOG_BACKOFF_MAX_S = int(os.getenv("APP_CATALOG_BACKOFF_MAX_S", str(12 * 3600)))
APP_CATALOG_FETCH_TIMEOUT_S = float(os.getenv("APP_CATALOG_FETCH_TIMEOUT_S", "20"))
#: How long the local model may take to read one listing.
APP_REVIEW_TIMEOUT_S = float(os.getenv("APP_REVIEW_TIMEOUT_S", "120"))
#: A device that stops reporting its apps is forgotten after this many days.
APP_INVENTORY_TTL_DAYS = int(os.getenv("APP_INVENTORY_TTL_DAYS", "60"))
#: Jarvis's own Android package: never listed among a child's apps.
JARVIS_ANDROID_PACKAGE = os.getenv("JARVIS_ANDROID_PACKAGE", "com.jarvisos.app")
#: Common Sense Media review pages (on while the hub's Common Sense Media
#: integration is set up): at least this long between
#: two requests, a found or missing review is looked for again after the TTL,
#: and a refusal or failure backs off, doubling up to the maximum.
COMMON_SENSE_MIN_INTERVAL_S = float(os.getenv("COMMON_SENSE_MIN_INTERVAL_S", "20"))
COMMON_SENSE_TTL_DAYS = int(os.getenv("COMMON_SENSE_TTL_DAYS", "30"))
COMMON_SENSE_BACKOFF_BASE_S = int(os.getenv("COMMON_SENSE_BACKOFF_BASE_S", "900"))
COMMON_SENSE_BACKOFF_MAX_S = int(os.getenv("COMMON_SENSE_BACKOFF_MAX_S", str(24 * 3600)))
#: abuse.ch MalwareBazaar lookups of app file hashes (the Auth-Key is the
#: device user's own abuse.ch key in Accounts & Integrations, else the household
#: default): spacing, how long a "not a known sample" answer is trusted, and the
#: backoff after a failure (a refused key backs off on its own, per key).
MALWAREBAZAAR_MIN_INTERVAL_S = float(os.getenv("MALWAREBAZAAR_MIN_INTERVAL_S", "5"))
MALWAREBAZAAR_TTL_DAYS = int(os.getenv("MALWAREBAZAAR_TTL_DAYS", "7"))
MALWAREBAZAAR_BACKOFF_BASE_S = int(os.getenv("MALWAREBAZAAR_BACKOFF_BASE_S", "600"))
MALWAREBAZAAR_BACKOFF_MAX_S = int(os.getenv("MALWAREBAZAAR_BACKOFF_MAX_S", str(24 * 3600)))
