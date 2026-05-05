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


class ResolvedCredentials(BaseModel):
    """
    The exact shape returned by /api/resolve.
    Mirrors the dict returned by the legacy get_user_creds() so downstream
    code requires zero changes.
    """
    user: str
    is_admin: bool = False
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


# ─── External CRUD schemas ─────────────────────────────────────────────────────

class UserCreate(BaseModel):
    username: str
    display_name: str = ""
    is_admin: bool = False
    is_system_default: bool = False
    api_key: Optional[str] = None
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
