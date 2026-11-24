"""The book catalogues the library fills books in from: Google Books and Open Library.

Both are integrations in Settings > Accounts & Integrations (docs/CALIBRE_LIBRARY.md),
stored per person by identity, with the system default user's settings as the
household default for anyone who has not set their own:

* **Google Books** needs an API key (free, from Google Cloud Console). It is off
  until it is turned on; turned on without a key it is *not* asked keyless (Google's
  shared keyless quota answers 429 most days): every lookup says the key is missing.
* **Open Library** needs no key. It is on unless it is turned off. Open Library asks
  automated clients to name a contact in their User-Agent; the optional contact
  email goes there.

Pure: the settings' shape and how a person's settings fall back to the household's,
what each catalogue's status is, the test lookup's URL and the words for each
error. Identity and storage both import it, so the two cannot disagree.
"""
from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import quote

GOOGLE_BOOKS = "google_books"
OPEN_LIBRARY = "open_library"
SOURCES = (GOOGLE_BOOKS, OPEN_LIBRARY)
NAMES = {GOOGLE_BOOKS: "Google Books", OPEN_LIBRARY: "Open Library"}

#: The credential service name (identity's credential_sources["google_books"]),
#: the plain field the forms send, and its encrypted column on User.
KEY_SERVICE = GOOGLE_BOOKS
KEY_FIELD = "google_books_key"
KEY_COLUMN = "google_books_key_enc"

#: Whether each catalogue is asked when nobody (not the person, not the household)
#: has said. Open Library needs nothing set up, the way Skylight is on until turned
#: off; Google Books needs a key first, the way abuse.ch is not set up until it has one.
DEFAULT_ENABLED = {GOOGLE_BOOKS: False, OPEN_LIBRARY: True}
DEFAULT_ORDER = (GOOGLE_BOOKS, OPEN_LIBRARY)

#: Where a person changes them, in every message that sends them there.
WHERE = "Settings > Accounts & Integrations"

#: Open Library's request: name the application and a way to reach its operator.
CLIENT_NAME = "JarvisOS-Library/1.0"

#: The book the Test button looks up (a long-printed classic, so any catalogue has it).
TEST_ISBN = "9780140449136"
TEST_TITLE = "The Brothers Karamazov"

_EMAIL = re.compile(r"^[^@\s<>()]+@[^@\s<>()]+\.[^@\s<>()]+$")


class SettingsError(ValueError):
    """A book_metadata settings update that cannot be saved (said to the person as is)."""


def parse(raw: Any) -> dict[str, Any]:
    """A stored settings value (JSON string or dict) as a dict; anything unreadable is {}."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw) if raw.strip() else {}
        except ValueError:
            return {}
    return raw if isinstance(raw, dict) else {}


def clean_update(update: Any, current: Any = None) -> str | None:
    """Validate a settings update and merge it over ``current``: the JSON to store.

    Accepted keys: ``google_books_enabled`` / ``open_library_enabled`` (bool or None to
    follow the household), ``open_library_email`` ("" or None clears it) and ``order``
    (the catalogues, first asked first). Raises SettingsError naming what is wrong.
    """
    if not isinstance(update, dict):
        raise SettingsError("book_metadata must be an object")
    allowed = {"google_books_enabled", "open_library_enabled", "open_library_email", "order"}
    unknown = sorted(set(update) - allowed)
    if unknown:
        raise SettingsError(f"book_metadata does not have {', '.join(unknown)}")
    merged = dict(parse(current))
    for key in ("google_books_enabled", "open_library_enabled"):
        if key in update:
            value = update[key]
            if value is not None and not isinstance(value, bool):
                raise SettingsError(f"{key} must be true, false or null")
            if value is None:
                merged.pop(key, None)
            else:
                merged[key] = value
    if "open_library_email" in update:
        email = str(update["open_library_email"] or "").strip()
        if email and (len(email) > 254 or not _EMAIL.match(email)):
            raise SettingsError("open_library_email must be an email address")
        if email:
            merged["open_library_email"] = email
        else:
            merged.pop("open_library_email", None)
    if "order" in update:
        order = update["order"]
        if order is None:
            merged.pop("order", None)
        else:
            if not isinstance(order, list) or sorted(order) != sorted(SOURCES):
                raise SettingsError(f"order must list each of {', '.join(SOURCES)} once")
            merged["order"] = list(order)
    return json.dumps(merged, sort_keys=True) if merged else None


def _pick(own: dict[str, Any], household: dict[str, Any], key: str, default: Any) -> tuple[Any, str]:
    if key in own:
        return own[key], "own"
    if key in household:
        return household[key], "household"
    return default, "default"


def resolve(own_raw: Any, household_raw: Any, *, key: str | None, key_source: str) -> dict[str, Any]:
    """A person's catalogue settings, each value their own, else the household's, else the default.

    ``key`` / ``key_source`` are the Google Books key already resolved the same way
    (identity's household-default credentials: "own", "household" or "absent").
    The answer holds the key: it goes to internal callers only (``/api/resolve``);
    ``public`` drops it for anything a browser sees.
    """
    own, household = parse(own_raw), parse(household_raw)
    order, order_from = _pick(own, household, "order", list(DEFAULT_ORDER))
    if not isinstance(order, list) or sorted(order) != sorted(SOURCES):
        order, order_from = list(DEFAULT_ORDER), "default"
    google_on, google_from = _pick(own, household, "google_books_enabled", DEFAULT_ENABLED[GOOGLE_BOOKS])
    open_on, open_from = _pick(own, household, "open_library_enabled", DEFAULT_ENABLED[OPEN_LIBRARY])
    email, email_from = _pick(own, household, "open_library_email", "")
    key = (key or "").strip() or None
    sources = {
        GOOGLE_BOOKS: {
            "name": NAMES[GOOGLE_BOOKS], "enabled": bool(google_on), "enabled_from": google_from,
            "api_key": key, "key_source": key_source if key else "absent",
        },
        OPEN_LIBRARY: {
            "name": NAMES[OPEN_LIBRARY], "enabled": bool(open_on), "enabled_from": open_from,
            "contact_email": str(email or ""), "contact_email_from": email_from if email else "default",
        },
    }
    for name, source in sources.items():
        source["ready"], source["reason"] = _readiness(name, source)
    return {"order": list(order), "order_from": order_from, "sources": sources}


def _readiness(name: str, source: dict[str, Any]) -> tuple[bool, str]:
    """(will it be asked, and if not why), in the words the library page shows."""
    label = NAMES[name]
    if not source["enabled"]:
        if name == GOOGLE_BOOKS and not source.get("api_key"):
            return False, f"{label}: not set up (add an API key in {WHERE})"
        return False, f"{label}: turned off in {WHERE}"
    if name == GOOGLE_BOOKS and not source.get("api_key"):
        return False, f"{label}: turned on but no API key is set in {WHERE}"
    return True, ""


def public(resolved: dict[str, Any]) -> dict[str, Any]:
    """``resolve``'s answer without the key: what a browser may see."""
    out = {k: v for k, v in resolved.items() if k != "sources"}
    out["sources"] = {
        name: {k: v for k, v in source.items() if k != "api_key"} | (
            {"key_set": bool(source.get("api_key"))} if name == GOOGLE_BOOKS else {})
        for name, source in (resolved.get("sources") or {}).items()
    }
    return out


def user_agent(contact_email: str = "") -> str:
    """The User-Agent catalogue requests carry: the app, and the contact Open Library asks for."""
    email = (contact_email or "").strip()
    return f"{CLIENT_NAME} ({email})" if email else CLIENT_NAME


def google_books_url(query: str, api_key: str) -> str:
    return f"https://www.googleapis.com/books/v1/volumes?maxResults=3&q={quote(query)}&key={quote(api_key)}"


def open_library_search_url(query: str) -> str:
    return f"https://openlibrary.org/search.json?limit=1&{query}"


def google_error(status: int, body: Any = None) -> str:
    """What a Google Books answer that is not a result means, for the person."""
    reason = ""
    if isinstance(body, dict):
        error = body.get("error") if isinstance(body.get("error"), dict) else {}
        details = error.get("errors") if isinstance(error.get("errors"), list) else []
        reason = str((details[0] or {}).get("reason") or "") if details and isinstance(details[0], dict) else ""
        message = str(error.get("message") or "")
        if not reason and "API key not valid" in message:
            reason = "keyInvalid"
    if status == 429 or reason in ("rateLimitExceeded", "dailyLimitExceeded", "quotaExceeded"):
        return (f"{NAMES[GOOGLE_BOOKS]} answered 429: the API key's daily quota is used up. "
                "It resets at midnight Pacific time, or raise the quota in Google Cloud Console.")
    if reason == "keyInvalid" or status == 400:
        return f"{NAMES[GOOGLE_BOOKS]} refused the API key (HTTP {status}: not a valid key). Check it in {WHERE}."
    if status == 403:
        why = f", {reason}" if reason else ""
        return (f"{NAMES[GOOGLE_BOOKS]} refused the API key (HTTP 403{why}): turn on the Books API for it in "
                "Google Cloud Console, or check its restrictions.")
    return f"{NAMES[GOOGLE_BOOKS]} answered HTTP {status}."


def open_library_error(status: int) -> str:
    if status == 429:
        return f"{NAMES[OPEN_LIBRARY]} answered 429: too many requests. Try again in a few minutes."
    return f"{NAMES[OPEN_LIBRARY]} answered HTTP {status}."


def unreachable(name: str) -> str:
    return f"{NAMES.get(name, name)} did not answer. Try again in a few minutes."
