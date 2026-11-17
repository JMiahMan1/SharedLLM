"""Export my data, and restore it (docs/ACCOUNT_EXPORT.md).

The export is encrypted on the person's device, never here: this module only
gathers the person's data from each service over their internal routes and
streams it to the signed-in device as NDJSON, one line at a time. The device
derives the key from the password the person chose and encrypts every chunk
before anything is saved (services/ui/src/lib/accountExport/crypto.ts), so the
server never sees the password or the key, and no clear copy is written to
the server's disk (nothing here touches a file).

Routes:

* ``POST /api/account/export`` ``{"user"?}``: the stream. Lines, in order:
  ``{"kind": "manifest", ...}``, one ``{"kind": "section", "service", "collections"}``
  per service (the geo location trail follows in further pages), and
  ``{"kind": "end", "sections", "failed"}``. A service that cannot answer gets a
  ``section`` with ``error`` instead, and is named in ``failed``: the device says
  so rather than saving a file that looks whole.
* ``POST /api/account/import`` ``{"user"?, "service", "collections", "dry_run", "replace_conflicts"}``:
  one service's part of an opened file, merged into the account (``dry_run``
  for the summary first). The data comes from the device, decrypted there.

Who may: anyone, for their own account. For another person, only an admin
whom identity says may manage them (as Admin > Users already allows). Every
export and every applied restore is written to identity's audit log.

Both are rate limited per caller (``ACCOUNT_EXPORT_COOLDOWN_S`` between
exports, ``ACCOUNT_IMPORT_MAX_PER_WINDOW`` restore calls per
``ACCOUNT_IMPORT_WINDOW_S``), read once at startup.
"""
from __future__ import annotations

import json
import logging
import os
import time
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import aiohttp
from fastapi import APIRouter, Body, HTTPException, Request
from fastapi.responses import StreamingResponse

from services.gateway.config import INTERNAL_SECRET

log = logging.getLogger("gateway.account_export")

router = APIRouter(prefix="/api/account")

FORMAT = "jarvis-account-export"
VERSION = 1
EXPORT_COOLDOWN_S = float(os.getenv("ACCOUNT_EXPORT_COOLDOWN_S", "60"))
IMPORT_WINDOW_S = float(os.getenv("ACCOUNT_IMPORT_WINDOW_S", "600"))
IMPORT_MAX_PER_WINDOW = int(os.getenv("ACCOUNT_IMPORT_MAX_PER_WINDOW", "60"))
HISTORY_PAGE = 5000
_TIMEOUT = aiohttp.ClientTimeout(total=120, connect=5)

#: The services in an export, in restore order, and where their internal routes live.
SERVICES: tuple[tuple[str, str, str], ...] = (
    ("identity", "IDENTITY_SVC", "/api/internal/account"),
    ("health", "HEALTH_SVC", "/internal/account"),
    ("bible", "BIBLE_SVC", "/internal/account"),
    ("geo", "GEO_SVC", "/internal/account"),
)

#: What is left out on purpose, and why (shown by the device and in the file).
EXCLUDED: tuple[dict[str, str], ...] = (
    {"what": "Passwords, API keys and stored service logins", "why": "Secrets are never exported, not even encrypted. Sign in again after a restore."},
    {"what": "Voice signature", "why": "A biometric template; enrol your voice again instead."},
    {"what": "Notes, recipes, calendar, files and family chat", "why": "They live in your Nextcloud account, which has its own export."},
    {"what": "The parents' private chat", "why": "It has its own encrypted backup, opened with the chat password."},
    {"what": "Screen time and app activity reports", "why": "Kept for the household's reports for a limited time; not part of an account copy."},
    {"what": "Other people's data", "why": "Shared lists, places and trips keep only what is yours."},
)

_last_export: dict[str, float] = {}
_imports: dict[str, list[float]] = {}


def _service_url(attr: str) -> str:
    from services.gateway import main as gateway

    return str(getattr(gateway, attr, "") or "").rstrip("/")


def _headers() -> dict[str, str]:
    return {"X-Internal-Secret": INTERNAL_SECRET}


async def _subject_for(request: Request, requested: str | None) -> tuple[str, str]:
    """(caller, subject) after checking the caller may act for the subject."""
    from services.gateway import main as gateway

    ident = await gateway._acting_identity(request)
    if not ident:
        raise HTTPException(status_code=401, detail="Authentication required")
    caller = str(ident["user"]).strip().lower()
    subject = (requested or caller).strip().lower()
    if subject != caller and not ident.get("is_admin"):
        raise HTTPException(status_code=403, detail="Only an admin can export or restore someone else's data")
    url = f"{_service_url('IDENTITY_SVC')}/api/internal/account/may-manage"
    try:
        async with aiohttp.ClientSession(timeout=_TIMEOUT) as session, session.get(
            url, params={"actor": caller, "subject": subject}, headers=_headers()
        ) as resp:
            answer = await resp.json(content_type=None) if resp.status == 200 else None
    except (aiohttp.ClientError, TimeoutError) as exc:
        log.warning("[account] identity unreachable for may-manage: %s", exc)
        raise HTTPException(status_code=503, detail="The identity service is not answering; try again shortly") from exc
    if not isinstance(answer, dict) or not answer.get("allowed"):
        raise HTTPException(status_code=403, detail="You cannot manage this person's account")
    return caller, subject


async def _audit(caller: str, subject: str, action: str, note: str) -> None:
    url = f"{_service_url('IDENTITY_SVC')}/api/internal/account/audit"
    try:
        async with aiohttp.ClientSession(timeout=_TIMEOUT) as session, session.post(
            url, json={"actor": caller, "target": subject, "action": action, "note": note}, headers=_headers()
        ) as resp:
            if resp.status != 200:
                log.error("[account] audit of %s for %s failed: HTTP %s", action, subject, resp.status)
    except (aiohttp.ClientError, TimeoutError) as exc:
        log.error("[account] audit of %s for %s failed: %s", action, subject, exc)


def _check_export_rate(caller: str) -> None:
    now = time.monotonic()
    last = _last_export.get(caller)
    if last is not None and now - last < EXPORT_COOLDOWN_S:
        wait = int(EXPORT_COOLDOWN_S - (now - last)) + 1
        raise HTTPException(status_code=429, detail=f"An export was just made. Try again in {wait} seconds.",
                            headers={"Retry-After": str(wait)})
    _last_export[caller] = now


def _check_import_rate(caller: str) -> None:
    now = time.monotonic()
    recent = [t for t in _imports.get(caller, []) if now - t < IMPORT_WINDOW_S]
    if len(recent) >= IMPORT_MAX_PER_WINDOW:
        raise HTTPException(status_code=429, detail="Too many restore steps in a short time. Wait a few minutes and try again.",
                            headers={"Retry-After": str(int(IMPORT_WINDOW_S))})
    recent.append(now)
    _imports[caller] = recent


def _line(record: dict[str, Any]) -> bytes:
    return (json.dumps(record, separators=(",", ":"), ensure_ascii=False) + "\n").encode()


async def _get_json(session: aiohttp.ClientSession, url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    async with session.get(url, params=params, headers=_headers()) as resp:
        if resp.status != 200:
            raise RuntimeError(f"HTTP {resp.status}")
        data = await resp.json(content_type=None)
    if not isinstance(data, dict):
        raise RuntimeError("not an object")
    return data


async def export_stream(caller: str, subject: str) -> AsyncIterator[bytes]:
    """The NDJSON lines of one person's export. One service's answer is held at a time."""
    yield _line({
        "kind": "manifest",
        "format": FORMAT,
        "version": VERSION,
        "exported_at": datetime.now(UTC).isoformat(),
        "subject": {"username": subject},
        "exported_by": {"username": caller},
        "services": [name for name, _, _ in SERVICES],
        "excluded": list(EXCLUDED),
    })
    sections = 0
    failed: list[str] = []
    async with aiohttp.ClientSession(timeout=_TIMEOUT) as session:
        for name, attr, prefix in SERVICES:
            base = f"{_service_url(attr)}{prefix}/export/{subject}"
            try:
                data = await _get_json(session, base)
                collections = data.get("collections")
                if not isinstance(collections, dict):
                    raise RuntimeError("no collections")
            except (aiohttp.ClientError, TimeoutError, RuntimeError, ValueError) as exc:
                log.warning("[account] %s export for %s failed: %s", name, subject, exc)
                failed.append(name)
                yield _line({"kind": "section", "service": name, "error": "This part could not be read just now."})
                continue
            sections += 1
            yield _line({"kind": "section", "service": name, "collections": collections})
            del data, collections
            if name == "geo":
                start: int | None = 0
                try:
                    while start is not None:
                        page = await _get_json(session, f"{base}/location-history", {"start": start, "count": HISTORY_PAGE})
                        points = page.get("points") or []
                        if points:
                            yield _line({"kind": "section", "service": "geo", "collections": {"location_history": points}})
                        nxt = page.get("next")
                        start = int(nxt) if isinstance(nxt, int) and nxt > start else None
                except (aiohttp.ClientError, TimeoutError, RuntimeError, ValueError) as exc:
                    log.warning("[account] geo location history for %s failed: %s", subject, exc)
                    failed.append("geo:location_history")
                    yield _line({"kind": "section", "service": "geo", "error": "The location trail could not be read just now."})
    yield _line({"kind": "end", "sections": sections, "failed": failed})


@router.post("/export")
async def export_account(request: Request, body: dict[str, Any] = Body(default={})) -> StreamingResponse:
    requested = body.get("user") if isinstance(body, dict) else None
    caller, subject = await _subject_for(request, requested if isinstance(requested, str) and requested.strip() else None)
    _check_export_rate(caller)
    note = "own account" if caller == subject else "exported by an admin for this person"
    await _audit(caller, subject, "account.export", note)
    return StreamingResponse(
        export_stream(caller, subject),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
    )


@router.post("/import")
async def import_account(request: Request, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    requested = body.get("user")
    caller, subject = await _subject_for(request, requested if isinstance(requested, str) and requested.strip() else None)
    service = body.get("service")
    target = next((s for s in SERVICES if s[0] == service), None)
    if target is None:
        raise HTTPException(status_code=422, detail="Unknown part of the export")
    collections = body.get("collections")
    if not isinstance(collections, dict):
        raise HTTPException(status_code=422, detail="collections must be an object")
    dry_run = bool(body.get("dry_run", True))
    _check_import_rate(caller)
    name, attr, prefix = target
    url = f"{_service_url(attr)}{prefix}/import/{subject}"
    payload = {"collections": collections, "dry_run": dry_run, "replace_conflicts": bool(body.get("replace_conflicts", False))}
    try:
        async with aiohttp.ClientSession(timeout=_TIMEOUT) as session, session.post(url, json=payload, headers=_headers()) as resp:
            data = await resp.json(content_type=None) if resp.status == 200 else None
            status = resp.status
    except (aiohttp.ClientError, TimeoutError, ValueError) as exc:
        log.warning("[account] %s import for %s failed: %s", name, subject, exc)
        raise HTTPException(status_code=503, detail=f"The {name} service is not answering; try again shortly") from exc
    if not isinstance(data, dict):
        raise HTTPException(status_code=502, detail=f"The {name} service could not take this part (HTTP {status})")
    if not dry_run:
        who = "own account" if caller == subject else "restored by an admin for this person"
        await _audit(caller, subject, "account.import", f"{name}: {who}")
    return {"service": name, "dry_run": dry_run, "collections": data.get("collections") or {}}
