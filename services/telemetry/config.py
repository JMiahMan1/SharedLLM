# services/telemetry/config.py
import os

from services.config import (
    GEO_SVC_URL,
    IDENTITY_SVC_URL,
    INTERNAL_SECRET,
    REDIS_URL,
    _net_url,
)

GATEWAY_INTERNAL_URL = (
    os.getenv("GATEWAY_INTERNAL_URL") or _net_url("GATEWAY", "http://gateway:11435")
).rstrip("/")
AUTOMATION_SVC_URL = (
    os.getenv("AUTOMATION_SVC_URL") or _net_url("AUTOMATION", "http://automation:11437")
).rstrip("/")

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
