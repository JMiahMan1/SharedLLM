# services/identity/schemas.py
"""
Pydantic schemas for the Identity Service API.
"""

from pydantic import BaseModel

# ─── Internal inter-service schema ────────────────────────────────────────────

class ResolveRequest(BaseModel):
    """Sent by the Gateway to resolve a caller's identity."""
    user_id: int | None = None
    rag_user: str | None = None
    voice_id: str | None = None
    device_id: str | None = None
    api_key: str | None = None


class ResolvedCredentials(BaseModel):
    """
    The exact shape returned by /api/resolve.
    Mirrors the dict returned by the legacy get_user_creds() so downstream
    code requires zero changes.
    """
    user: str
    is_admin: bool = False
    api_key: str | None = None         # decrypted at resolution time for tool usage
    nextcloud_url: str | None = None
    nextcloud_user: str | None = None
    nextcloud_pass: str | None = None   # decrypted at resolution time
    ha_url: str | None = None
    ha_token: str | None = None         # decrypted at resolution time
    github_url: str | None = None
    github_user: str | None = None
    github_token: str | None = None
    gitlab_url: str | None = None
    gitlab_user: str | None = None
    gitlab_token: str | None = None
    audiobookshelf_url: str | None = None
    audiobookshelf_user: str | None = None
    audiobookshelf_pass: str | None = None  # decrypted at resolution time
    audiobookshelf_api_key: str | None = None  # decrypted at resolution time
    mass_url: str | None = None
    mass_token: str | None = None           # decrypted at resolution time
    git_url: str | None = None
    git_user: str | None = None
    git_token: str | None = None
    huggingface_token: str | None = None
    skylight_url: str | None = None
    skylight_email: str | None = None
    skylight_pass: str | None = None
    skylight_enabled: bool = True
    preferred_tts_voice: str | None = "af_heart"
    calendar_settings: dict = {}  # per-user calendar integration prefs (default/disabled/priority/ical_urls)


# ─── External CRUD schemas ─────────────────────────────────────────────────────

class UserCreate(BaseModel):
    username: str
    display_name: str = ""
    is_admin: bool = False
    is_system_default: bool = False
    api_key: str | None = None
    password: str | None = None
    nextcloud_url: str | None = None
    nextcloud_user: str | None = None
    nextcloud_pass: str | None = None
    ha_url: str | None = None
    ha_token: str | None = None
    github_url: str | None = None
    github_user: str | None = None
    github_token: str | None = None
    gitlab_url: str | None = None
    gitlab_user: str | None = None
    gitlab_token: str | None = None
    audiobookshelf_url: str | None = None
    audiobookshelf_user: str | None = None
    audiobookshelf_pass: str | None = None
    audiobookshelf_api_key: str | None = None
    mass_url: str | None = None
    mass_token: str | None = None
    huggingface_token: str | None = None
    skylight_url: str | None = None
    skylight_email: str | None = None
    skylight_pass: str | None = None
    skylight_enabled: bool = True
    preferred_tts_voice: str | None = "af_heart"
    calendar_settings: dict = {}  # per-user calendar integration prefs (default/disabled/priority/ical_urls)


class UserUpdate(BaseModel):
    display_name: str | None = None
    nextcloud_url: str | None = None
    nextcloud_user: str | None = None
    nextcloud_pass: str | None = None
    ha_url: str | None = None
    ha_token: str | None = None
    github_url: str | None = None
    github_user: str | None = None
    github_token: str | None = None
    gitlab_url: str | None = None
    gitlab_user: str | None = None
    gitlab_token: str | None = None
    audiobookshelf_url: str | None = None
    audiobookshelf_user: str | None = None
    audiobookshelf_pass: str | None = None
    audiobookshelf_api_key: str | None = None
    mass_url: str | None = None
    mass_token: str | None = None
    git_url: str | None = None
    git_user: str | None = None
    git_token: str | None = None
    huggingface_token: str | None = None
    skylight_url: str | None = None
    skylight_email: str | None = None
    skylight_pass: str | None = None
    skylight_enabled: bool | None = None
    preferred_tts_voice: str | None = None
    voice_fingerprint: str | None = None
    is_admin: bool | None = None
    is_system_default: bool | None = None


class UserRead(BaseModel):
    id: int
    username: str
    display_name: str
    is_admin: bool
    is_system_default: bool
    nextcloud_url: str | None = None
    nextcloud_user: str | None = None
    ha_url: str | None = None
    github_url: str | None = None
    github_user: str | None = None
    gitlab_url: str | None = None
    gitlab_user: str | None = None
    audiobookshelf_url: str | None = None
    audiobookshelf_user: str | None = None
    audiobookshelf_api_key: str | None = None
    mass_url: str | None = None
    git_url: str | None = None
    git_user: str | None = None
    skylight_url: str | None = None
    skylight_email: str | None = None
    skylight_enabled: bool = True
    voice_fingerprint: str | None = None
    preferred_tts_voice: str | None = "af_heart"
    calendar_settings: dict = {}  # per-user calendar integration prefs (default/disabled/priority/ical_urls)
    api_key: str | None = None
    # NOTE: Encrypted fields (pass/token) are intentionally omitted from read responses


class DeviceAssignmentCreate(BaseModel):
    device_id: str
    username: str  # resolved to user_id on server
    revoked: bool = False  # not used on create, but included for schema completeness


class DeviceAssignmentRead(BaseModel):
    id: int
    device_id: str
    user_id: int
    username: str
    revoked: bool = False

class LoginRequest(BaseModel):
    username: str
    password: str

class LoginResponse(BaseModel):
    api_key: str
    username: str
    is_admin: bool

class DiscoverUser(BaseModel):
    username: str
    source: str  # e.g. "Home Assistant", "Nextcloud", "Home Assistant + Nextcloud"
    display_name: str | None = None
    email: str | None = None
    ha_person_id: str | None = None
    nc_username: str | None = None

class DiscoverResponse(BaseModel):
    users: list[DiscoverUser]
    warnings: list[str] = []
    errors: list[str] = []

class ImportUserResult(BaseModel):
    username: str
    display_name: str | None = None
    email: str | None = None
    source: str
    temp_password: str | None = None
    nextcloud_groups: list[str] = []
    ha_entity_id: str | None = None
    ha_device_trackers: list[str] = []

class ImportResponse(BaseModel):
    status: str
    message: str
    imported_users: list[ImportUserResult] = []
    warnings: list[str] = []
    errors: list[str] = []

class GlobalSettingRead(BaseModel):
    key: str
    value: str
    description: str | None = None

class GlobalSettingUpdate(BaseModel):
    value: str

class RavenMissionCreate(BaseModel):
    slug: str | None = None
    mission_type: str = "admin_fix"
    priority: int = 1
    target_container: str | None = None
    error_summary: str | None = None
    proposed_mission: str
    coding_model: str = "Qwen3.6-35B-A3B-MTP-GGUF/Qwen3.6-35B-A3B-UD-Q4_K_M"
    user_id: int | None = None
    queued_at: str | None = None
    workspace_id: str | None = None

class RavenMissionUpdate(BaseModel):
    slug: str | None = None
    status: str | None = None
    progress: int | None = None
    scheduled_for: str | None = None
    output_log: str | None = None
    result: str | None = None
    started_at: str | None = None
    completed_at: str | None = None
    duration: int | None = None
    workspace_id: str | None = None
    last_llm_reply: str | None = None
    artifacts: str | None = None

class RavenMissionRead(BaseModel):
    id: int
    slug: str | None = None
    mission_type: str
    priority: int
    target_container: str | None = None
    error_summary: str | None = None
    proposed_mission: str
    coding_model: str | None = None
    status: str
    progress: int
    scheduled_for: str | None = None
    created_at: str
    queued_at: str | None = None
    started_at: str | None = None
    completed_at: str | None = None
    duration: int | None = None
    output_log: str | None = None
    result: str | None = None
    workspace_id: str | None = None
    last_llm_reply: str | None = None
    artifacts: str | None = None

class RavenMissionListItem(BaseModel):
    id: int
    slug: str | None = None
    mission_type: str
    priority: int
    target_container: str | None = None
    error_summary: str | None = None
    proposed_mission: str
    coding_model: str | None = None
    status: str
    progress: int
    scheduled_for: str | None = None
    created_at: str
    queued_at: str | None = None
    started_at: str | None = None
    completed_at: str | None = None
    duration: int | None = None
    output_log: str | None = None
    result: str | None = None
    workspace_id: str | None = None
    last_llm_reply: str | None = None
    artifacts: str | None = None

class UserWidgetRead(BaseModel):
    widget_key: str
    visibility: str
    order_index: int
    size: str
    is_pinned: bool
    sort_mode: str | None = None
    pinned_devices: list[str] = []
    config: dict = {}
    updated_at: int


class WidgetSettingsRead(BaseModel):
    widgets: list[UserWidgetRead]
    quick_assistant_enabled: bool

class UserWidgetUpdate(BaseModel):
    visibility: str | None = None
    order_index: int | None = None
    size: str | None = None
    is_pinned: bool | None = None
    sort_mode: str | None = None
    pinned_devices: list[str] | None = None
    config: dict | None = None
    quick_assistant_enabled: bool | None = None
    user_id: int | None = None
