from typing import Optional, List
from sqlmodel import SQLModel, Field, JSON, Column


class Workspace(SQLModel, table=True):
    id: str = Field(primary_key=True)
    display_name: str
    access_policy: str = Field(default="authenticated")
    local_path: Optional[str] = None  # Legacy: kept for backward compat, maps to container_mount_path
    host_mount_path: Optional[str] = None  # Absolute path on host (e.g. /home/jeremiah/Code/SharedLLM)
    container_mount_path: Optional[str] = None  # Path inside container (e.g. relative to WORKSPACE_RUNTIME_ROOT)
    nextcloud_path: Optional[str] = None
    repo_url: Optional[str] = None
    git_remote: Optional[str] = Field(default="origin")
    default_branch: Optional[str] = Field(default="main")
    sync_mode: str = Field(default="local_git_authoritative")
    scope: str = Field(default="user")
    capabilities: List[str] = Field(default_factory=list, sa_column=Column(JSON))
    owner_user: Optional[str] = None  # Tied to Identity service user
    auto_pull_enabled: bool = Field(default=False)
    auto_backup_enabled: bool = Field(default=False)
    webhook_token: Optional[str] = Field(default=None)
    webhook_token_enc: Optional[str] = Field(default=None)
    quarantined: bool = Field(default=False)
    last_raven_mission_id: Optional[int] = Field(default=None)
    excludes: List[str] = Field(default_factory=list, sa_column=Column(JSON))

    @property
    def effective_container_path(self) -> str:
        """Returns the path to use for container-internal operations."""
        return self.container_mount_path or self.local_path

