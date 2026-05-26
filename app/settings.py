"""Minimal stub for app.settings to support legacy scripts."""
from __future__ import annotations

import logging

log = logging.getLogger("app")

SERVER_URL: str = "http://localhost:8000"
CHROMA_DIR: str = "/data/chroma_db"


class GlobalResources:
    """Stub for GlobalResources."""

    pass


def load_resources() -> None:
    """Stub for load_resources."""


def get_user_creds() -> dict:
    """Stub for get_user_creds."""
    return {}


HA_URL: str = "http://localhost:8123"
