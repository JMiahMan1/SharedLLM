"""Shared utilities for gateway modules to avoid circular imports."""
from __future__ import annotations

import logging
from typing import Any

from services.common.secrets import secret_matches
from services.gateway.http_client import shared_http_client

log = logging.getLogger("gateway.shared")

__all__ = ["shared_http_client", "secret_matches", "log"]