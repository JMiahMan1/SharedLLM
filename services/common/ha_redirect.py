"""Point stored Home Assistant URLs at homehub without rewriting anyone's settings.

Every account keeps the HA URL it was set up with (e.g. ``https://ha.example.com``).
``HA_URL_REDIRECTS`` maps such a base URL to the hub that now answers for it:

    HA_URL_REDIRECTS=https://ha.example.com=http://hub.lan:8123

Music Assistant ran on the same host, so its stored URLs (``mass_url``) go
through the same map, e.g. ``http://ha.example.com:8095=http://hub.lan:8095``.
Several pairs are comma-separated. Matching is on scheme+host+port, so a stored
URL with a trailing slash or path still redirects. The accounts' HA tokens keep
working because the hub verifies them against the refresh tokens it imported.
Unset, nothing changes.
"""
from __future__ import annotations

import os
from urllib.parse import urlsplit


def _origin(url: str) -> str:
    parts = urlsplit(url.strip())
    if not parts.scheme or not parts.hostname:
        return ""
    port = parts.port or (443 if parts.scheme == "https" else 80)
    return f"{parts.scheme.lower()}://{parts.hostname.lower()}:{port}"


def redirects(raw: str | None = None) -> dict[str, str]:
    raw = os.getenv("HA_URL_REDIRECTS", "") if raw is None else raw
    out: dict[str, str] = {}
    for pair in raw.split(","):
        src, sep, dst = pair.partition("=")
        if sep and _origin(src) and _origin(dst):
            out[_origin(src)] = dst.strip().rstrip("/")
    return out


def redirect(url: str | None, raw: str | None = None) -> str | None:
    """The URL Jarvis should call for a stored HA URL (unchanged when not redirected)."""
    if not url:
        return url
    target = redirects(raw).get(_origin(url))
    return target if target else url
