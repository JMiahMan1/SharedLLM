"""Artifact metadata, quota accounting, and lifecycle.

Artifacts are files a workspace produced (TTS audio, images, video, exported
documents) that must outlive the workspace that made them. The bytes live in a
storage backend; this module owns the row that says who owns them, how big they
are, and whether they are inside the 24-hour undo window.

The database lives beside the local artifacts root so a volume backup contains
both the bytes and the index that explains them.
"""

import hashlib
import hmac
import json
import os
import shutil
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from fastapi import HTTPException
from pydantic import BaseModel
from sqlalchemy import func
from sqlmodel import Field, Session, SQLModel, create_engine, select

from services.common.secrets import secret_matches
from services.config import INTERNAL_SECRET
from services.storage.providers_impl.local import LocalStorageProvider

ArtifactKind = Literal["tts_audio", "image", "video", "document", "other"]
ARTIFACT_KINDS: tuple[str, ...] = ("tts_audio", "image", "video", "document", "other")

DEFAULT_QUOTA_BYTES = 5 * 1024 * 1024 * 1024
SIGNED_URL_TTL_SECONDS = 15 * 60
PURGE_INTERVAL_SECONDS = 5 * 60


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "") or default)
    except ValueError:
        return default


def artifacts_root() -> Path:
    return Path(os.getenv("ARTIFACTS_ROOT", "/artifacts"))


def database_url() -> str:
    explicit = (os.getenv("ARTIFACT_DATABASE_URL") or "").strip()
    if explicit:
        return explicit
    return f"sqlite:///{artifacts_root() / 'artifacts.db'}"


def default_quota_bytes() -> int:
    return _env_int("ARTIFACT_LOCAL_QUOTA_BYTES", DEFAULT_QUOTA_BYTES)


def warn_pct() -> float:
    try:
        return float(os.getenv("ARTIFACT_WARN_PCT", "0.90"))
    except ValueError:
        return 0.90


def undo_window_hours() -> int:
    return _env_int("ARTIFACT_UNDO_WINDOW_HOURS", 24)


@lru_cache(maxsize=4)
def _engine_for(url: str):
    return create_engine(url, connect_args={"check_same_thread": False})


def reset_for_tests() -> None:
    """Drop the cached engine after tests point the env at a temp directory."""
    _engine_for.cache_clear()


def init_db() -> None:
    root = artifacts_root()
    root.mkdir(parents=True, exist_ok=True)
    SQLModel.metadata.create_all(_engine_for(database_url()))


@contextmanager
def _session() -> Iterator[Session]:
    with Session(_engine_for(database_url())) as session:
        yield session


class Artifact(SQLModel, table=True):
    id: str = Field(primary_key=True)
    owner_user: str = Field(index=True)
    workspace_id: str | None = Field(default=None, index=True)
    kind: str = Field(index=True)
    name: str
    mime_type: str | None = None
    size: int
    storage_backend: str = "local"
    storage_ref: str
    sha256: str | None = Field(default=None, index=True)
    generator: str | None = None
    extra_json: str = "{}"
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None = Field(default=None, index=True)
    is_attached: bool = True


class UserStorageQuota(SQLModel, table=True):
    username: str = Field(primary_key=True)
    quota_bytes: int
    updated_at: datetime


class ArtifactRead(BaseModel):
    id: str
    owner_user: str
    workspace_id: str | None = None
    kind: str
    name: str
    mime_type: str | None = None
    size: int
    storage_backend: str
    sha256: str | None = None
    generator: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None = None
    is_attached: bool = True
    download_url: str | None = None

    @classmethod
    def from_row(cls, row: Artifact, *, download_url: str | None = None) -> "ArtifactRead":
        try:
            meta = json.loads(row.extra_json or "{}")
        except (TypeError, ValueError):
            meta = {}
        if not isinstance(meta, dict):
            meta = {}
        return cls(
            id=row.id,
            owner_user=row.owner_user,
            workspace_id=row.workspace_id,
            kind=row.kind,
            name=row.name,
            mime_type=row.mime_type,
            size=row.size,
            storage_backend=row.storage_backend,
            sha256=row.sha256,
            generator=row.generator,
            metadata=meta,
            created_at=row.created_at,
            updated_at=row.updated_at,
            deleted_at=row.deleted_at,
            is_attached=row.is_attached,
            download_url=download_url,
        )


class FitResult(BaseModel):
    fits: bool
    needed: int
    used: int
    quota: int
    free: int
    headroom: int
    warn: bool
    message: str


class ArtifactCreate(BaseModel):
    name: str
    kind: ArtifactKind = "other"
    mime_type: str | None = None
    workspace_id: str | None = None
    generator: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    content_b64: str


class ArtifactPatch(BaseModel):
    name: str | None = None
    metadata: dict[str, Any] | None = None
    is_attached: bool | None = None


class ArtifactList(BaseModel):
    artifacts: list[ArtifactRead]
    total: int
    limit: int
    offset: int


class UsageRead(BaseModel):
    user: str
    used: int
    quota: int
    free: int
    count: int
    by_kind: dict[str, int]
    warn: bool


class SystemUsageRead(BaseModel):
    users: list[UsageRead]
    total: int
    free: int
    quota: int
    used: int


class QuotaUpdate(BaseModel):
    quota_bytes: int = Field(ge=0)


def _now() -> datetime:
    return datetime.now(UTC)


def _quota_for(session: Session, username: str) -> int:
    row = session.get(UserStorageQuota, username)
    return row.quota_bytes if row else default_quota_bytes()


def _used_bytes(session: Session, owner: str | None = None, *, include_deleted: bool = True) -> int:
    statement = select(func.coalesce(func.sum(Artifact.size), 0))
    if owner is not None:
        statement = statement.where(Artifact.owner_user == owner)
    if not include_deleted:
        statement = statement.where(Artifact.deleted_at.is_(None))
    return int(session.exec(statement).one() or 0)


def _active_count(session: Session, owner: str | None = None) -> int:
    statement = select(func.count()).select_from(Artifact).where(Artifact.deleted_at.is_(None))
    if owner is not None:
        statement = statement.where(Artifact.owner_user == owner)
    return int(session.exec(statement).one() or 0)


def _mb(value: int) -> str:
    return f"{value / (1024 * 1024):.1f} MB"


def check_fit(owner_user: str, needed_bytes: int) -> FitResult:
    """Whether ``needed_bytes`` fits the owner's quota and the local volume.

    Soft-deleted artifacts still count: their bytes are on disk until the undo
    window expires, so ignoring them would promise space that is not free.
    """
    if needed_bytes < 0:
        raise HTTPException(status_code=400, detail="size must not be negative")
    root = artifacts_root()
    root.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(root).free
    with _session() as session:
        quota = _quota_for(session, owner_user)
        used = _used_bytes(session, owner_user, include_deleted=True)
        active_used = _used_bytes(session, owner_user, include_deleted=False)
    capacity = min(quota, free)
    headroom = max(0, capacity - used)
    fits = needed_bytes <= headroom
    warn = quota > 0 and (active_used / quota >= warn_pct() or (active_used + needed_bytes) / quota >= warn_pct())
    if not fits:
        message = (
            f"Cannot store artifact: needs {_mb(needed_bytes)}, "
            f"only {_mb(headroom)} free of {_mb(quota)} quota/local space."
        )
    elif warn:
        message = "You're at 90% of your storage. This may fail due to space. Clean up or request more from your admin."
    else:
        message = ""
    return FitResult(
        fits=fits,
        needed=needed_bytes,
        used=used,
        quota=quota,
        free=free,
        headroom=headroom,
        warn=warn,
        message=message,
    )


def create_artifact(
    owner_user: str,
    *,
    name: str,
    kind: str,
    data: bytes,
    mime_type: str | None = None,
    workspace_id: str | None = None,
    generator: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> ArtifactRead:
    if kind not in ARTIFACT_KINDS:
        raise HTTPException(status_code=400, detail=f"Unsupported artifact kind: {kind}")
    clean_name = (name or "").strip()
    if not clean_name:
        raise HTTPException(status_code=400, detail="name is required")
    if len(clean_name) > 255:
        raise HTTPException(status_code=400, detail="name is too long")
    if not data:
        raise HTTPException(status_code=400, detail="artifact content is empty")

    fit = check_fit(owner_user, len(data))
    if not fit.fits:
        raise HTTPException(status_code=413, detail=fit.message)

    artifact_id = uuid.uuid4().hex
    provider = LocalStorageProvider(artifacts_root())
    provider.write_bytes(artifact_id, data, verify=True)
    now = _now()
    row = Artifact(
        id=artifact_id,
        owner_user=owner_user,
        workspace_id=workspace_id,
        kind=kind,
        name=clean_name,
        mime_type=mime_type,
        size=len(data),
        storage_backend="local",
        storage_ref=artifact_id,
        sha256=hashlib.sha256(data).hexdigest(),
        generator=generator,
        extra_json=json.dumps(metadata or {}, separators=(",", ":"), default=str),
        created_at=now,
        updated_at=now,
        is_attached=True,
    )
    try:
        with _session() as session:
            session.add(row)
            session.commit()
            session.refresh(row)
    except Exception:
        provider.delete_file(artifact_id)
        raise
    token = make_download_token(artifact_id)
    return ArtifactRead.from_row(
        row, download_url=f"/artifacts/{artifact_id}/stream?token={token}"
    )


def get_artifact_row(artifact_id: str, owner_user: str, *, is_admin: bool = False) -> Artifact:
    with _session() as session:
        row = session.get(Artifact, artifact_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Artifact not found")
        if not is_admin and row.owner_user != owner_user:
            # Do not reveal that someone else's artifact exists.
            raise HTTPException(status_code=404, detail="Artifact not found")
        return row


def list_artifacts(
    owner_user: str,
    *,
    is_admin: bool = False,
    kind: str | None = None,
    workspace_id: str | None = None,
    attached: bool | None = None,
    search: str | None = None,
    include_deleted: bool = False,
    limit: int = 50,
    offset: int = 0,
) -> ArtifactList:
    limit = max(1, min(limit, 100))
    offset = max(0, offset)
    with _session() as session:
        filters = []
        if not is_admin:
            filters.append(Artifact.owner_user == owner_user)
        if not include_deleted:
            filters.append(Artifact.deleted_at.is_(None))
        if kind:
            filters.append(Artifact.kind == kind)
        if workspace_id:
            filters.append(Artifact.workspace_id == workspace_id)
        if attached is not None:
            filters.append(Artifact.is_attached == attached)
        if search:
            filters.append(Artifact.name.contains(search))
        total_statement = select(func.count()).select_from(Artifact).where(*filters)
        total = int(session.exec(total_statement).one() or 0)
        statement = (
            select(Artifact)
            .where(*filters)
            .order_by(Artifact.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        rows = session.exec(statement).all()
    return ArtifactList(
        artifacts=[ArtifactRead.from_row(row) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


def patch_artifact(artifact_id: str, owner_user: str, *, is_admin: bool, patch: ArtifactPatch) -> ArtifactRead:
    with _session() as session:
        row = session.get(Artifact, artifact_id)
        if row is None or (not is_admin and row.owner_user != owner_user):
            raise HTTPException(status_code=404, detail="Artifact not found")
        if patch.name is not None:
            clean = patch.name.strip()
            if not clean:
                raise HTTPException(status_code=400, detail="name is required")
            row.name = clean[:255]
        if patch.metadata is not None:
            row.extra_json = json.dumps(patch.metadata, separators=(",", ":"), default=str)
        if patch.is_attached is not None:
            row.is_attached = patch.is_attached
        row.updated_at = _now()
        session.add(row)
        session.commit()
        session.refresh(row)
        return ArtifactRead.from_row(row)


def soft_delete(artifact_id: str, owner_user: str, *, is_admin: bool) -> ArtifactRead:
    with _session() as session:
        row = session.get(Artifact, artifact_id)
        if row is None or (not is_admin and row.owner_user != owner_user):
            raise HTTPException(status_code=404, detail="Artifact not found")
        if row.deleted_at is None:
            row.deleted_at = _now()
            row.updated_at = row.deleted_at
            session.add(row)
            session.commit()
            session.refresh(row)
        return ArtifactRead.from_row(row)


def restore_artifact(artifact_id: str, owner_user: str, *, is_admin: bool) -> ArtifactRead:
    with _session() as session:
        row = session.get(Artifact, artifact_id)
        if row is None or (not is_admin and row.owner_user != owner_user):
            raise HTTPException(status_code=404, detail="Artifact not found")
        if row.deleted_at is None:
            return ArtifactRead.from_row(row)
        if _now() - _aware(row.deleted_at) > timedelta(hours=undo_window_hours()):
            raise HTTPException(status_code=410, detail="The undo window has expired")
        row.deleted_at = None
        row.updated_at = _now()
        session.add(row)
        session.commit()
        session.refresh(row)
        return ArtifactRead.from_row(row)


def hard_delete(artifact_id: str, owner_user: str, *, is_admin: bool, confirm_phrase: str) -> None:
    with _session() as session:
        row = session.get(Artifact, artifact_id)
        if row is None or (not is_admin and row.owner_user != owner_user):
            raise HTTPException(status_code=404, detail="Artifact not found")
        if confirm_phrase != f"delete {row.name}":
            raise HTTPException(status_code=400, detail=f"Type 'delete {row.name}' to confirm")
        LocalStorageProvider(artifacts_root()).delete_file(row.storage_ref)
        session.delete(row)
        session.commit()


def purge_expired() -> int:
    cutoff = _now() - timedelta(hours=undo_window_hours())
    removed = 0
    with _session() as session:
        rows = session.exec(select(Artifact).where(Artifact.deleted_at.is_not(None))).all()
        for row in rows:
            if _aware(row.deleted_at) > cutoff:
                continue
            LocalStorageProvider(artifacts_root()).delete_file(row.storage_ref)
            session.delete(row)
            removed += 1
        if removed:
            session.commit()
    return removed


def workspace_artifact_count(workspace_id: str) -> int:
    """Count the live artifacts that belong to a workspace."""
    with _session() as session:
        count = session.exec(
            select(func.count())
            .select_from(Artifact)
            .where(
                Artifact.workspace_id == workspace_id,
                Artifact.deleted_at.is_(None),
                Artifact.is_attached.is_(True),
            )
        ).one()
    return int(count or 0)


def delete_workspace_artifacts(workspace_id: str) -> int:
    """Delete every artifact in a workspace, including ones in the undo window.

    Called only after the workspace owner confirms that the workspace and its
    artifacts should go together, so the per-artifact phrase check does not
    apply here.
    """
    provider = LocalStorageProvider(artifacts_root())
    removed = 0
    with _session() as session:
        rows = session.exec(select(Artifact).where(Artifact.workspace_id == workspace_id)).all()
        for row in rows:
            provider.delete_file(row.storage_ref)
            session.delete(row)
            removed += 1
        if removed:
            session.commit()
    return removed


def orphan_workspace_artifacts(workspace_id: str) -> int:
    """Keep a workspace's artifacts in the library after the workspace is gone."""
    changed = 0
    with _session() as session:
        rows = session.exec(
            select(Artifact).where(
                Artifact.workspace_id == workspace_id,
                Artifact.is_attached.is_(True),
            )
        ).all()
        now = _now()
        for row in rows:
            row.is_attached = False
            row.updated_at = now
            session.add(row)
            changed += 1
        if changed:
            session.commit()
    return changed


def usage_for(owner_user: str) -> UsageRead:
    with _session() as session:
        quota = _quota_for(session, owner_user)
        used = _used_bytes(session, owner_user, include_deleted=False)
        count = _active_count(session, owner_user)
        by_kind_rows = session.exec(
            select(Artifact.kind, func.coalesce(func.sum(Artifact.size), 0))
            .where(Artifact.owner_user == owner_user, Artifact.deleted_at.is_(None))
            .group_by(Artifact.kind)
        ).all()
    free = shutil.disk_usage(artifacts_root()).free
    by_kind = {str(kind): int(size or 0) for kind, size in by_kind_rows}
    return UsageRead(
        user=owner_user,
        used=used,
        quota=quota,
        free=free,
        count=count,
        by_kind=by_kind,
        warn=quota > 0 and used / quota >= warn_pct(),
    )


def system_usage() -> SystemUsageRead:
    with _session() as session:
        owners = session.exec(select(Artifact.owner_user).distinct()).all()
        users = [usage_for(str(owner)) for owner in owners]
        total = _used_bytes(session, include_deleted=False)
    free = shutil.disk_usage(artifacts_root()).free
    return SystemUsageRead(
        users=users,
        total=total,
        free=free,
        quota=sum(user.quota for user in users),
        used=total,
    )


def set_quota(username: str, quota_bytes: int) -> UserStorageQuota:
    if quota_bytes < 0:
        raise HTTPException(status_code=400, detail="quota_bytes must not be negative")
    now = _now()
    with _session() as session:
        row = session.get(UserStorageQuota, username)
        if row is None:
            row = UserStorageQuota(username=username, quota_bytes=quota_bytes, updated_at=now)
        else:
            row.quota_bytes = quota_bytes
            row.updated_at = now
        session.add(row)
        session.commit()
        session.refresh(row)
        return row


def signing_key() -> str:
    return os.getenv("ARTIFACT_SIGNING_KEY") or INTERNAL_SECRET


def make_download_token(artifact_id: str, ttl_seconds: int = SIGNED_URL_TTL_SECONDS) -> str:
    expires = int(time.time()) + ttl_seconds
    signature = hmac.new(
        signing_key().encode("utf-8"), f"{artifact_id}:{expires}".encode(), hashlib.sha256
    ).hexdigest()
    return f"{expires}.{signature}"


def verify_download_token(artifact_id: str, token: str) -> bool:
    try:
        expires_text, signature = token.split(".", 1)
        expires = int(expires_text)
    except (AttributeError, ValueError):
        return False
    if expires < int(time.time()):
        return False
    expected = hmac.new(
        signing_key().encode("utf-8"), f"{artifact_id}:{expires}".encode(), hashlib.sha256
    ).hexdigest()
    return secret_matches(signature, expected)


def stream_target(artifact_id: str, token: str) -> tuple[Path, str, str]:
    if not verify_download_token(artifact_id, token):
        raise HTTPException(status_code=403, detail="Invalid or expired download link")
    with _session() as session:
        row = session.get(Artifact, artifact_id)
        if row is None or row.deleted_at is not None:
            raise HTTPException(status_code=404, detail="Artifact not found")
        path = LocalStorageProvider(artifacts_root()).local_path(row.storage_ref)
        mime = row.mime_type or "application/octet-stream"
        name = row.name
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Artifact content is missing")
    return path, mime, name


def _aware(value: datetime | None) -> datetime:
    if value is None:
        return _now()
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value
