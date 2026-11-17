"""The abuse.ch Auth-Key in Accounts & Integrations (docs/APP_VETTING.md).

App vetting looks each app file's SHA-256 up on abuse.ch MalwareBazaar. The
key is a per-person login like the others (``User.malwarebazaar_key_enc``),
with one rule of its own: the system default user's key is the **household
default**, used for everyone who has not set their own, with no admin grant
needed. A person's own key wins.

A key is checked with abuse.ch before it is saved: one lookup of the empty
file's SHA-256 (``malwarebazaar.KEY_CHECK_SHA256``), so nothing about the
family goes out. A refused key is not saved; abuse.ch not answering is an
error too, never a silent save. ``lookup`` is the one network call, so the
tests replace it. The key is never logged.
"""
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from services.common import malwarebazaar

log = logging.getLogger("identity.abuse_ch")

#: The credential service name (credential_sources["abuse_ch"]).
SERVICE = "abuse_ch"
#: The plain field the forms send, and its encrypted column on User.
FIELD = "malwarebazaar_key"
COLUMN = "malwarebazaar_key_enc"
TIMEOUT_S = 15.0

REFUSED = "abuse.ch refused this Auth-Key, so it was not saved. Check it at auth.abuse.ch."
UNREACHABLE = "abuse.ch did not answer, so the Auth-Key was not saved. Try again in a few minutes."


class KeyRefused(ValueError):
    """abuse.ch said the key is not a working MalwareBazaar Auth-Key."""


class Unreachable(RuntimeError):
    """abuse.ch could not be asked, or answered something that is not an answer."""


def lookup(auth_key: str, sha256: str) -> tuple[int, Any]:
    """POST one hash to MalwareBazaar: ``(status, JSON body or None)``."""
    data = urllib.parse.urlencode(malwarebazaar.request_form(sha256)).encode()
    req = urllib.request.Request(malwarebazaar.API_URL, data=data, method="POST",
                                 headers=malwarebazaar.request_headers(auth_key))
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
            status, raw = resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, None
    try:
        return status, json.loads(raw)
    except ValueError:
        return status, None


def check_key(auth_key: str) -> None:
    """Raise KeyRefused or Unreachable unless abuse.ch accepts ``auth_key``."""
    try:
        status, body = lookup(auth_key, malwarebazaar.KEY_CHECK_SHA256)
    except Exception as exc:
        # The type only: a urllib error's text can carry the request.
        log.warning("abuse.ch could not be asked to check an Auth-Key (%s)", type(exc).__name__)
        raise Unreachable(UNREACHABLE) from None
    try:
        malwarebazaar.parse_response(status, body, malwarebazaar.KEY_CHECK_SHA256)
    except malwarebazaar.AuthKeyRejected:
        log.info("abuse.ch refused an Auth-Key; it was not saved")
        raise KeyRefused(REFUSED) from None
    except LookupError as exc:
        if status == 200 and isinstance(body, dict) and body.get("query_status") == "ok":
            return  # it answered about the hash: the key works
        log.warning("abuse.ch gave no usable answer while checking an Auth-Key: %s", exc)
        raise Unreachable(UNREACHABLE) from None


def status_for(own_enc: str | None, household_enc: str | None) -> str:
    """What the account page shows: "own", "household" or "not_set_up"."""
    if own_enc:
        return "own"
    if household_enc:
        return "household"
    return "not_set_up"
