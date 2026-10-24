"""In-app links a stored notice may open (timers, place reminders)."""
from __future__ import annotations


def app_link(value: object) -> str | None:
    """An in-app path a notice may open (``/notes?note=…``), or None.

    Only a path inside the app is kept: no scheme, no ``//host``, no
    backslashes, so a stored reminder can never send someone off-site.
    """
    if not isinstance(value, str):
        return None
    link = value.strip()
    if not link.startswith("/") or link.startswith("//") or "\\" in link or len(link) > 512:
        return None
    if any(ch in link for ch in "\r\n\t") or ":" in link.split("?", 1)[0]:
        return None
    return link
