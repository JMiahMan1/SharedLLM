"""
Gateway configuration.

NO .env imports for service URLs or credentials.
All runtime settings come from Identity service at runtime.
.env is seed-only (used only by Identity's /api/admin/seed endpoint).

Only INTERNAL_SECRET is read from the environment (set by docker-compose).
"""
import os

# --- Inter-service auth (set by docker-compose) ---
INTERNAL_SECRET = os.getenv("INTERNAL_SECRET", "")

# --- Constants (not user-configurable) ---
SYSTEM_IDENTITY = "raven_system"

# --- CONFIG dict for backward compat (resolved at runtime from Identity) ---
CONFIG = {
    "assistant_model": "",
    "librarian_model": "",
    "coding_model": "",
}
