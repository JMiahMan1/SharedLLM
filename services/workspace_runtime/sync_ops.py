"""Nextcloud storage for workspaces: sync endpoints and the background loop.

A workspace's ``sync_mode`` says where its files live:

* ``local_git_authoritative`` (also ``git``): git only. ``nextcloud_path`` is
  optional and, with ``auto_backup_enabled``, receives a one-way push after
  every successful git pull.
* ``nextcloud``: no git. The workspace is a two-way synced copy of
  ``nextcloud_path``.
* ``git_and_nextcloud``: a git checkout that is ALSO two-way synced with
  ``nextcloud_path`` (``.git`` itself never travels).

Like ``git_ops``, this module is imported at the bottom of ``main.py`` so the
shared helpers it uses already exist.
"""
from __future__ import annotations

import asyncio
import logging
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Header, HTTPException
from pydantic import Field
from sqlmodel import Session, delete, select

import services.config as config
import services.workspace_runtime.main as main_mod
from services.workspace_runtime.main import (
    WorkspaceRef,
    _require_internal_secret,
    _require_workspace_capability,
    _resolve_identity_context,
    _resolve_workspace,
    get_workspace_lock,
    resolve_safe_path,
)
from services.workspace_runtime.models import Workspace, WorkspaceSyncEntry
from services.workspace_runtime.nextcloud_sync import (
    SYNC_DIRECTIONS,
    NextcloudSyncError,
    SyncEngine,
    SyncRecord,
    WebDavClient,
)

log = logging.getLogger("workspace_runtime.sync")

sync_router = APIRouter()

GIT_ONLY_MODES = {"local_git_authoritative", "git"}
NEXTCLOUD_MODES = {"nextcloud", "git_and_nextcloud"}
SYNC_MODES = GIT_ONLY_MODES | NEXTCLOUD_MODES

# After a write, wait this long for more writes before syncing, so saving a
# file or uploading a folder triggers one sync, not one per file.
SYNC_DEBOUNCE_SECONDS = 5.0


def validate_sync_settings(sync_mode: str | None, nextcloud_path: str | None) -> None:
    """Reject an unknown mode, or a Nextcloud mode with nowhere to sync to."""
    mode = (sync_mode or "local_git_authoritative").strip()
    if mode not in SYNC_MODES:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown sync_mode {mode!r}; expected one of {', '.join(sorted(SYNC_MODES))}",
        )
    if mode in NEXTCLOUD_MODES and not str(nextcloud_path or "").strip():
        raise HTTPException(
            status_code=400,
            detail=f"sync_mode {mode!r} needs a nextcloud_path (the Nextcloud folder to sync with)",
        )


def default_direction(workspace: dict[str, Any]) -> str:
    mode = str(workspace.get("sync_mode") or "local_git_authoritative")
    return "both" if mode in NEXTCLOUD_MODES else "push"


def _nextcloud_client(identity: dict[str, Any]) -> WebDavClient:
    missing = [k for k in ("nextcloud_url", "nextcloud_user", "nextcloud_pass") if not str(identity.get(k) or "").strip()]
    if missing:
        user = identity.get("user") or "this user"
        raise NextcloudSyncError(
            f"Nextcloud is not configured for {user}: missing {', '.join(missing)} in their Identity integrations"
        )
    return WebDavClient(
        str(identity["nextcloud_url"]).strip(),
        str(identity["nextcloud_user"]).strip(),
        str(identity["nextcloud_pass"]).strip(),
        timeout_seconds=float(config.WORKSPACE_NEXTCLOUD_TIMEOUT_SECONDS),
    )


def _load_records(workspace_id: str) -> dict[str, SyncRecord]:
    with Session(main_mod.engine) as session:
        rows = session.exec(select(WorkspaceSyncEntry).where(WorkspaceSyncEntry.workspace_id == workspace_id)).all()
        return {
            row.path: SyncRecord(
                path=row.path, is_dir=row.is_dir, size=row.size, mtime_ns=row.mtime_ns,
                sha256=row.sha256, etag=row.etag,
            )
            for row in rows
        }


def _save_records(workspace_id: str, records: dict[str, SyncRecord]) -> None:
    with Session(main_mod.engine) as session:
        session.exec(delete(WorkspaceSyncEntry).where(WorkspaceSyncEntry.workspace_id == workspace_id))
        for rec in records.values():
            session.add(WorkspaceSyncEntry(
                workspace_id=workspace_id, path=rec.path, is_dir=rec.is_dir, size=rec.size,
                mtime_ns=rec.mtime_ns, sha256=rec.sha256, etag=rec.etag,
            ))
        session.commit()


def clear_sync_records(workspace_id: str) -> None:
    with Session(main_mod.engine) as session:
        session.exec(delete(WorkspaceSyncEntry).where(WorkspaceSyncEntry.workspace_id == workspace_id))
        session.commit()


def _record_outcome(workspace_id: str, status: str, error: str | None, sync_user: str | None) -> None:
    with Session(main_mod.engine) as session:
        ws = session.get(Workspace, workspace_id)
        if ws is None:
            return
        ws.last_sync_at = datetime.now(UTC)
        ws.last_sync_status = status
        ws.last_sync_error = error
        if sync_user and not ws.sync_owner:
            ws.sync_owner = sync_user
        session.add(ws)
        session.commit()


def run_workspace_sync(
    workspace: dict[str, Any],
    identity: dict[str, Any],
    direction: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Sync one workspace with its Nextcloud folder. Raises NextcloudSyncError."""
    remote_root = str(workspace.get("nextcloud_path") or "").strip()
    if not remote_root:
        raise NextcloudSyncError(f"Workspace {workspace['id']} has no nextcloud_path to sync with")
    direction = direction or default_direction(workspace)
    if direction not in SYNC_DIRECTIONS:
        raise NextcloudSyncError(f"Unknown sync direction {direction!r}; expected one of {SYNC_DIRECTIONS}")

    client = _nextcloud_client(identity)
    lock = get_workspace_lock(workspace["id"])
    with lock:
        engine = SyncEngine(
            local_root=Path(workspace["resolved_path"]),
            remote=client,
            remote_root=remote_root,
            records=_load_records(workspace["id"]),
            excludes=workspace.get("excludes") or [],
        )
        report = engine.run(direction=direction, dry_run=dry_run)
        if not dry_run:
            _save_records(workspace["id"], engine.records)
    return report.as_dict()


def _sync_and_record(workspace: dict[str, Any], identity: dict[str, Any], direction: str | None, dry_run: bool) -> dict[str, Any]:
    sync_user = str(identity.get("user") or "").strip() or None
    try:
        result = run_workspace_sync(workspace, identity, direction, dry_run)
    except NextcloudSyncError as exc:
        if not dry_run:
            _record_outcome(workspace["id"], "error", str(exc), sync_user)
        raise
    if not dry_run:
        status = "error" if result["errors"] else ("conflicts" if result["conflicts"] else "ok")
        error = "; ".join(f"{e['path']}: {e['error']}" for e in result["errors"][:5]) or None
        _record_outcome(workspace["id"], status, error, sync_user)
    return result


# ---------------------------------------------------------------------------
# Unattended syncs (after writes, after git pulls, on a timer)
# ---------------------------------------------------------------------------


def _resolve_sync_identity(ws: Workspace) -> dict[str, Any]:
    user = (ws.sync_owner or ws.owner_user or "").strip()
    if not user:
        raise NextcloudSyncError(
            "No Nextcloud account is linked to this workspace yet. Run a sync once from the workspace "
            "(as the user whose Nextcloud it should use) to link it."
        )
    identity = _resolve_identity_context(WorkspaceRef(rag_user=user))
    if not identity:
        raise NextcloudSyncError(f"Identity could not resolve user {user!r}")
    return identity


def background_sync(workspace_id: str, direction: str | None = None) -> dict[str, Any] | None:
    """Sync a workspace using its linked account. Logs and records failures."""
    with Session(main_mod.engine) as session:
        ws = session.get(Workspace, workspace_id)
        if ws is None or not ws.nextcloud_path:
            return None
        ws_dict = main_mod._workspace_to_dict(ws)
    try:
        ws_dict["resolved_path"] = str(
            resolve_safe_path(main_mod.get_workspace_root(), str(ws_dict.get("local_path") or ""), must_exist=False)
        )
        identity = _resolve_sync_identity(ws)
        result = _sync_and_record(ws_dict, identity, direction, dry_run=False)
    except NextcloudSyncError as exc:
        log.warning("Nextcloud sync for workspace %s failed: %s", workspace_id, exc)
        _record_outcome(workspace_id, "error", str(exc), None)
        return None
    except HTTPException as exc:
        log.warning("Nextcloud sync for workspace %s failed: %s", workspace_id, exc.detail)
        _record_outcome(workspace_id, "error", str(exc.detail), None)
        return None
    except Exception as exc:  # the timer loop must survive one bad workspace
        log.exception("Nextcloud sync for workspace %s crashed", workspace_id)
        _record_outcome(workspace_id, "error", f"unexpected error: {exc}", None)
        return None
    if result["changed"] or result["conflicts"]:
        log.info(
            "Nextcloud sync %s: %d up, %d down, %d deleted here, %d deleted there, %d conflicts",
            workspace_id, len(result["uploaded"]), len(result["downloaded"]),
            len(result["deleted_local"]), len(result["deleted_remote"]), len(result["conflicts"]),
        )
    return result


_pending_timers: dict[str, threading.Timer] = {}
_pending_lock = threading.Lock()


def schedule_sync_after_write(workspace: dict[str, Any]) -> None:
    """Debounced two-way sync after a local change, for Nextcloud-mode workspaces."""
    if str(workspace.get("sync_mode") or "") not in NEXTCLOUD_MODES or not workspace.get("nextcloud_path"):
        return
    workspace_id = workspace["id"]
    with _pending_lock:
        existing = _pending_timers.pop(workspace_id, None)
        if existing:
            existing.cancel()
        timer = threading.Timer(SYNC_DEBOUNCE_SECONDS, _run_scheduled, args=(workspace_id,))
        timer.daemon = True
        _pending_timers[workspace_id] = timer
        timer.start()


def _run_scheduled(workspace_id: str) -> None:
    with _pending_lock:
        _pending_timers.pop(workspace_id, None)
    background_sync(workspace_id)


async def periodic_sync_loop() -> None:
    """Two-way sync every Nextcloud-mode workspace on a fixed interval."""
    interval = config.WORKSPACE_NEXTCLOUD_SYNC_INTERVAL_SECONDS
    if interval <= 0:
        log.info("Background Nextcloud workspace sync is off (WORKSPACE_NEXTCLOUD_SYNC_INTERVAL_SECONDS=%s)", interval)
        return
    log.info("Background Nextcloud workspace sync every %ss", interval)
    while True:
        await asyncio.sleep(interval)
        with Session(main_mod.engine) as session:
            ids = [
                ws.id for ws in session.exec(select(Workspace)).all()
                if ws.sync_mode in NEXTCLOUD_MODES and ws.nextcloud_path and not ws.quarantined
            ]
        for workspace_id in ids:
            await asyncio.to_thread(background_sync, workspace_id)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


class WorkspaceSyncRequest(WorkspaceRef):
    # both = two-way, push = workspace -> Nextcloud, pull = Nextcloud -> workspace.
    # Omitted: two-way for Nextcloud-mode workspaces, push for git workspaces.
    direction: str | None = None
    dry_run: bool = False


class WorkspaceSyncResetRequest(WorkspaceRef):
    confirm: bool = Field(default=False)


@sync_router.post("/provider/sync/workspace")
def sync_workspace(req: WorkspaceSyncRequest, x_internal_secret: str | None = Header(default=None)):
    _require_internal_secret(x_internal_secret)
    workspace = _resolve_workspace(req, check_recovery=True)
    _require_workspace_capability(workspace, "read" if req.dry_run else "write")
    identity = workspace.get("resolved_identity") or {}
    if not identity:
        raise HTTPException(status_code=400, detail="A user identity is required to sync with Nextcloud")
    try:
        result = _sync_and_record(workspace, identity, req.direction, req.dry_run)
    except NextcloudSyncError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from None
    return {"status": "SUCCESS", "workspace_id": workspace["id"], "result": result}


@sync_router.post("/provider/sync/workspace/reset")
def reset_workspace_sync(req: WorkspaceSyncResetRequest, x_internal_secret: str | None = Header(default=None)):
    """Forget the sync history, e.g. after pointing nextcloud_path elsewhere.

    The next two-way sync then treats both sides as new: nothing is deleted,
    files that differ become conflicted copies.
    """
    _require_internal_secret(x_internal_secret)
    if not req.confirm:
        raise HTTPException(status_code=400, detail="Set confirm=true to forget this workspace's sync history")
    workspace = _resolve_workspace(req)
    _require_workspace_capability(workspace, "write")
    with get_workspace_lock(workspace["id"]):
        clear_sync_records(workspace["id"])
    return {"status": "SUCCESS", "workspace_id": workspace["id"]}
