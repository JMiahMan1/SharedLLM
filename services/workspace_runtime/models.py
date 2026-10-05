
from datetime import UTC, datetime

from sqlmodel import JSON, Column, Field, SQLModel


class Workspace(SQLModel, table=True):
    id: str = Field(default="", primary_key=True)
    display_name: str = Field(default="")
    access_policy: str = Field(default="authenticated")
    local_path: str | None = None  # User-facing path: relative for user workspaces, absolute for system
    nextcloud_path: str | None = None
    repo_url: str | None = None
    git_remote: str | None = Field(default="origin")
    default_branch: str | None = Field(default="main")
    # Where the files live: "local_git_authoritative" (git only), "nextcloud"
    # (no git; two-way sync with nextcloud_path) or "git_and_nextcloud" (a git
    # checkout that is also two-way synced with nextcloud_path).
    sync_mode: str = Field(default="local_git_authoritative")
    scope: str = Field(default="user")
    capabilities: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    owner_user: str | None = None  # Tied to Identity service user
    is_default: bool = Field(default=False)  # Marks this workspace as the default for git/tool operations
    auto_pull_enabled: bool = Field(default=False)
    auto_backup_enabled: bool = Field(default=False)
    webhook_token: str | None = Field(default=None)
    webhook_token_enc: str | None = Field(default=None)
    # Per-workspace environment variables / secrets, encrypted at rest (Fernet).
    # Merged OVER the user's Identity integration secrets at sandbox-exec time,
    # so a workspace can override (e.g. a different GITHUB_TOKEN) or add new
    # environment variables available to every command run in its sandbox.
    env_enc: str | None = Field(default=None)
    quarantined: bool = Field(default=False)
    last_raven_mission_id: int | None = Field(default=None)
    excludes: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    # The Identity user whose Nextcloud credentials back the sync. Set from
    # the first caller that syncs, so background syncs can run unattended.
    sync_owner: str | None = Field(default=None)
    last_sync_at: datetime | None = Field(default=None)
    last_sync_status: str | None = Field(default=None)  # "ok", "conflicts", "error"
    last_sync_error: str | None = Field(default=None)
    # Must be timezone-aware: SQLModel rejects naive datetimes on write
    # ("Datetime values must have timezone information"), and datetime.utcnow()
    # is deprecated from Python 3.12.
    created_at: datetime | None = Field(default_factory=lambda: datetime.now(UTC))


class WorkspaceSyncEntry(SQLModel, table=True):
    """One path's state the last time a workspace and Nextcloud agreed on it."""

    workspace_id: str = Field(primary_key=True)
    path: str = Field(primary_key=True)
    is_dir: bool = Field(default=False)
    size: int = Field(default=0)
    mtime_ns: int = Field(default=0)
    sha256: str = Field(default="")
    etag: str = Field(default="")
