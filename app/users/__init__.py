"""User credentials module for the SharedLLM monolith.

Provides get_user_creds() as a drop-in replacement for the legacy app/users.py.
When running in a Docker environment, delegates to the Identity Service.
When running standalone, returns default credentials from config.
"""
from __future__ import annotations

import os
import logging
from typing import Dict, Optional

log = logging.getLogger("app.users")

_IDENTITY_SVC_URL = os.environ.get("IDENTITY_SVC_URL", "http://localhost:8001")
_INTERNAL_SECRET = os.environ.get("INTERNAL_SECRET", "change-me-in-production")


def get_user_creds(username: str = "default") -> Dict[str, Optional[str]]:
    """
    Resolve user credentials from the Identity Service or fallback to defaults.
    Returns a dict with keys: user, ha_url, ha_token, nextcloud_url, nextcloud_user,
    nextcloud_pass, audiobookshelf_url, audiobookshelf_user, audiobookshelf_pass.
    """
    import requests as _requests

    url = f"{_IDENTITY_SVC_URL.rstrip('/')}/api/resolve"
    headers = {"X-Internal-Secret": _INTERNAL_SECRET, "Content-Type": "application/json"}
    payload = {"rag_user": username}

    try:
        resp = _requests.post(url, json=payload, headers=headers, timeout=5.0)
        if resp.status_code == 404:
            if username != "default":
                return get_user_creds("default")
            raise RuntimeError(f"No user '{username}' found in Identity Service")
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        log.warning(
            f"[app/users] Identity Service unavailable for '{username}': {e}. "
            "Returning safe defaults."
        )
        return {
            "user": username,
            "nextcloud_url": None,
            "nextcloud_user": None,
            "nextcloud_pass": None,
            "ha_url": os.environ.get("HA_URL", "http://localhost:8123"),
            "ha_token": os.environ.get("HA_TOKEN", ""),
            "audiobookshelf_url": None,
            "audiobookshelf_user": None,
            "audiobookshelf_pass": None,
        }


def get_all_users() -> Dict[str, Dict]:
    """Return a dict of known users. Currently returns only the default."""
    default_creds = get_user_creds("default")
    return {"default": default_creds}


def get_user_config(username: str) -> Dict:
    """Alias for get_user_creds for backwards compatibility."""
    return get_user_creds(username)
