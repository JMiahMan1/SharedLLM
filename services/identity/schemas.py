# services/identity/schemas.py
"""
Pydantic schemas for the Identity Service API.
"""

import json
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

FamilyRole = Literal["father", "mother", "child", "teen", "adult_child", "other"]

# Internal inter-service schema

class ResolveRequest(BaseModel):
    """Sent by the Gateway to resolve a caller's identity."""
    user_id: int | None = None
    rag_user: str | None = None
    voice_id: str | None = None
    device_id: str | None = None
    api_key: str | None = None
    anonymous: bool = False
    # Non-admin identity for shared household surfaces (kiosk, voice) that
    # need the household's Home Assistant and media credentials but must not
    # inherit the system administrator's powers or personal accounts.
    household: bool = False


class ResolvedCredentials(BaseModel):
    """
    The exact shape returned by /api/resolve.
    Mirrors the dict returned by the legacy get_user_creds() so downstream
    code requires zero changes.
    """
    user: str
    id: int | None = None              # numeric PK, needed for ownership checks
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
    audiobookshelf_api_key: str | None = None
    mailcow_url: str | None = None
    mailcow_api_key: str | None = None
    mail_user: str | None = None
    mail_pass: str | None = None  # decrypted at resolution time
    mass_url: str | None = None
    mass_token: str | None = None           # decrypted at resolution time
    git_url: str | None = None
    git_user: str | None = None
    git_token: str | None = None
    huggingface_token: str | None = None
    #: Branch patterns the workspace runtime must never push to directly
    #: (docs/INTEGRATIONS.md, GitHub > Protected Branches). Empty leaves the
    #: runtime's own default patterns in force.
    forbidden_branches: list[str] = []
    skylight_url: str | None = None
    skylight_email: str | None = None
    skylight_pass: str | None = None
    skylight_enabled: bool = True
    preferred_tts_voice: str | None = "af_heart"
    calendar_settings: dict = {}  # per-user calendar integration prefs (default/disabled/priority/ical_urls)
    # Where each shared-able service's credentials came from:
    #   "own"     -> this user's own entry
    #   "granted" -> borrowed from the system default user under an explicit grant
    #   "shared"  -> the system default user's shared account (skylight only)
    #   "absent"  -> not configured and not granted; the consumer must fail loudly
    credential_sources: dict = {}
    # Username whose credentials were borrowed, when a grant was used.
    shared_credential_owner: str | None = None


class CredentialSharesRead(BaseModel):
    """Which shared (system default user) services a user may borrow."""
    username: str
    services: list[str] = []
    granted_by: str | None = None
    granted_at: str | None = None
    note: str | None = None
    # The account the borrowed credentials come from.
    shared_owner: str | None = None


class CredentialSharesUpdate(BaseModel):
    services: list[str] = []
    note: str | None = None


# External CRUD schemas

class UserCreate(BaseModel):
    username: str
    display_name: str = ""
    is_admin: bool = False
    is_system_default: bool = False
    family_role: FamilyRole = "other"
    mother_username: str | None = None
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
    mailcow_url: str | None = None
    mailcow_api_key: str | None = None
    mail_user: str | None = None
    mail_pass: str | None = None
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
    mailcow_url: str | None = None
    mailcow_api_key: str | None = None
    mail_user: str | None = None
    mail_pass: str | None = None
    mass_url: str | None = None
    mass_token: str | None = None
    git_url: str | None = None
    git_user: str | None = None
    git_token: str | None = None
    huggingface_token: str | None = None
    #: Branch patterns Jarvis must never push to directly (GitHub > Protected
    #: Branches). Stored as a JSON array string; sent as a list.
    forbidden_branches: list[str] | None = None
    skylight_url: str | None = None
    skylight_email: str | None = None
    skylight_pass: str | None = None
    skylight_enabled: bool | None = None
    #: The family chat conversation to open on launch (clear with clear_fields).
    chat_default_token: str | None = None
    preferred_tts_voice: str | None = None
    is_admin: bool | None = None
    is_system_default: bool | None = None
    # Erase these fields. A blank value never erases anything (it means "leave
    # it"), so removing a saved login has to be asked for by name.
    clear_fields: list[str] | None = None


class ShareRecipient(BaseModel):
    """The minimum needed to pick who to share activity with.

    Deliberately narrower than ``UserRead``, which carries integration URLs,
    credential fields, the voice fingerprint and the raw API key. A share
    picker only needs to render "who are the other people here" -- it has no
    business seeing any of that, and a non-admin must be able to build the
    picker at all (this endpoint is what makes opt-in sharing usable for them).
    """

    username: str
    display_name: str


# Plain credential field -> its encrypted column on User. The one map the
# update, reveal and read paths all use.
CREDENTIAL_COLUMNS: dict[str, str] = {
    "nextcloud_pass": "nextcloud_pass_enc",
    "ha_token": "ha_token_enc",
    "github_token": "github_token_enc",
    "gitlab_token": "gitlab_token_enc",
    "audiobookshelf_pass": "audiobookshelf_pass_enc",
    "audiobookshelf_api_key": "audiobookshelf_api_key_enc",
    "mailcow_api_key": "mailcow_api_key_enc",
    "mail_pass": "mail_pass_enc",
    "mass_token": "mass_token_enc",
    "git_token": "git_token_enc",
    "huggingface_token": "huggingface_token_enc",
    "skylight_pass": "skylight_pass_enc",
}


def forbidden_branch_list(raw: Any) -> list[str]:
    """A user's protected-branch patterns, however they are stored.

    The User row keeps them as a JSON array string (the same pattern as
    ``UserWidget.pinned_devices``) so a list survives SQLite; this turns that
    string, an already-parsed list, or a legacy comma-separated string into the
    clean list the API, ``/api/resolve`` and the workspace runtime expect.
    """
    if raw is None:
        return []
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            raw = [part.strip() for part in raw.split(",")]
    if not isinstance(raw, list):
        return []
    return [str(item).strip() for item in raw if str(item).strip()]


class UserRead(BaseModel):
    id: int
    username: str
    display_name: str
    is_admin: bool
    is_system_default: bool
    #: Whether the account has a password at all. An account without one is
    #: listed, appears in the family pages, and cannot sign in - which is worth
    #: showing rather than leaving as a mystery "invalid username or password".
    has_password: bool = False
    #: Whether the account still signs in with the seeded DEFAULT_ADMIN_PASSWORD.
    #: It is the one credential a stranger can guess, so the Admin page says so
    #: instead of leaving it in a log line nobody reads.
    has_default_password: bool = False
    #: Household relationship and the mother account a child/teen belongs to.
    family_role: FamilyRole = "other"
    mother_username: str | None = None
    #: Derived: child/teen are minors. Their activity sharing cannot be turned
    #: off (the family circle sees totals); adult children can opt out.
    is_minor: bool = False
    sharing_locked: bool = False
    #: Ids of the roles this user holds, and the permissions they add up to
    #: (every permission for an admin). Filled by Identity, not stored.
    role_ids: list[int] = []
    permissions: list[str] = []
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
    mailcow_url: str | None = None
    mailcow_api_key: str | None = None
    mail_user: str | None = None
    mail_pass: str | None = None
    mass_url: str | None = None
    git_url: str | None = None
    git_user: str | None = None
    #: Branch patterns Jarvis must never push to directly (see ResolvedCredentials).
    forbidden_branches: list[str] = []
    skylight_url: str | None = None
    skylight_email: str | None = None
    skylight_enabled: bool = True
    avatar: str | None = None
    chat_default_token: str | None = None
    #: Skylight for this user: "connected" (the household account is set up
    #: and they have it on), "off" (they turned it off) or "not_set_up".
    skylight_status: str | None = None
    voice_fingerprint: str | None = None
    preferred_tts_voice: str | None = "af_heart"
    calendar_settings: dict = {}  # per-user calendar integration prefs (default/disabled/priority/ical_urls)
    api_key: str | None = None
    # NOTE: Encrypted fields (pass/token) are intentionally omitted from read responses.
    # Which of them hold a value, by plain field name: without this a saved
    # password looked identical to a missing one, and the forms showed blanks.
    saved_credentials: list[str] = []

    @model_validator(mode="before")
    @classmethod
    def _list_saved_credentials(cls, data: Any) -> Any:
        if isinstance(data, dict):
            # FastAPI dumps the User row to a dict first; the *_enc keys are
            # still there (and dropped below as unknown fields).
            if "saved_credentials" not in data:
                data = {**data, "saved_credentials": [p for p, enc in CREDENTIAL_COLUMNS.items() if data.get(enc)]}
            if "forbidden_branches" in data:
                data = {**data, "forbidden_branches": forbidden_branch_list(data.get("forbidden_branches"))}
            return data
        saved = [plain for plain, enc in CREDENTIAL_COLUMNS.items() if getattr(data, enc, None)]
        values = {name: getattr(data, name, None) for name in cls.model_fields if name != "saved_credentials"}
        values = {k: v for k, v in values.items() if v is not None}
        values["saved_credentials"] = saved
        values["forbidden_branches"] = forbidden_branch_list(getattr(data, "forbidden_branches", None))
        return values


class DeviceAssignmentCreate(BaseModel):
    device_id: str
    username: str  # resolved to user_id on server
    revoked: bool = False


class DeviceAssignmentRead(BaseModel):
    id: int
    device_id: str
    user_id: int
    username: str
    revoked: bool = False


class RoleWrite(BaseModel):
    name: str
    description: str = ""
    permissions: list[str] = []


class RoleRead(BaseModel):
    id: int
    name: str
    description: str
    permissions: list[str]
    builtin: bool
    builtin_key: str | None = None
    members: list[str]


class UserRolesUpdate(BaseModel):
    role_ids: list[int]


class FamilyUpdate(BaseModel):
    """Change one person's place in the family, without touching credentials.

    ``mother_username`` is only meaningful for a child or teen. Send an empty
    string (or ``clear_mother``) to remove the link; omitting it leaves the
    existing link alone.
    """

    family_role: FamilyRole | None = None
    mother_username: str | None = None
    clear_mother: bool = False


class FamilyMemberRead(BaseModel):
    """Safe family-list fields; never credentials, keys or integration URLs."""

    username: str
    display_name: str
    family_role: FamilyRole
    mother_username: str | None = None
    is_minor: bool = False
    sharing_locked: bool = False
    is_admin: bool = False
    avatar: str | None = None


class EntityProtectionUpdate(BaseModel):
    """Body for locking/unlocking one entity.

    `permitted_usernames` is a list of usernames that may control a protected
    entity even though they are not admins. Sending `protected: false` releases
    the lock and forgets the permit list (the entity reverts to plain
    DeviceAssignment rules).
    """
    protected: bool = True
    permitted_usernames: list[str] = Field(default_factory=list)
    note: str | None = None


class EntityProtectionRead(BaseModel):
    entity_id: str
    permitted_usernames: list[str] = Field(default_factory=list)
    granted_by: str | None = None
    granted_at: str | None = None
    note: str | None = None


class LoginRequest(BaseModel):
    username: str
    password: str

class LoginResponse(BaseModel):
    api_key: str
    username: str
    is_admin: bool

class ChangePasswordRequest(BaseModel):
    new_password: str

class DiscoverUser(BaseModel):
    username: str
    source: str  # e.g. "Home Assistant", "Nextcloud", "Home Assistant + Nextcloud"
    display_name: str | None = None
    email: str | None = None
    ha_person_id: str | None = None
    nc_username: str | None = None
    abs_username: str | None = None
    mail_address: str | None = None
    mailcow_address: str | None = None

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
    depends_on_mission_id: int | None = None
    next_mission_query: str | None = None

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
    depends_on_mission_id: int | None = None
    next_mission_query: str | None = None

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
    depends_on_mission_id: int | None = None
    next_mission_query: str | None = None

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


# ---------------------------------------------------------------------------
# User Panel: device registry
# ---------------------------------------------------------------------------

DEVICE_KINDS = ("phone", "tablet", "assistant", "light", "watch")


class DeviceSelfRegister(BaseModel):
    """What a phone or tablet reports about itself when it logs in.

    The client may declare only its own mobile form factor (``phone`` or
    ``tablet``), never a full ``kind``: letting the app claim ``kind="light"``
    would move itself out of the advanced panel. Assistants, lights and watches
    go through the admin or claim endpoints. Omitting ``form_factor`` keeps an
    older client working: the server treats it as a phone.

    Deliberately no IP field: the address is taken from the request, not
    accepted from the client, so a caller cannot record someone else's.
    """

    device_key: str = Field(min_length=8, max_length=128)
    #: The mobile form factor this device is. Anything but phone/tablet is a
    #: 422; the server never lets the client name a non-mobile kind.
    form_factor: Literal["phone", "tablet"] | None = None
    model: str | None = None
    manufacturer: str | None = None
    os_version: str | None = None
    os_build: str | None = None
    app_version: str | None = None
    app_build: str | None = None


class DeviceAdminCreate(BaseModel):
    """Register an assistant or light, which has no account to log in with."""

    device_key: str = Field(min_length=1, max_length=128)
    kind: str
    label: str = ""
    owner_username: str | None = None
    entity_id: str | None = None
    esphome_version: str | None = None
    hardware: str | None = None
    capabilities: dict = Field(default_factory=dict)


class DeviceClaim(BaseModel):
    """A user claiming a companion device through Jarvis (internal: sent by
    the execution service once pairing or adoption has succeeded)."""

    device_key: str = Field(min_length=1, max_length=128)
    kind: str
    label: str = ""
    owner_username: str = Field(min_length=1)
    #: True when the user proved possession with the code the device showed.
    verified: bool = False
    esphome_version: str | None = None
    #: The device's own firmware version (ESPHome project version), e.g. 1.2.0.
    app_version: str | None = None
    hardware: str | None = None
    capabilities: dict = Field(default_factory=dict)
    ip_address: str | None = None


class DeviceAssign(BaseModel):
    owner_username: str | None = None


class BatteryReading(BaseModel):
    """One battery report from a device (telemetry event "battery")."""

    at: str
    pct: float | None = None  # absent while on USB, where the watch cannot read its cell
    usb: bool | None = None
    cell_v: float | None = None


class DeviceRead(BaseModel):
    id: int | None = None
    device_key: str
    kind: str
    label: str = ""
    owner_username: str | None = None
    registered_by: str = "self"
    revoked: bool = False
    entity_id: str | None = None
    model: str | None = None
    manufacturer: str | None = None
    os_version: str | None = None
    app_version: str | None = None
    app_build: str | None = None
    esphome_version: str | None = None
    hardware: str | None = None
    capabilities: dict = Field(default_factory=dict)
    last_ip_address: str | None = None
    last_seen_at: str | None = None
    first_seen_at: str | None = None
    #: The device's latest battery report, when it sends them.
    battery: BatteryReading | None = None


class DeviceEventRead(BaseModel):
    """One usage event from a device, with whatever small scalars it carried."""

    event: str
    at: str
    extra: dict = Field(default_factory=dict)


class DeviceActivity(BaseModel):
    """Everything one device has reported lately, for its detail view.

    Built from the no-opt-in DeviceEvent allowlist, so it carries counts and
    small scalars -- an event name, a battery percentage, a step count -- and
    never content.
    """

    device: DeviceRead
    #: How many of each event arrived inside the window.
    counts: dict[str, int] = Field(default_factory=dict)
    #: The most recent events, newest first.
    events: list[DeviceEventRead] = Field(default_factory=list)
    first_seen_at: str | None = None
    last_seen_at: str | None = None


class TelemetryIngest(BaseModel):
    """A usage event, written without opt-in.

    ``event`` is checked against NO_OPT_IN_EVENTS; ``extra`` is expected to hold
    small non-content scalars. Anything content-derived belongs in
    AssistantConversation, which has its own retention policy.
    """

    device_key: str = Field(min_length=1, max_length=128)
    events: list[dict]
    #: The firmware/app version the device is running, kept current on the
    #: device record (pairing alone records it once, and devices update).
    app_version: str | None = Field(default=None, max_length=32)
