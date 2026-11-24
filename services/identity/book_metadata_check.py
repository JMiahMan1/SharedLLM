"""The Test button on the Google Books and Open Library cards (Accounts & Integrations).

One lookup of a known book (``book_metadata.TEST_ISBN``) with the settings that
would be used, the key or contact email being typed taking the place of the
saved one. The answer is the exact outcome: the book found, or what the
catalogue said (a refused key, a used-up quota, no answer). ``fetch`` is the
one network call, so the tests replace it. The key is never logged.
"""
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from typing import Any

from services.common import book_metadata

log = logging.getLogger("identity.book_metadata")

TIMEOUT_S = 12.0


def fetch(url: str, headers: dict[str, str]) -> tuple[int, Any]:
    """GET ``url``: ``(status, JSON body or None)``."""
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
            status, raw = resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read() or b"null")
        except ValueError:
            return exc.code, None
    try:
        return status, json.loads(raw)
    except ValueError:
        return status, None


def _ask(source: str, url: str, headers: dict[str, str]) -> tuple[int, Any]:
    try:
        return fetch(url, headers)
    except Exception as exc:
        # The type only: a urllib error's text can carry the URL, and with it the key.
        log.warning("%s could not be asked for the test lookup (%s)", book_metadata.NAMES[source], type(exc).__name__)
        return 0, None


def test_source(source: str, *, api_key: str | None, contact_email: str) -> dict[str, Any]:
    """``{"ok", "source", "message", "status"}`` for one test lookup."""
    name = book_metadata.NAMES[source]
    headers = {"User-Agent": book_metadata.user_agent(contact_email), "Accept": "application/json"}
    if source == book_metadata.GOOGLE_BOOKS:
        if not api_key:
            return {"ok": False, "source": source, "status": None,
                    "message": f"{name}: no API key to test. Paste one, or add it in {book_metadata.WHERE}."}
        status, body = _ask(source, book_metadata.google_books_url(f"isbn:{book_metadata.TEST_ISBN}", api_key), headers)
        if status == 0:
            return {"ok": False, "source": source, "status": None, "message": book_metadata.unreachable(source)}
        if status != 200:
            return {"ok": False, "source": source, "status": status, "message": book_metadata.google_error(status, body)}
        items = (body or {}).get("items") if isinstance(body, dict) else None
        found = ((items or [{}])[0].get("volumeInfo") or {}).get("title") if items else None
    else:
        url = book_metadata.open_library_search_url(f"isbn={book_metadata.TEST_ISBN}&fields=title")
        status, body = _ask(source, url, headers)
        if status == 0:
            return {"ok": False, "source": source, "status": None, "message": book_metadata.unreachable(source)}
        if status != 200:
            return {"ok": False, "source": source, "status": status, "message": book_metadata.open_library_error(status)}
        docs = (body or {}).get("docs") if isinstance(body, dict) else None
        found = (docs or [{}])[0].get("title") if docs else None
    if not found:
        return {"ok": False, "source": source, "status": status,
                "message": f"{name} answered, but did not find the test book (ISBN {book_metadata.TEST_ISBN})."}
    return {"ok": True, "source": source, "status": status,
            "message": f"{name} works: it found “{found}” (ISBN {book_metadata.TEST_ISBN})."}
