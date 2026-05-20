# services/identity/schemas.py
"""
Pydantic schemas for the Identity Service API.
"""
from typing import Optional
from pydantic import BaseModel


# ─── Internal inter-service schema ────────────────────────────────────────────

class ResolveRequest(BaseModel):
    """Sent by the Gateway to resolve a caller's identity."""
    rag_user: Optional[str] = None
    voice_id: Optional[str] = None
    device_id: Optional[str] = None
    api_key: Optional[str] = None


class ResolvedCredentials(BaseModel):
    """
    The exact shape returned by /api/resolve.
    Mirrors the dict returned by the legacy get_user_creds() so downstream
    code requires zero changes.
    """
    user: str
    is_admin: bool = False
    api_key: Optional[str] = None         # decrypted at resolution time for tool usage
    nextcloud_url: Optional[str] = None
    nextcloud_user: Optional[str] = None
    nextcloud_pass: Optional[str] = None   # decrypted at resolution time
    ha_url: Optional[str] = None
    ha_token: Optional[str] = None         # decrypted at resolution time
    github_url: Optional[str] = None
    github_user: Optional[str] = None
    github_token: Optional[str] = None
    gitlab_url: Optional[str] = None
    gitlab_user: Optional[str] = None
    gitlab_token: Optional[str] = None
    audiobookshelf_url: Optional[str] = None
    audiobookshelf_user: Optional[str] = None
    audiobookshelf_pass: Optional[str] = None  # decrypted at resolution time
    preferred_tts_voice: Optional[str] = "af_heart"


# ─── External CRUD schemas ─────────────────────────────────────────────────────

class UserCreate(BaseModel):
    username: str
    display_name: str = ""
    is_admin: bool = False
    is_system_default: bool = False
    api_key: Optional[str] = None
    password: Optional[str] = None
    nextcloud_url: Optional[str] = None
    nextcloud_user: Optional[str] = None
    nextcloud_pass: Optional[str] = None
    ha_url: Optional[str] = None
    ha_token: Optional[str] = None
    github_url: Optional[str] = None
    github_user: Optional[str] = None
    github_token: Optional[str] = None
    gitlab_url: Optional[str] = None
    gitlab_user: Optional[str] = None
    gitlab_token: Optional[str] = None
    audiobookshelf_url: Optional[str] = None
    audiobookshelf_user: Optional[str] = None
    audiobookshelf_pass: Optional[str] = None
    preferred_tts_voice: Optional[str] = "af_heart"


class UserUpdate(BaseModel):
    display_name: Optional[str] = None
    nextcloud_url: Optional[str] = None
    nextcloud_user: Optional[str] = None
    nextcloud_pass: Optional[str] = None
    ha_url: Optional[str] = None
    ha_token: Optional[str] = None
    github_url: Optional[str] = None
    github_user: Optional[str] = None
    github_token: Optional[str] = None
    gitlab_url: Optional[str] = None
    gitlab_user: Optional[str] = None
    gitlab_token: Optional[str] = None
    audiobookshelf_url: Optional[str] = None
    audiobookshelf_user: Optional[str] = None
    audiobookshelf_pass: Optional[str] = None
    git_url: Optional[str] = None
    git_user: Optional[str] = None
    git_token: Optional[str] = None
    preferred_tts_voice: Optional[str] = None
    voice_fingerprint: Optional[str] = None
    is_admin: Optional[bool] = None
    is_system_default: Optional[bool] = None


class UserRead(BaseModel):
    id: int
    username: str
    display_name: str
    is_admin: bool
    is_system_default: bool
    nextcloud_url: Optional[str] = None
    nextcloud_user: Optional[str] = None
    ha_url: Optional[str] = None
    github_url: Optional[str] = None
    github_user: Optional[str] = None
    gitlab_url: Optional[str] = None
    gitlab_user: Optional[str] = None
    audiobookshelf_url: Optional[str] = None
    audiobookshelf_user: Optional[str] = None
    git_url: Optional[str] = None
    git_user: Optional[str] = None
    voice_fingerprint: Optional[str] = None
    preferred_tts_voice: Optional[str] = "af_heart"
    # NOTE: Encrypted fields (pass/token) are intentionally omitted from read responses


class DeviceAssignmentCreate(BaseModel):
    device_id: str
    username: str  # resolved to user_id on server


class DeviceAssignmentRead(BaseModel):
    id: int
    device_id: str
    user_id: int
    username: str

class LoginRequest(BaseModel):
    username: str
    password: str

class LoginResponse(BaseModel):
    api_key: str
    username: str
    is_admin: bool

class DiscoverUser(BaseModel):
    username: str
    source: str  # e.g. "Home Assistant", "Nextcloud"
    display_name: Optional[str] = None

class GlobalSettingRead(BaseModel):
    key: str
    value: str
    description: Optional[str] = None

class GlobalSettingUpdate(BaseModel):
    value: str

class RavenMissionCreate(BaseModel):
    slug: Optional[str] = None
    mission_type: str = "admin_fix"
    priority: int = 1
    target_container: Optional[str] = None
    error_summary: Optional[str] = None
    proposed_mission: str
    coding_model: Optional[str] = None
    user_id: Optional[int] = None

class RavenMissionUpdate(BaseModel):
    slug: Optional[str] = None
    status: Optional[str] = None
    progress: Optional[int] = None
    scheduled_for: Optional[str] = None
    output_log: Optional[str] = None
    result: Optional[str] = None

class RavenMissionRead(BaseModel):
    id: int
    slug: Optional[str] = None
    mission_type: str
    priority: int
    target_container: Optional[str] = None
    error_summary: Optional[str] = None
    proposed_mission: str
    coding_model: Optional[str] = None
    status: str
    progress: int
    scheduled_for: Optional[str] = None
    created_at: str
    output_log: Optional[str] = None
    result: Optional[str] = None
    user_id: Optional[int] = None
