# services/execution/media_links.py
"""Signed URL helpers for the execution media file server (port 8888).

Media URLs are fetched header-less by devices (HA, Roku, browsers), so the
only credential they can present is a short-lived signed media token in the
query string (§7.4 of docs/MEDIA_OVERHAUL.md): ``?user=...&mt=...``.
"""
from urllib.parse import quote

from services.shared.media_token import DEVICE_TOKEN_TTL_SECONDS, sign


def media_file_url(media_id: str, user: str, host: str) -> str:
    """Build ``http://<host>:8888/media/<id>?user=<user>&mt=<signed token>``."""
    token, _exp = sign(str(user or ""), ttl=DEVICE_TOKEN_TTL_SECONDS)
    return (
        f"http://{host}:8888/media/{quote(str(media_id), safe='')}"
        f"?user={quote(str(user or ''))}&mt={token}"
    )
