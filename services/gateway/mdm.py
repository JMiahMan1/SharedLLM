"""Device management (MDM): a parent's view and control of the family's phones.

Phase 12 (HANDOVER.md). This module adds the pieces that did not exist:

* ``GET  /api/mdm/devices``            - every family device in one list.
* ``POST /api/mdm/devices/update``     - push an app-update notice to selected devices.
* ``POST /api/mdm/devices/{key}/wipe`` - queue a remote wipe on a device-owner phone.
* ``GET/POST/DELETE /api/mdm/files*``  - the shared School and Library folders.

Two MDM actions are deliberately *not* reimplemented here, because the mechanism
already exists and a second copy is how the two drift apart:

* **Remote lock** is the existing screen-time pause (``POST /api/screen-time/{u}/pause``).
* **App install/uninstall control** is the existing screen rules
  (``install_approval`` and ``blocked_apps`` on ``PUT /api/screen-time/{u}/rules``).

The UI exposes both as MDM actions on a device; the server adds nothing.

Nothing here invents a value. A member whose devices identity will not hand over
is reported as unreadable, not as having none, and an update push with no
published build is refused rather than announced (no fake sends).

The shared folders are plain files under ``MDM_FILES_DIR`` (default
``/app/data/mdm_files``), each with a manifest, plus a ``pushes.json`` naming who
each folder is pushed to. The read-only Calibre library (docs/CALIBRE_LIBRARY.md)
is a candidate *source* for the Library folder, but this module never writes to
it.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import re
from datetime import UTC, datetime
from pathlib import Path

import aiohttp
from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse

from services.common import notify as _notify
from services.common.http import get_client
from services.gateway.config import IDENTITY_SVC, INTERNAL_SECRET

log = logging.getLogger("gateway.mdm")

router = APIRouter()

_TIMEOUT = aiohttp.ClientTimeout(total=10)
_HEADERS = {"X-Internal-Secret": INTERNAL_SECRET}

#: The shared sets a parent pushes: one School folder and one Library folder.
SHARED_FOLDERS = ("school", "library")
#: Where the shared folders, their manifests and pushes.json live.
MDM_FILES_DIR = Path(os.getenv("MDM_FILES_DIR", "/app/data/mdm_files"))
#: A single uploaded file may not exceed this.
MAX_UPLOAD_BYTES = 200 * 1024 * 1024
#: How long a queued wipe is offered to the phone before it is ignored.
WIPE_TTL_S = 24 * 3600

#: A file name: no path separator, no control characters, bounded length.
_NAME = re.compile(r"^[^/\\\x00-\x1f]{1,120}$")


# ---------------------------------------------------------------------------
# Pure file store (no FastAPI, so the routes and the tests share one copy).
# ---------------------------------------------------------------------------


def _root(root: Path | None = None) -> Path:
    return Path(root) if root is not None else MDM_FILES_DIR


def _check_folder(folder: str) -> str:
    if folder not in SHARED_FOLDERS:
        raise ValueError(f"folder must be one of {', '.join(SHARED_FOLDERS)}")
    return folder


def _check_name(name: str) -> str:
    name = (name or "").strip()
    if not _NAME.match(name) or name in (".", ".."):
        raise ValueError("a file name may not contain a path separator")
    return name


def _read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        log.warning("[mdm] %s is unreadable; treating it as empty", path.name)
        return {}
    return data if isinstance(data, dict) else {}


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True))
    tmp.replace(path)


def _manifest_path(folder: str, root: Path | None = None) -> Path:
    return _root(root) / folder / "manifest.json"


def list_folder(folder: str, root: Path | None = None) -> list[dict]:
    """Every file in a shared folder, oldest upload first."""
    _check_folder(folder)
    files = _read_json(_manifest_path(folder, root)).get("files")
    out = []
    for name, meta in (files or {}).items():
        if not isinstance(meta, dict):
            continue
        out.append({"name": name, **{k: meta.get(k) for k in
                                     ("size", "sha256", "content_type", "uploaded_by", "uploaded_at")}})
    return sorted(out, key=lambda e: (e.get("uploaded_at") or "", e["name"]))


def store_file(folder: str, name: str, content: bytes, content_type: str | None,
               uploaded_by: str, root: Path | None = None) -> dict:
    """Write ``content`` into a shared folder and record it in the manifest."""
    _check_folder(folder)
    name = _check_name(name)
    folder_dir = _root(root) / folder
    folder_dir.mkdir(parents=True, exist_ok=True)
    (folder_dir / name).write_bytes(content)
    entry = {
        "size": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
        "content_type": content_type or "application/octet-stream",
        "uploaded_by": uploaded_by,
        "uploaded_at": datetime.now(UTC).isoformat(),
    }
    data = _read_json(_manifest_path(folder, root))
    files = data.get("files")
    if not isinstance(files, dict):
        files = {}
    files[name] = entry
    data["files"] = files
    _write_json(_manifest_path(folder, root), data)
    return {"name": name, **entry}


def delete_file(folder: str, name: str, root: Path | None = None) -> bool:
    """Remove a file and its manifest row; False when it was not there."""
    _check_folder(folder)
    name = _check_name(name)
    data = _read_json(_manifest_path(folder, root))
    files = data.get("files")
    existed = isinstance(files, dict) and name in files
    if isinstance(files, dict):
        files.pop(name, None)
        data["files"] = files
        _write_json(_manifest_path(folder, root), data)
    with contextlib.suppress(FileNotFoundError):
        (_root(root) / folder / name).unlink()
    return existed


def file_path(folder: str, name: str, root: Path | None = None) -> Path:
    _check_folder(folder)
    return _root(root) / folder / _check_name(name)


def _pushes_path(root: Path | None = None) -> Path:
    return _root(root) / "pushes.json"


def read_pushes(root: Path | None = None) -> dict[str, list[str]]:
    """Who each shared folder is pushed to (lowercased usernames)."""
    data = _read_json(_pushes_path(root))
    out: dict[str, list[str]] = {}
    for folder, users in data.items():
        if folder in SHARED_FOLDERS and isinstance(users, list):
            out[folder] = sorted({str(u).strip().lower() for u in users if str(u).strip()})
    return out


def set_push(folder: str, usernames: list, root: Path | None = None) -> list[str]:
    """Replace who a folder is pushed to; returns the stored, cleaned list."""
    _check_folder(folder)
    clean = sorted({str(u).strip().lower() for u in usernames if str(u).strip()})
    data = read_pushes(root)
    data[folder] = clean
    _write_json(_pushes_path(root), data)
    return clean


def pushed_folders(username: str, root: Path | None = None) -> list[str]:
    who = (username or "").strip().lower()
    data = read_pushes(root)
    return [f for f in SHARED_FOLDERS if who and who in data.get(f, [])]


def _wipes_path(root: Path | None = None) -> Path:
    return _root(root) / "wipe_requests.json"


def read_wipes(root: Path | None = None) -> dict:
    return _read_json(_wipes_path(root))


def queue_wipe(device_key: str, owner: str, root: Path | None = None) -> dict:
    """Record a wipe for a device so the phone erases itself on its next check-in."""
    data = read_wipes(root)
    data[device_key] = {"owner": (owner or "").strip().lower(), "at": datetime.now(UTC).isoformat()}
    _write_json(_wipes_path(root), data)
    return data[device_key]


def wipe_requested(username: str, root: Path | None = None) -> bool:
    """Whether any device owned by ``username`` has a live queued wipe.

    A wipe is offered for ``WIPE_TTL_S`` and then ignored, so a phone that was
    offline for longer than that does not erase itself unexpectedly later; the
    parent can queue it again. Without this bound a queued wipe would fire on
    every check-in, including on the freshly reset phone.
    """
    who = (username or "").strip().lower()
    now = datetime.now(UTC)
    for record in read_wipes(root).values():
        if not isinstance(record, dict) or record.get("owner") != who:
            continue
        try:
            at = datetime.fromisoformat(str(record.get("at")))
        except ValueError:
            continue
        if at.tzinfo is None:
            at = at.replace(tzinfo=UTC)
        if (now - at).total_seconds() <= WIPE_TTL_S:
            return True
    return False


def clear_wipe(device_key: str, root: Path | None = None) -> None:
    data = read_wipes(root)
    if device_key in data:
        data.pop(device_key)
        _write_json(_wipes_path(root), data)


# ---------------------------------------------------------------------------
# Identity lookups
# ---------------------------------------------------------------------------


async def _owner_of(device_key: str) -> str | None:
    """The username a device key belongs to, from identity's internal lookup."""
    try:
        async with get_client().get(
            f"{IDENTITY_SVC}/api/internal/device-registered/{device_key}", headers=_HEADERS, timeout=_TIMEOUT
        ) as resp:
            if resp.status != 200:
                return None
            data = await resp.json(content_type=None)
    except Exception as e:  # a lookup failure is not a device that exists
        log.warning("[mdm] device lookup failed: %s", e)
        return None
    owner = (data or {}).get("owner") if isinstance(data, dict) else None
    return str(owner).lower() if owner else None


async def _selected_owners(body: dict) -> list[str]:
    """The distinct owners named directly, or through their device keys."""
    owners: list[str] = []
    for name in body.get("usernames") or []:
        if isinstance(name, str) and name.strip():
            owners.append(name.strip().lower())
    for key in body.get("device_keys") or []:
        if not isinstance(key, str) or not key.strip():
            continue
        owner = await _owner_of(key.strip())
        if owner:
            owners.append(owner)
    out: list[str] = []
    seen: set[str] = set()
    for owner in owners:
        if owner not in seen:
            seen.add(owner)
            out.append(owner)
    return out


def _folder_or_404(folder: str) -> str:
    if folder not in SHARED_FOLDERS:
        raise HTTPException(status_code=404, detail=f"No shared folder named {folder!r}")
    return folder


async def _body(request: Request) -> dict:
    try:
        body = await request.json()
    except Exception:
        return {}
    return body if isinstance(body, dict) else {}


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/api/mdm/devices")
async def mdm_device_inventory(request: Request):
    """Every family device in one list, for the father or an admin.

    Aggregated from identity's existing per-member endpoint
    (``/api/family/devices/{username}``) rather than a second source of truth.
    A member whose devices identity will not hand over is reported as
    ``readable: false`` with the reason, so the page shows an explicit
    "not readable" state instead of an invented empty list.
    """
    from services.gateway.main import _identity_as_caller, _require_authenticated

    caller = (await _require_authenticated(request) or "").lower()
    try:
        async with get_client().get(f"{IDENTITY_SVC}/api/users", headers=_HEADERS, timeout=_TIMEOUT) as resp:
            if resp.status != 200:
                raise HTTPException(status_code=502, detail="The family list could not be read")
            users = await resp.json(content_type=None)
    except HTTPException:
        raise
    except Exception as e:
        log.warning("[mdm] family list failed: %s", e)
        raise HTTPException(status_code=502, detail="The family list could not be read") from e
    if not isinstance(users, list):
        raise HTTPException(status_code=502, detail="The family list could not be read")

    devices: list[dict] = []
    members: list[dict] = []
    for u in users:
        if not isinstance(u, dict) or not u.get("username") or u.get("is_system_default"):
            continue
        username = str(u["username"])
        display = u.get("display_name") or username
        if username.lower() == caller:
            # Identity's per-member endpoint refuses the subject themselves, so
            # the caller's own devices come from their user-panel list instead
            # of being reported as unreadable.
            status, info = await _identity_as_caller(request, "GET", "/api/user-panel/devices")
            rows = ([d for d in info if isinstance(d, dict) and (d.get("owner_username") or "").lower() == caller]
                    if status == 200 and isinstance(info, list) else [])
        else:
            status, info = await _identity_as_caller(request, "GET", f"/api/family/devices/{username}")
            rows = info.get("devices") or [] if status == 200 and isinstance(info, dict) else []
        if status == 200:
            for d in rows:
                devices.append({
                    **d,
                    "owner_username": d.get("owner_username") or username,
                    "owner_display_name": display,
                })
            members.append({"username": username, "display_name": display,
                            "device_count": len(rows), "readable": True, "reason": None})
        else:
            detail = (info or {}).get("detail") if isinstance(info, dict) else None
            members.append({"username": username, "display_name": display, "device_count": None,
                            "readable": False,
                            "reason": detail or f"Could not read this member's devices (HTTP {status})"})
    return {"devices": devices, "members": members, "generated_at": datetime.now(UTC).isoformat()}


@router.post("/api/mdm/devices/update")
async def mdm_push_app_update(request: Request):
    """Send an app-update notice to the owners of selected devices.

    The notice goes to the device owner (a minor included): ``audience`` stays
    the default ``all``, unlike the whole-family publish notice in ``notices.py``
    which is ``parents``-only. When no build is published the request is refused
    with 409 rather than announcing an update nobody can install.
    """
    from services.gateway.main import _require_admin, get_app_update_version

    await _require_admin(request)
    body = await _body(request)
    owners = await _selected_owners(body)
    if not owners:
        raise HTTPException(status_code=422, detail="Select at least one device or person")

    info = await get_app_update_version(request)
    bundle = bool(info.get("bundle_available"))
    apk = bool(info.get("apk_available"))
    if not bundle and not apk:
        raise HTTPException(
            status_code=409,
            detail="No app build is published yet (no OTA bundle and no APK on the server), so there is nothing to push.",
        )
    version = str(info.get("version") or "unknown")
    code = info.get("apk_version_code")
    label = f"{version} (build {code})" if code else version
    available = []
    if bundle:
        available.append("the web bundle over the air")
    if apk:
        available.append("a new app build to install")
    text = "Update now: " + " and ".join(available) + "."
    sent = 0
    for owner in owners:
        payload = _notify.notice(
            owner, "app_updates", f"Jarvis app {label} is available", text,
            url="/settings", tag="mdm_app_update",
            actions=[_notify.view_action("/settings", "Update now")],
        )
        sent += int(await _notify.send(payload))
    return {"status": "SUCCESS", "sent": sent, "owners": owners, "version": version,
            "apk_version_code": code, "bundle_available": bundle, "apk_available": apk}


@router.get("/api/mdm/files")
async def mdm_list_shared_files(request: Request):
    """The two shared folders, their files and who each is pushed to (admin)."""
    from services.gateway.main import _require_admin

    await _require_admin(request)
    pushes = read_pushes()
    return {"folders": [{"folder": f, "files": list_folder(f), "pushed_to": pushes.get(f, [])}
                        for f in SHARED_FOLDERS]}


@router.post("/api/mdm/files/push")
async def mdm_push_shared_files(request: Request):
    """Push a shared folder to selected people's devices (admin).

    Declared before ``/api/mdm/files/{folder}`` so the literal path is not read
    as a folder name.
    """
    from services.gateway.main import _require_admin

    await _require_admin(request)
    body = await _body(request)
    folder = _folder_or_404(str(body.get("folder") or ""))
    usernames = body.get("usernames")
    if not isinstance(usernames, list) or not usernames:
        raise HTTPException(status_code=422, detail="Select at least one person to push the folder to")
    people = set_push(folder, usernames)
    files = list_folder(folder)
    label = "School" if folder == "school" else "Library"
    sent = 0
    for user in people:
        payload = _notify.notice(
            user, "family_safety", f"{label} folder updated",
            f"{len(files)} file(s) are ready in your shared {label} folder.",
            url="/devices", tag=f"mdm_files:{folder}",
            actions=[_notify.view_action("/devices", f"Open {label}")],
        )
        sent += int(await _notify.send(payload))
    return {"status": "SUCCESS", "folder": folder, "pushed_to": people, "files": len(files), "sent": sent}


@router.get("/api/mdm/files/for-me")
async def mdm_files_for_me(request: Request):
    """The shared folders pushed to the caller, for the device-facing list."""
    from services.gateway.main import _require_authenticated

    user = await _require_authenticated(request)
    return {"folders": [{"folder": f, "files": list_folder(f)} for f in pushed_folders(user)]}


@router.post("/api/mdm/files/{folder}")
async def mdm_upload_shared_file(request: Request, folder: str, file: UploadFile = File(...)):
    """Upload one file into a shared folder (admin)."""
    from services.gateway.main import _require_admin

    caller = await _require_admin(request)
    _folder_or_404(folder)
    name = (file.filename or "").strip()
    try:
        _check_name(name)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    content = await file.read()
    if not content:
        raise HTTPException(status_code=422, detail="The uploaded file is empty")
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail=f"The file is larger than the {MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit")
    entry = store_file(folder, name, content, file.content_type, caller)
    return {"status": "SUCCESS", "file": entry}


@router.delete("/api/mdm/files/{folder}/{name}")
async def mdm_delete_shared_file(request: Request, folder: str, name: str):
    """Remove one file from a shared folder (admin)."""
    from services.gateway.main import _require_admin

    await _require_admin(request)
    _folder_or_404(folder)
    try:
        removed = delete_file(folder, name)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if not removed:
        raise HTTPException(status_code=404, detail="No such file")
    return {"status": "SUCCESS"}


@router.get("/api/mdm/files/{folder}/{name}")
async def mdm_download_shared_file(request: Request, folder: str, name: str):
    """Download one shared file (any signed-in member; the device-facing read)."""
    from services.gateway.main import _require_authenticated

    await _require_authenticated(request)
    _folder_or_404(folder)
    try:
        path = file_path(folder, name)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if not path.exists():
        raise HTTPException(status_code=404, detail="No such file")
    return FileResponse(str(path), filename=name)


@router.post("/api/mdm/devices/{device_key}/wipe")
async def mdm_wipe_device(request: Request, device_key: str):
    """Queue a remote wipe, only for a device-owner phone, and only with confirm.

    A wipe is destructive and irreversible, so it is gated on an explicit
    ``confirm`` and on the device having reported ``guard.deviceOwner``: without
    device owner the phone's own Android will not honour it, and pretending
    otherwise would be a lie. The queue is read by the screen-time status the
    phone already polls, so the phone erases itself on its next check-in.
    """
    from services.gateway.main import _identity_as_caller, _require_authenticated

    await _require_authenticated(request)
    body = await _body(request)
    if not body.get("confirm"):
        raise HTTPException(
            status_code=422,
            detail="A remote wipe erases everything on the phone and cannot be undone; send confirm=true to proceed.",
        )
    key = device_key.strip()
    owner = await _owner_of(key)
    if not owner:
        raise HTTPException(status_code=404, detail="No registered phone with that key")
    status, info = await _identity_as_caller(request, "GET", f"/api/family/devices/{owner}")
    if status != 200 or not isinstance(info, dict):
        detail = (info or {}).get("detail") if isinstance(info, dict) else None
        raise HTTPException(status_code=status if status in (401, 403, 404) else 502,
                            detail=detail or "You may not manage that device")
    device = next((d for d in (info.get("devices") or []) if d.get("device_key") == key), None)
    if device is None:
        raise HTTPException(status_code=404, detail="That device does not belong to a member you can manage")
    guard = (device.get("capabilities") or {}).get("guard") or {}
    if not guard.get("deviceOwner"):
        raise HTTPException(
            status_code=409,
            detail="Remote wipe needs a device-owner phone; this device has not reported device owner.",
        )
    record = queue_wipe(key, owner)
    return {"status": "queued", "device_key": key, "owner_username": owner, "queued_at": record["at"],
            "delivery": "the phone erases itself on its next check-in"}


__all__ = [
    "MAX_UPLOAD_BYTES", "MDM_FILES_DIR", "SHARED_FOLDERS", "WIPE_TTL_S", "clear_wipe", "delete_file",
    "file_path", "list_folder", "pushed_folders", "queue_wipe", "read_pushes", "read_wipes", "router",
    "set_push", "store_file", "wipe_requested",
]
