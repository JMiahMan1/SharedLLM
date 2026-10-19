"""HTTP surface for artifacts and storage usage.

Every endpoint is internal-secret protected except the signed streaming URL a
browser media element loads. The gateway resolves the caller and forwards
``X-User``/``X-Admin``; storage never trusts a client-supplied owner.
"""
from __future__ import annotations

import base64
import binascii
import os

from fastapi import APIRouter, Header, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel

from services.common.secrets import secret_matches
from services.config import INTERNAL_SECRET
from services.storage import artifacts as store

router = APIRouter()


class Caller(BaseModel):
    user: str
    is_admin: bool = False


class FitRequest(BaseModel):
    size: int
    owner_user: str | None = None


class HardDeleteRequest(BaseModel):
    confirm_phrase: str = ""


class WorkspaceArtifactRequest(BaseModel):
    action: str


def require_internal_secret(
    x_internal_secret: str | None = Header(None, alias="X-Internal-Secret"),
) -> None:
    expected = os.getenv("INTERNAL_SECRET", INTERNAL_SECRET)
    if not x_internal_secret or not (
        (expected and secret_matches(x_internal_secret, expected))
        or (INTERNAL_SECRET and secret_matches(x_internal_secret, INTERNAL_SECRET))
    ):
        raise HTTPException(status_code=403, detail="Forbidden")


def caller_identity(
    x_user: str | None = Header(None, alias="X-User"),
    x_admin: str | None = Header(None, alias="X-Admin"),
) -> Caller:
    user = (x_user or "").strip()
    if not user:
        raise HTTPException(status_code=400, detail="X-User is required")
    return Caller(user=user, is_admin=(x_admin or "").lower() in {"1", "true", "yes"})


def admin_identity(x_admin: str | None) -> Caller:
    caller = Caller(user="", is_admin=(x_admin or "").lower() in {"1", "true", "yes"})
    if not caller.is_admin:
        raise HTTPException(status_code=403, detail="Admin only")
    return caller


def _require_gateway(
    x_internal_secret: str | None = Header(None, alias="X-Internal-Secret"),
) -> None:
    require_internal_secret(x_internal_secret)


@router.post("/artifacts/check-fit", dependencies=[])
def check_fit(
    request: FitRequest,
    x_internal_secret: str | None = Header(None, alias="X-Internal-Secret"),
    x_user: str | None = Header(None, alias="X-User"),
    x_admin: str | None = Header(None, alias="X-Admin"),
) -> store.FitResult:
    require_internal_secret(x_internal_secret)
    caller = caller_identity(x_user, x_admin)
    owner = request.owner_user or caller.user
    if owner != caller.user and not caller.is_admin:
        raise HTTPException(status_code=403, detail="Cannot inspect another user's quota")
    return store.check_fit(owner, request.size)


@router.post("/artifacts", status_code=201)
def create_artifact(
    request: store.ArtifactCreate,
    x_internal_secret: str | None = Header(None, alias="X-Internal-Secret"),
    x_user: str | None = Header(None, alias="X-User"),
    x_admin: str | None = Header(None, alias="X-Admin"),
) -> store.ArtifactRead:
    require_internal_secret(x_internal_secret)
    caller = caller_identity(x_user, x_admin)
    try:
        data = base64.b64decode(request.content_b64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(status_code=400, detail="content_b64 is not valid base64") from exc
    return store.create_artifact(
        caller.user,
        name=request.name,
        kind=request.kind,
        data=data,
        mime_type=request.mime_type,
        workspace_id=request.workspace_id,
        generator=request.generator,
        metadata=request.metadata,
    )


@router.get("/artifacts")
def list_artifacts(
    kind: str | None = None,
    workspace_id: str | None = None,
    attached: bool | None = None,
    search: str | None = None,
    include_deleted: bool = False,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    x_internal_secret: str | None = Header(None, alias="X-Internal-Secret"),
    x_user: str | None = Header(None, alias="X-User"),
    x_admin: str | None = Header(None, alias="X-Admin"),
) -> store.ArtifactList:
    require_internal_secret(x_internal_secret)
    caller = caller_identity(x_user, x_admin)
    return store.list_artifacts(
        caller.user,
        is_admin=caller.is_admin,
        kind=kind,
        workspace_id=workspace_id,
        attached=attached,
        search=search,
        include_deleted=include_deleted,
        limit=limit,
        offset=offset,
    )


@router.post("/artifacts/workspace/{workspace_id}")
def workspace_artifacts(
    workspace_id: str,
    request: WorkspaceArtifactRequest,
    x_internal_secret: str | None = Header(None, alias="X-Internal-Secret"),
) -> dict:
    """Count, delete, or orphan a workspace's artifacts during deletion.

    Only the gateway calls this, after it has checked that the caller may
    administer the workspace.
    """
    require_internal_secret(x_internal_secret)
    action = (request.action or "").strip().lower()
    if action == "count":
        return {"action": "count", "artifact_count": store.workspace_artifact_count(workspace_id)}
    if action == "delete":
        return {"action": "delete", "deleted": store.delete_workspace_artifacts(workspace_id)}
    if action == "orphan":
        return {"action": "orphan", "orphaned": store.orphan_workspace_artifacts(workspace_id)}
    raise HTTPException(status_code=400, detail=f"Unsupported workspace artifact action: {request.action}")


@router.get("/artifacts/{artifact_id}")
def get_artifact(
    artifact_id: str,
    x_internal_secret: str | None = Header(None, alias="X-Internal-Secret"),
    x_user: str | None = Header(None, alias="X-User"),
    x_admin: str | None = Header(None, alias="X-Admin"),
) -> store.ArtifactRead:
    require_internal_secret(x_internal_secret)
    caller = caller_identity(x_user, x_admin)
    row = store.get_artifact_row(artifact_id, caller.user, is_admin=caller.is_admin)
    return store.ArtifactRead.from_row(row)


@router.get("/artifacts/{artifact_id}/download")
def download_artifact(
    artifact_id: str,
    x_internal_secret: str | None = Header(None, alias="X-Internal-Secret"),
    x_user: str | None = Header(None, alias="X-User"),
    x_admin: str | None = Header(None, alias="X-Admin"),
) -> dict:
    require_internal_secret(x_internal_secret)
    caller = caller_identity(x_user, x_admin)
    row = store.get_artifact_row(artifact_id, caller.user, is_admin=caller.is_admin)
    token = store.make_download_token(row.id)
    return {
        "url": f"/artifacts/{row.id}/stream?token={token}",
        "expires_in": store.SIGNED_URL_TTL_SECONDS,
        "name": row.name,
        "mime_type": row.mime_type,
        "size": row.size,
    }


@router.get("/artifacts/{artifact_id}/stream")
def stream_artifact(artifact_id: str, token: str = ""):
    """Range-capable content for a short-lived signed URL."""
    path, mime, name = store.stream_target(artifact_id, token)
    return FileResponse(
        path,
        media_type=mime,
        filename=name,
        content_disposition_type="inline",
        headers={
            "Accept-Ranges": "bytes",
            "Cache-Control": "private, max-age=900",
        },
    )


@router.patch("/artifacts/{artifact_id}")
def patch_artifact(
    artifact_id: str,
    request: store.ArtifactPatch,
    x_internal_secret: str | None = Header(None, alias="X-Internal-Secret"),
    x_user: str | None = Header(None, alias="X-User"),
    x_admin: str | None = Header(None, alias="X-Admin"),
) -> store.ArtifactRead:
    require_internal_secret(x_internal_secret)
    caller = caller_identity(x_user, x_admin)
    return store.patch_artifact(
        artifact_id, caller.user, is_admin=caller.is_admin, patch=request
    )


@router.delete("/artifacts/{artifact_id}")
def delete_artifact(
    artifact_id: str,
    request: HardDeleteRequest | None = None,
    hard: bool = False,
    x_internal_secret: str | None = Header(None, alias="X-Internal-Secret"),
    x_user: str | None = Header(None, alias="X-User"),
    x_admin: str | None = Header(None, alias="X-Admin"),
):
    require_internal_secret(x_internal_secret)
    caller = caller_identity(x_user, x_admin)
    if hard:
        store.hard_delete(
            artifact_id,
            caller.user,
            is_admin=caller.is_admin,
            confirm_phrase=(request.confirm_phrase if request else ""),
        )
        return {"status": "DELETED", "hard": True}
    row = store.soft_delete(artifact_id, caller.user, is_admin=caller.is_admin)
    return {"status": "DELETED", "hard": False, "artifact": row}


@router.post("/artifacts/{artifact_id}/restore")
def restore_artifact(
    artifact_id: str,
    x_internal_secret: str | None = Header(None, alias="X-Internal-Secret"),
    x_user: str | None = Header(None, alias="X-User"),
    x_admin: str | None = Header(None, alias="X-Admin"),
) -> store.ArtifactRead:
    require_internal_secret(x_internal_secret)
    caller = caller_identity(x_user, x_admin)
    return store.restore_artifact(artifact_id, caller.user, is_admin=caller.is_admin)


@router.get("/storage/usage")
def get_usage(
    x_internal_secret: str | None = Header(None, alias="X-Internal-Secret"),
    x_user: str | None = Header(None, alias="X-User"),
    x_admin: str | None = Header(None, alias="X-Admin"),
) -> store.UsageRead:
    require_internal_secret(x_internal_secret)
    caller = caller_identity(x_user, x_admin)
    return store.usage_for(caller.user)


@router.get("/storage/usage/system")
def get_system_usage(
    x_internal_secret: str | None = Header(None, alias="X-Internal-Secret"),
    x_admin: str | None = Header(None, alias="X-Admin"),
) -> store.SystemUsageRead:
    require_internal_secret(x_internal_secret)
    admin_identity(x_admin)
    return store.system_usage()


@router.get("/storage/usage/{username}")
def get_user_usage(
    username: str,
    x_internal_secret: str | None = Header(None, alias="X-Internal-Secret"),
    x_user: str | None = Header(None, alias="X-User"),
    x_admin: str | None = Header(None, alias="X-Admin"),
) -> store.UsageRead:
    require_internal_secret(x_internal_secret)
    caller = caller_identity(x_user, x_admin)
    if username != caller.user and not caller.is_admin:
        raise HTTPException(status_code=403, detail="Admin only")
    return store.usage_for(username)


@router.put("/storage/quota/{username}")
def set_user_quota(
    username: str,
    request: store.QuotaUpdate,
    x_internal_secret: str | None = Header(None, alias="X-Internal-Secret"),
    x_admin: str | None = Header(None, alias="X-Admin"),
) -> dict:
    require_internal_secret(x_internal_secret)
    admin_identity(x_admin)
    row = store.set_quota(username, request.quota_bytes)
    return {"username": row.username, "quota_bytes": row.quota_bytes}
