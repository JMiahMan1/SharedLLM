# services/identity/main.py
"""
Microservice 1: Identity & Profile Service
Manages user profiles, device assignments, and secure credential resolution.
"""
import base64
import binascii
import hashlib
import json
import logging
import os
import re
import secrets
import time
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from datetime import datetime as dt
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import aiohttp
from fastapi import BackgroundTasks, Depends, FastAPI, File, Header, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import inspect, text
from sqlmodel import Session, SQLModel, create_engine, select

from services.common.http import get_client, get_client_insecure
from services.common.screen_time import PERMISSION_GROUPS
from services.common.secrets import secret_matches
from services.config import IDENTITY_CORS_ORIGINS, IDENTITY_DATABASE_URL, INTERNAL_SECRET
from services.identity import audit, battery_alerts, push, roles, voice
from services.identity.crypto import decrypt, digest_secret, encrypt
from services.identity.models import (
    DEFAULT_GLOBAL_SETTINGS,
    DEVICE_KINDS,
    MOBILE_DEVICE_KINDS,
    NO_OPT_IN_EVENTS,
    APIKey,
    AuditEvent,
    CapabilityInventory,
    Device,
    DeviceAssignment,
    DeviceEvent,
    DnsRecord,
    EntityProtection,
    GlobalSetting,
    GuardianNote,
    GuardOfflineCode,
    GuardRequest,
    LinkedAccount,
    RavenMission,
    RecommendationLanes,
    Role,
    ScreenRules,
    ScreenTimeRequest,
    User,
    UserActivitySharing,
    UserAvatarImage,
    UserCalendarSetting,
    UserCredentialShare,
    UserRole,
    UserThemeSetting,
    UserWidget,
    UserWorkspaceSession,
    VoiceSignature,
    WorkspaceChat,
    WorkspaceChatMessage,
)
from services.identity.schemas import (
    CREDENTIAL_COLUMNS,
    BatteryReading,
    ChangePasswordRequest,
    CredentialSharesRead,
    CredentialSharesUpdate,
    DeviceActivity,
    DeviceAdminCreate,
    DeviceAssign,
    DeviceAssignmentCreate,
    DeviceAssignmentRead,
    DeviceClaim,
    DeviceEventRead,
    DeviceRead,
    DeviceSelfRegister,
    DiscoverResponse,
    DiscoverUser,
    EntityProtectionRead,
    EntityProtectionUpdate,
    FamilyMemberRead,
    FamilyUpdate,
    GlobalSettingRead,
    GlobalSettingUpdate,
    ImportResponse,
    LoginRequest,
    LoginResponse,
    RavenMissionCreate,
    RavenMissionListItem,
    RavenMissionRead,
    RavenMissionUpdate,
    ResolvedCredentials,
    ResolveRequest,
    RoleRead,
    RoleWrite,
    ShareRecipient,
    TelemetryIngest,
    UserCreate,
    UserRead,
    UserRolesUpdate,
    UserUpdate,
    UserWidgetRead,
    UserWidgetUpdate,
    WidgetSettingsRead,
    forbidden_branch_list,
)
from services.identity.seed import hash_password, seed_from_env, system_service_urls, verify_password
from services.shared.info_endpoint import info_router

# Config

log = logging.getLogger("identity")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s")


def _require_internal_secret(x_internal_secret: str | None) -> None:
    expected = os.getenv("INTERNAL_SECRET", INTERNAL_SECRET)
    if not x_internal_secret or not (
        (expected and secret_matches(x_internal_secret, expected))
        or (INTERNAL_SECRET and secret_matches(x_internal_secret, INTERNAL_SECRET))
    ):
        raise HTTPException(status_code=403, detail="Forbidden")

DATABASE_URL = IDENTITY_DATABASE_URL or "sqlite:///default.db"

if "sqlite" in DATABASE_URL:
    # The small deployment runs file-backed SQLite. Its branch kept the
    # default 5 + 10 connection pool: a phone's parallel location posts could
    # exhaust it and the wait turned into 500s. Widen it like the server
    # database branch. An in-memory database (the tests) uses SQLAlchemy's
    # single-threaded pool, which has no size knobs.
    sqlite_kwargs: dict = {
        "connect_args": {"check_same_thread": False, "timeout": 30},
        "pool_pre_ping": True,
    }
    if DATABASE_URL != "sqlite://" and ":memory:" not in DATABASE_URL:
        sqlite_kwargs.update(pool_size=20, max_overflow=40, pool_timeout=60)
    engine = create_engine(DATABASE_URL, **sqlite_kwargs)
else:
    engine = create_engine(
        DATABASE_URL,
        pool_size=20,
        max_overflow=40,
        pool_timeout=60,
        pool_pre_ping=True
    )



def _ensure_schema_upgrades() -> None:
    _table_columns: dict[str, set[str]] = {}
    _known_tables: set[str] = set()

    def _table_exists(table_name: str) -> bool:
        if table_name not in _known_tables:
            try:
                _known_tables.add(table_name) if table_name in inspect(engine).get_table_names() else None
            except Exception:
                return False
        return table_name in _known_tables

    def _has_column(table_name: str, col_name: str) -> bool:
        key = table_name
        if key not in _table_columns:
            _table_columns[key] = {c["name"] for c in inspect(engine).get_columns(table_name)} if _table_exists(table_name) else set()
        return col_name in _table_columns[key]

    def _add_column(table_name: str, col_name: str, sql: str) -> None:
        if not _has_column(table_name, col_name):
            with engine.connect() as conn:
                conn.execute(text(sql))
                conn.commit()

    if _table_exists("user"):
        _add_column("user", "is_admin", "ALTER TABLE user ADD COLUMN is_admin BOOLEAN NOT NULL DEFAULT 0")
        _add_column("user", "family_role", "ALTER TABLE user ADD COLUMN family_role VARCHAR NOT NULL DEFAULT 'other'")
        _add_column("user", "mother_username", "ALTER TABLE user ADD COLUMN mother_username VARCHAR")
        _add_column("user", "password_hash", "ALTER TABLE user ADD COLUMN password_hash VARCHAR")
        _add_column("user", "api_key", "ALTER TABLE user ADD COLUMN api_key VARCHAR")
        _add_column("user", "api_key_enc", "ALTER TABLE user ADD COLUMN api_key_enc VARCHAR")
        _add_column("user", "api_key_hash", "ALTER TABLE user ADD COLUMN api_key_hash VARCHAR")
        _add_column("user", "github_url", "ALTER TABLE user ADD COLUMN github_url VARCHAR")
        _add_column("user", "github_user", "ALTER TABLE user ADD COLUMN github_user VARCHAR")
        _add_column("user", "github_token_enc", "ALTER TABLE user ADD COLUMN github_token_enc VARCHAR")
        _add_column("user", "huggingface_token_enc", "ALTER TABLE user ADD COLUMN huggingface_token_enc VARCHAR")
        _add_column("user", "gitlab_url", "ALTER TABLE user ADD COLUMN gitlab_url VARCHAR")
        _add_column("user", "gitlab_user", "ALTER TABLE user ADD COLUMN gitlab_user VARCHAR")
        _add_column("user", "gitlab_token_enc", "ALTER TABLE user ADD COLUMN gitlab_token_enc VARCHAR")
        _add_column("user", "audiobookshelf_url", "ALTER TABLE user ADD COLUMN audiobookshelf_url VARCHAR")
        _add_column("user", "audiobookshelf_user", "ALTER TABLE user ADD COLUMN audiobookshelf_user VARCHAR")
        _add_column("user", "audiobookshelf_pass_enc", "ALTER TABLE user ADD COLUMN audiobookshelf_pass_enc VARCHAR")
        _add_column("user", "audiobookshelf_api_key_enc", "ALTER TABLE user ADD COLUMN audiobookshelf_api_key_enc VARCHAR")
        _add_column("user", "mailcow_url", "ALTER TABLE user ADD COLUMN mailcow_url VARCHAR")
        _add_column("user", "mailcow_api_key_enc", "ALTER TABLE user ADD COLUMN mailcow_api_key_enc VARCHAR")
        _add_column("user", "mail_user", "ALTER TABLE user ADD COLUMN mail_user VARCHAR")
        _add_column("user", "mail_pass_enc", "ALTER TABLE user ADD COLUMN mail_pass_enc VARCHAR")
        _add_column("user", "mass_url", "ALTER TABLE user ADD COLUMN mass_url VARCHAR")
        _add_column("user", "mass_token_enc", "ALTER TABLE user ADD COLUMN mass_token_enc VARCHAR")
        _add_column("user", "skylight_enabled", "ALTER TABLE user ADD COLUMN skylight_enabled BOOLEAN NOT NULL DEFAULT 1")
        _add_column("user", "skylight_url", "ALTER TABLE user ADD COLUMN skylight_url VARCHAR")
        _add_column("user", "avatar", "ALTER TABLE user ADD COLUMN avatar VARCHAR")
        _add_column("user", "chat_default_token", "ALTER TABLE user ADD COLUMN chat_default_token VARCHAR")
        _add_column("user", "push_topic", "ALTER TABLE user ADD COLUMN push_topic VARCHAR")
        _add_column("user", "skylight_email", "ALTER TABLE user ADD COLUMN skylight_email VARCHAR")
        _add_column("user", "skylight_pass_enc", "ALTER TABLE user ADD COLUMN skylight_pass_enc VARCHAR")
        _add_column("user", "git_url", "ALTER TABLE user ADD COLUMN git_url VARCHAR")
        _add_column("user", "git_user", "ALTER TABLE user ADD COLUMN git_user VARCHAR")
        _add_column("user", "git_token_enc", "ALTER TABLE user ADD COLUMN git_token_enc VARCHAR")
        _add_column("user", "forbidden_branches", "ALTER TABLE user ADD COLUMN forbidden_branches VARCHAR")
        _add_column("user", "router_url", "ALTER TABLE user ADD COLUMN router_url VARCHAR")
        _add_column("user", "router_user", "ALTER TABLE user ADD COLUMN router_user VARCHAR")
        _add_column("user", "router_pass_enc", "ALTER TABLE user ADD COLUMN router_pass_enc VARCHAR")
        _add_column("user", "router_cert_sha256", "ALTER TABLE user ADD COLUMN router_cert_sha256 VARCHAR")
        _add_column("user", "voice_fingerprint", "ALTER TABLE user ADD COLUMN voice_fingerprint VARCHAR")
        _add_column("user", "preferred_tts_voice", "ALTER TABLE user ADD COLUMN preferred_tts_voice VARCHAR DEFAULT 'af_heart'")

    if _table_exists("apikey"):
        _add_column("apikey", "key_hash", "ALTER TABLE apikey ADD COLUMN key_hash VARCHAR")
        _add_column("apikey", "key_prefix", "ALTER TABLE apikey ADD COLUMN key_prefix VARCHAR")

    if _table_exists("ravenmission"):
        _add_column("ravenmission", "slug", "ALTER TABLE ravenmission ADD COLUMN slug VARCHAR")
        _add_column("ravenmission", "queued_at", "ALTER TABLE ravenmission ADD COLUMN queued_at VARCHAR")
        _add_column("ravenmission", "started_at", "ALTER TABLE ravenmission ADD COLUMN started_at VARCHAR")
        _add_column("ravenmission", "completed_at", "ALTER TABLE ravenmission ADD COLUMN completed_at VARCHAR")
        _add_column("ravenmission", "duration", "ALTER TABLE ravenmission ADD COLUMN duration INTEGER")
        _add_column("ravenmission", "workspace_id", "ALTER TABLE ravenmission ADD COLUMN workspace_id VARCHAR")
        _add_column("ravenmission", "last_llm_reply", "ALTER TABLE ravenmission ADD COLUMN last_llm_reply TEXT")
        _add_column("ravenmission", "artifacts", "ALTER TABLE ravenmission ADD COLUMN artifacts TEXT")

    if _table_exists("deviceassignment"):
        _add_column("deviceassignment", "revoked", "ALTER TABLE deviceassignment ADD COLUMN revoked BOOLEAN NOT NULL DEFAULT 0")
        # A device used to have one user (unique device_id); now it can have
        # several, unique per (device, user).
        indexes = {ix["name"]: ix for ix in inspect(engine).get_indexes("deviceassignment")}
        if indexes.get("ix_deviceassignment_device_id", {}).get("unique"):
            with engine.connect() as conn:
                conn.execute(text("DROP INDEX ix_deviceassignment_device_id"))
                conn.execute(text("CREATE INDEX ix_deviceassignment_device_id ON deviceassignment (device_id)"))
                conn.commit()
        if "uq_deviceassignment_device_user" not in indexes:
            with engine.connect() as conn:
                conn.execute(text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS uq_deviceassignment_device_user ON deviceassignment (device_id, user_id)"
                ))
                conn.commit()

    if not _table_exists("user_widgets"):
        with engine.connect() as conn:
            conn.execute(text("""
                CREATE TABLE user_widgets (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username VARCHAR NOT NULL,
                    widget_key VARCHAR NOT NULL,
                    visibility VARCHAR NOT NULL DEFAULT 'visible',
                    order_index INTEGER NOT NULL DEFAULT 0,
                    size VARCHAR NOT NULL DEFAULT 'medium',
                    is_pinned BOOLEAN NOT NULL DEFAULT 0,
                    sort_mode VARCHAR,
                    pinned_devices VARCHAR NOT NULL DEFAULT '[]',
                    config VARCHAR NOT NULL DEFAULT '{}',
                    updated_at INTEGER NOT NULL
                )
            """))
            conn.commit()

    if not _table_exists("userthemesetting"):
        with engine.connect() as conn:
            conn.execute(text("""
                CREATE TABLE userthemesetting (
                    username VARCHAR PRIMARY KEY,
                    data VARCHAR NOT NULL DEFAULT '{}'
                )
            """))
            conn.commit()

    if not _table_exists("useractivitysharing"):
        with engine.connect() as conn:
            conn.execute(text("""
                CREATE TABLE useractivitysharing (
                    username VARCHAR PRIMARY KEY,
                    data VARCHAR NOT NULL DEFAULT '{}'
                )
            """))
            conn.commit()

    if not _table_exists("usercredentialshare"):
        with engine.connect() as conn:
            conn.execute(text("""
                CREATE TABLE usercredentialshare (
                    username VARCHAR PRIMARY KEY,
                    services VARCHAR NOT NULL DEFAULT '[]',
                    granted_by VARCHAR,
                    granted_at VARCHAR,
                    note VARCHAR
                )
            """))
            conn.commit()


# Credential sharing policy
# Every user resolves a service with their OWN credentials. The system default
# user ("User 1": is_system_default, or id 1, or username "default"; it may
# have been renamed) additionally owns the shared credentials. Any other user
# may borrow them for a service only when an admin granted that service to them
# (UserCredentialShare). Ungranted + unconfigured resolves to None so the
# consumer fails loudly rather than acting as the shared account.
SHARED_CREDENTIAL_SERVICES = (
    "home_assistant",
    "music_assistant",
    "audiobookshelf",
    "nextcloud",
)


def _is_system_default_user(user: User | None) -> bool:
    """True for the "User 1" account that owns the shared credentials."""
    if user is None:
        return False
    return bool(user.is_system_default or user.id == 1 or user.username == "default")


def _system_default_user(session: Session) -> User | None:
    """The shared-credential owner: is_system_default first, then the legacy keys."""
    user = session.exec(select(User).where(User.is_system_default == True)).first()  # noqa: E712
    if user:
        return user
    user = session.exec(select(User).where(User.id == 1)).first()
    if user:
        return user
    return session.exec(select(User).where(User.username == "default")).first()


def _granted_services(session: Session, username: str) -> list[str]:
    row = session.exec(
        select(UserCredentialShare).where(UserCredentialShare.username == username)
    ).first()
    if not row or not row.services:
        return []
    try:
        parsed = json.loads(row.services)
    except (ValueError, TypeError):
        log.warning(f"[credentials] Grant row for {username} is not valid JSON: {row.services!r}; ignoring")
        return []
    if not isinstance(parsed, list):
        return []
    return [s for s in parsed if isinstance(s, str)]


def _decrypt_field(enc: str | None) -> str | None:
    return decrypt(enc) if enc else None


def _own_service_creds(user: User, service: str) -> dict[str, str | None]:
    """The user's own plaintext credentials for one service."""
    if service == "home_assistant":
        return {"ha_url": user.ha_url, "ha_token": _decrypt_field(user.ha_token_enc)}
    if service == "music_assistant":
        return {"mass_url": user.mass_url, "mass_token": _decrypt_field(user.mass_token_enc)}
    if service == "audiobookshelf":
        return {
            "audiobookshelf_url": user.audiobookshelf_url,
            "audiobookshelf_user": user.audiobookshelf_user,
            "audiobookshelf_pass": _decrypt_field(user.audiobookshelf_pass_enc),
            "audiobookshelf_api_key": _decrypt_field(user.audiobookshelf_api_key_enc),
        }
    if service == "nextcloud":
        return {
            "nextcloud_url": user.nextcloud_url,
            "nextcloud_user": user.nextcloud_user,
            "nextcloud_pass": _decrypt_field(user.nextcloud_pass_enc),
        }
    raise ValueError(f"Unknown credential service: {service}")


def _resolve_service_creds(
    session: Session, user: User, service: str, granted: set[str]
) -> tuple[dict[str, str | None], str, str | None]:
    """Return (values, source, shared_owner) for one shared-able service."""
    own = _own_service_creds(user, service)
    if any(own.values()):
        return own, "own", None

    # The shared account uses its own entry; nothing to borrow.
    if _is_system_default_user(user):
        return own, "absent", None

    if service not in granted:
        return own, "absent", None

    shared_user = _system_default_user(session)
    if shared_user is None:
        log.warning(
            f"[credentials] {user.username} is granted the shared {service} credentials "
            f"but no system default user exists; nothing to borrow"
        )
        return own, "absent", None

    shared = _own_service_creds(shared_user, service)
    if not any(shared.values()):
        log.warning(
            f"[credentials] {user.username} is granted the shared {service} credentials "
            f"but the system default user ({shared_user.username}) has none configured"
        )
        return own, "absent", None

    log.info(
        f"[credentials] {user.username} is using the shared {service} credentials "
        f"granted to them (owner={shared_user.username})"
    )
    return shared, "granted", shared_user.username


async def _ensure_default_settings(session: Session) -> None:
    existing_keys = {
        setting.key
        for setting in session.exec(select(GlobalSetting)).all()
    }

    # Insert missing defaults exactly as defined in DEFAULT_GLOBAL_SETTINGS.
    # Model settings default to "" (unconfigured); they MUST be set explicitly
    # via the UI before inference will work. No auto-resolution is performed here
    # because silently picking the wrong model causes OOMs and load failures.
    inserted = False
    for setting in DEFAULT_GLOBAL_SETTINGS:
        if setting["key"] in existing_keys:
            continue
        session.add(GlobalSetting(key=setting["key"], value=setting["value"], description=setting.get("description")))
        inserted = True
    if inserted:
        session.commit()

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Check DB file state before initialization
    db_path = DATABASE_URL.replace("sqlite:///", "") if "sqlite" in DATABASE_URL else "unknown"
    try:
        import os.path as osp
        db_exists = osp.exists(db_path)
        db_size = osp.getsize(db_path) if db_exists else 0
        log.info(f"[lifespan] DB file: path={db_path}, exists={db_exists}, size={db_size} bytes")
    except Exception as e:
        log.warning(f"[lifespan] Could not check DB file state: {e}")

    # Ensure tables exist
    log.info("[lifespan] Creating tables via SQLModel.metadata.create_all()...")
    SQLModel.metadata.create_all(engine)
    log.info("[lifespan] Tables creation complete")

    _ensure_schema_upgrades()
    log.info("[lifespan] Schema upgrades applied")

    # Run initial seed if needed
    with Session(engine) as session:
        # Check actual DB state before seeding
        user_count = session.exec(select(User)).count() if hasattr(session.exec(select(User)), "count") else len(session.exec(select(User)).all())
        log.info(f"[lifespan] User count before seed: {user_count}")

        force_reseed = os.getenv("FORCE_RESEED", "false").lower() == "true"
        if force_reseed:
            log.info("[lifespan] FORCE_RESEED=true, re-seeding all data")
        seed_from_env(session, force=force_reseed)
        await _ensure_default_settings(session)
        _migrate_api_key_material(session)
        roles.seed_default_roles(session)
        family_synced = False
        for existing in session.exec(select(User)).all():
            family_synced = roles.sync_family_role(session, existing) or family_synced
        if family_synced:
            session.commit()
    yield

app = FastAPI(title="Jarvis OS Identity Service", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=IDENTITY_CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(info_router)

# Dependencies

def get_session():
    with Session(engine) as session:
        yield session


def _api_key_prefix(key_value: str | None) -> str | None:
    if not key_value:
        return None
    return f"{key_value[:8]}..."


def _store_user_api_key(user: User, key_value: str | None) -> str | None:
    key_value = (key_value or "").strip() or None
    user.api_key_enc = encrypt(key_value) if key_value else None
    user.api_key_hash = digest_secret(key_value) if key_value else None
    user.api_key = None
    return key_value


def _get_user_api_key(user: User) -> str | None:
    return decrypt(user.api_key_enc) if user.api_key_enc else user.api_key


def _store_generated_api_key(record: APIKey, key_value: str | None) -> str | None:
    key_value = (key_value or "").strip() or None
    key_hash = digest_secret(key_value) if key_value else None
    record.key_hash = key_hash
    record.key_prefix = _api_key_prefix(key_value)
    record.key_value = key_hash
    return key_value


def _find_user_for_api_key(session: Session, key_value: str) -> User | None:
    if not key_value:
        return None
    key_hash = digest_secret(key_value)

    api_key_obj = session.exec(select(APIKey).where(APIKey.key_hash == key_hash)).first() if key_hash else None
    if api_key_obj:
        log.info(f"[auth] API key lookup: matched by hash in APIKey table (user={api_key_obj.user.username})")
        return api_key_obj.user

    api_key_obj = session.exec(select(APIKey).where(APIKey.key_value == key_value)).first()
    if api_key_obj:
        if not api_key_obj.key_hash:
            _store_generated_api_key(api_key_obj, key_value)
            session.add(api_key_obj)
            session.commit()
            session.refresh(api_key_obj)
        log.info(f"[auth] API key lookup: matched by value in APIKey table (user={api_key_obj.user.username})")
        return api_key_obj.user

    user = session.exec(select(User).where(User.api_key_hash == key_hash)).first() if key_hash else None
    if user:
        if not user.api_key_hash:
            _store_user_api_key(user, key_value)
            session.add(user)
            session.commit()
            session.refresh(user)
        log.info(f"[auth] API key lookup: matched by hash in User table (user={user.username})")
        return user

    user = session.exec(select(User).where(User.api_key == key_value)).first()
    if user:
        if not user.api_key_hash:
            _store_user_api_key(user, key_value)
            session.add(user)
            session.commit()
            session.refresh(user)
        log.info(f"[auth] API key lookup: matched by value in User table (user={user.username})")
        return user

    log.warning("[auth] API key lookup: no match for provided key")
    return None


def _migrate_api_key_material(session: Session) -> None:
    dirty = False
    for user in session.exec(select(User)).all():
        if user.api_key and not user.api_key_hash:
            _store_user_api_key(user, user.api_key)
            session.add(user)
            dirty = True
    for key in session.exec(select(APIKey)).all():
        if key.key_value and not key.key_hash:
            _store_generated_api_key(key, key.key_value)
            session.add(key)
            dirty = True
    if dirty:
        session.commit()

def _matches_internal_secret(val: str | None) -> bool:
    if not val:
        return False
    expected = os.getenv("INTERNAL_SECRET", INTERNAL_SECRET)
    return bool(
        (expected and secret_matches(val, expected))
        or (INTERNAL_SECRET and secret_matches(val, INTERNAL_SECRET))
    )

def require_internal(authorization: str = Header(None), x_internal_secret: str = Header(None, alias="X-Internal-Secret")):
    if _matches_internal_secret(x_internal_secret):
        return
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing internal token")
    token = authorization.split(" ")[1]
    if not _matches_internal_secret(token):
        raise HTTPException(status_code=403, detail="Invalid internal token")

def require_admin_or_internal(
    authorization: str = Header(None),
    x_internal_secret: str = Header(None, alias="X-Internal-Secret"),
    session: Session = Depends(get_session)
):
    # Trust internal services
    if _matches_internal_secret(x_internal_secret):
        return True

    # Trust bearer tokens matching internal secret
    if authorization and authorization.startswith("Bearer "):
        token = authorization.split(" ")[1]
        if _matches_internal_secret(token):
            return True

    # Check if user is admin via API key
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing authorization")

    token = authorization.split(" ")[1]
    user = _find_user_for_api_key(session, token)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid API key")
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    return True

@app.post("/api/users/{username}/password")
def admin_set_password(username: str, req: dict, request: Request, session: Session = Depends(get_session), admin: User = Depends(require_admin_or_internal)):
    new_password = req.get("new_password")
    if not new_password:
        raise HTTPException(status_code=400, detail="new_password is required")

    user = session.exec(select(User).where(User.username == username)).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    user.password_hash = hash_password(new_password)
    session.add(user)
    session.commit()
    _audit(request, session, "user.password_reset", user.username, changes=[{"field": "password", "change": "changed", "secret": True}])
    return {"status": "SUCCESS", "message": f"Password for @{username} updated"}

def require_api_key(authorization: str = Header(None), session: Session = Depends(get_session)) -> User:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing API Key")
    key = authorization.split(" ")[1]

    user = _find_user_for_api_key(session, key)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid API Key")
    return user


# Push topics, notification preferences, UnifiedPush (docs/PUSH.md)
app.include_router(push.build_router(get_session, require_api_key, require_internal))

# Internal API

@app.post("/api/resolve", response_model=ResolvedCredentials)
def resolve_identity(req: ResolveRequest, session: Session = Depends(get_session), _: None = Depends(require_internal)):
    """Downstream services call this to get decrypted credentials for a user.

    A request that names a user or presents a credential must resolve to that
    user. If it does not, this is a 401 -- it must never silently become the
    system default user, who is an administrator.

    A request with no identifier at all may ask for one of two explicit
    non-person identities:

    * ``anonymous`` -- no credentials at all, for an unauthenticated caller;
    * ``household`` -- the household's shared Home Assistant, media,
      audiobook, Nextcloud and Skylight credentials, with no administrator
      powers and no personal accounts. This is for kiosk and voice surfaces
      that need to control the house, not administer it.

    The one compatibility exception is a ``device_id`` that is genuinely
    assigned to more than one person: the device is real but cannot name a
    single speaker, so the household identity still answers.
    """
    if req.anonymous and req.household:
        raise HTTPException(
            status_code=400, detail="anonymous and household are mutually exclusive"
        )

    user = None
    log.debug(f"[resolve] Input: user_id={req.user_id}, api_key={'***' + req.api_key[-4:] if req.api_key and len(req.api_key) > 4 else req.api_key}, rag_user={req.rag_user}, voice_id={req.voice_id}, device_id={req.device_id}, anonymous={req.anonymous}, household={req.household}")

    # Resolve by user ID (integer primary key)
    if req.user_id is not None:
        user = session.exec(select(User).where(User.id == req.user_id)).first()
        if user:
            log.info(f"[resolve] Resolved by user_id={req.user_id}: user={user.username}")
        else:
            log.warning(f"[resolve] No user found for user_id={req.user_id}")

    # Resolve by API Key first (for OpenWebUI & UI clients)
    if not user and req.api_key is not None:
        user = _find_user_for_api_key(session, req.api_key)

    if not user and req.rag_user is not None:
        user = session.exec(select(User).where(User.username == req.rag_user.lower())).first()
        if user:
            log.info(f"[resolve] Resolved by rag_user={req.rag_user}: user={user.username}")
        else:
            log.warning(f"[resolve] No user found for rag_user={req.rag_user}")

    if not user and req.voice_id is not None:
        # Search for user by voice_id (username or biometric match)
        user = session.exec(select(User).where(User.username == req.voice_id.lower())).first()
        if user:
            log.info(f"[resolve] Resolved by voice_id={req.voice_id}: user={user.username}")
        else:
            log.warning(f"[resolve] No user found for voice_id={req.voice_id}")

    device_known = False
    if not user and req.device_id is not None:
        # A device names its user only when exactly one person (other than
        # the household account) uses it; a shared one says nothing about
        # who is speaking. A device that is not assigned at all is not a
        # credential and must not silently become the administrator.
        assignments = _assignments_for(session, req.device_id)
        active = [a for a in assignments if not a.revoked]
        device_known = bool(active)
        users = {a.user.username: a.user for a in active if a.user and not a.user.is_system_default}
        if len(users) == 1:
            user = next(iter(users.values()))
            log.info(f"[resolve] Resolved by device_id={req.device_id}: user={user.username}")
        elif users:
            log.info(f"[resolve] Device {req.device_id} is shared by {sorted(users)}, not resolving by device")
        elif active:
            log.info(f"[resolve] Device {req.device_id} names no single user, skipping")
        else:
            log.warning(f"[resolve] No active device assignment found for device_id={req.device_id}")

    household = False
    if not user:
        unmatched = (
            req.user_id is not None
            or req.api_key is not None
            or req.rag_user is not None
            or req.voice_id is not None
            or (req.device_id is not None and not device_known)
        )
        if unmatched:
            log.warning("[resolve] Presented identity did not match a user; refusing fallback")
            raise HTTPException(status_code=401, detail="Invalid credentials")

        if req.anonymous:
            return ResolvedCredentials(user="anonymous", is_admin=False)

        # A real device shared by more than one person cannot name a speaker;
        # answer as the household, never as the administrator. An explicit
        # household request selects the same identity. Anything else is a
        # caller that failed to say who it is: refuse rather than inherit
        # administrator powers.
        shared_device = req.device_id is not None and device_known
        if not (req.household or shared_device):
            log.warning("[resolve] No identity and no household request; refusing default admin")
            raise HTTPException(status_code=401, detail="Identity required")

        household = True
        user = _system_default_user(session)
        if user is None:
            log.error("[resolve] No system default user found in database!")
            raise HTTPException(status_code=404, detail="No valid identity found")
        log.info(
            f"[resolve] Resolved household credentials from system default: user={user.username}"
        )

    # Per-service credential policy: the user's own credentials, else the
    # system default user's shared ones when an admin granted that service.
    granted = set(_granted_services(session, user.username))
    if granted:
        log.info(f"[resolve] {user.username} has shared-credential grants for: {sorted(granted)}")

    service_creds: dict[str, dict[str, str | None]] = {}
    credential_sources: dict[str, str] = {}
    shared_owner: str | None = None
    for service in SHARED_CREDENTIAL_SERVICES:
        values, source, owner = _resolve_service_creds(session, user, service, granted)
        service_creds[service] = values
        credential_sources[service] = source
        if owner and shared_owner is None:
            shared_owner = owner

    ha = service_creds["home_assistant"]
    mass = service_creds["music_assistant"]
    abs_ = service_creds["audiobookshelf"]
    nextcloud = service_creds["nextcloud"]

    # Skylight is one shared system account, so every user logs in with the
    # configured system email, unless they configured an account of their own.
    if any((user.skylight_url, user.skylight_email, user.skylight_pass_enc)):
        skylight_user: User | None = user
        skylight_source = "own"
    else:
        default_owner = _system_default_user(session)
        skylight_user = default_owner
        skylight_source = "shared" if default_owner is not None and any(
            (default_owner.skylight_url, default_owner.skylight_email, default_owner.skylight_pass_enc)
        ) else "absent"
    credential_sources["skylight"] = skylight_source

    if household:
        # These are the system default's shared accounts, not the caller's own.
        credential_sources = {
            service: ("shared" if any(values.values()) else "absent")
            for service, values in service_creds.items()
        }
        credential_sources["skylight"] = (
            "shared" if skylight_source in ("own", "shared") else skylight_source
        )

    log.info(
        f"[resolve] Returning credentials for user={user.username}, "
        f"credential_sources={credential_sources}"
    )

    return ResolvedCredentials(
        user="household" if household else user.username,
        id=None if household else user.id,
        is_admin=False if household else user.is_admin,
        api_key=None if household else (decrypt(user.api_key_enc) if user.api_key_enc else req.api_key),
        nextcloud_url=nextcloud["nextcloud_url"],
        nextcloud_user=nextcloud["nextcloud_user"],
        nextcloud_pass=nextcloud["nextcloud_pass"],
        ha_url=ha["ha_url"],
        ha_token=ha["ha_token"],
        github_url=None if household else user.github_url,
        github_user=None if household else user.github_user,
        github_token=None if household else (
            decrypt(user.github_token_enc) if user.github_token_enc else None
        ),
        gitlab_url=None if household else user.gitlab_url,
        gitlab_user=None if household else user.gitlab_user,
        gitlab_token=None if household else (
            decrypt(user.gitlab_token_enc) if user.gitlab_token_enc else None
        ),
        audiobookshelf_url=abs_["audiobookshelf_url"],
        audiobookshelf_user=abs_["audiobookshelf_user"],
        audiobookshelf_pass=abs_["audiobookshelf_pass"],
        audiobookshelf_api_key=abs_["audiobookshelf_api_key"],
        mailcow_url=None if household else user.mailcow_url,
        mailcow_api_key=None if household else (
            decrypt(user.mailcow_api_key_enc) if user.mailcow_api_key_enc else None
        ),
        mail_user=None if household else user.mail_user,
        mail_pass=None if household else (
            decrypt(user.mail_pass_enc) if user.mail_pass_enc else None
        ),
        mass_url=mass["mass_url"],
        mass_token=mass["mass_token"],
        git_url=None if household else user.git_url,
        git_user=None if household else user.git_user,
        git_token=None if household else (
            decrypt(user.git_token_enc) if user.git_token_enc else None
        ),
        # Protected-branch patterns reach the workspace runtime through here
        # (services/workspace_runtime/main.py reads identity["forbidden_branches"]).
        forbidden_branches=[] if household else forbidden_branch_list(user.forbidden_branches),
        huggingface_token=None if household else (
            decrypt(user.huggingface_token_enc) if user.huggingface_token_enc else None
        ),
        skylight_url=skylight_user.skylight_url if skylight_user else None,
        skylight_email=(skylight_user.skylight_email if skylight_user else None) or user.username,
        skylight_pass=(
            decrypt(skylight_user.skylight_pass_enc)
            if skylight_user and skylight_user.skylight_pass_enc
            else None
        ),
        skylight_enabled=user.skylight_enabled,
        preferred_tts_voice=user.preferred_tts_voice or "af_heart",
        calendar_settings={} if household else _load_calendar_settings(session, user.username),
        credential_sources=credential_sources,
        shared_credential_owner=user.username if household else shared_owner,
    )


def _load_calendar_settings(session: Session, username: str) -> dict:
    row = session.exec(
        select(UserCalendarSetting).where(UserCalendarSetting.username == username)
    ).first()
    if row and row.data:
        try:
            return json.loads(row.data)
        except (ValueError, TypeError):
            return {}
    return {}


def normalize_abs_url(value: str | None) -> str | None:
    """The Audiobookshelf server root, however it was pasted.

    ABS logs in at ``{root}/login`` and serves its API at ``{root}/api/...``,
    so a URL saved with ``/api`` or ``/login`` on the end (or a trailing slash)
    breaks every call built from it. A path prefix such as
    ``https://host/audiobookshelf`` is a real deployment and is kept.
    """
    if not value:
        return value
    url = value.strip().rstrip("/")
    for suffix in ("/login", "/api"):
        if url.lower().endswith(suffix):
            url = url[: -len(suffix)].rstrip("/")
    return url or None


def _coerce_profile_value(key: str, value):
    """Normalize a profile update field without nulling valid falsy values.

    Booleans like False must stay False (NOT NULL columns). Only blank optional
    strings become None; display_name keeps "" so UserRead validation passes.
    """
    if isinstance(value, str):
        value = value.strip()
        if value == "" and key != "display_name":
            return None
        if key == "audiobookshelf_url":
            return normalize_abs_url(value)
        return value
    if key == "forbidden_branches" and isinstance(value, list):
        # A JSON array string on the User row (UserWidget.pinned_devices does
        # the same); an empty list is explicit and clears the patterns.
        return json.dumps([str(item).strip() for item in value if str(item).strip()])
    return value

START_TIME = time.time()

@app.get("/health")
def health():
    return {
        "status": "ok",
        "service": "identity",
        "git_sha": os.getenv("GIT_SHA", "unknown"),
        "start_time": START_TIME
    }

# Public/Admin API

@app.get("/api/users/me", response_model=UserRead)
def get_me(user: User = Depends(require_api_key), session: Session = Depends(get_session)):
    me = _user_read(session, user)
    me.skylight_status = skylight_status(session, user)
    return me


def _user_read(session: Session, user: User) -> UserRead:
    """A user as the API shows them, with their roles and what those allow."""
    read = UserRead.model_validate(user)
    read.family_role = user.family_role if user.family_role in roles.FAMILY_ROLES else "other"
    read.mother_username = user.mother_username or None
    read.is_minor = read.family_role in roles.MINOR_FAMILY_ROLES
    read.sharing_locked = read.is_minor
    read.role_ids = roles.user_role_ids(session, user)
    read.permissions = sorted(roles.user_permissions(session, user))
    return read


def skylight_status(session: Session, user: User) -> str:
    """Whether Skylight works for this user.

    Skylight is one household account kept on the system user (ID 1, the same
    account execution logs in with); each user can only switch it on or off
    for themselves. A normal user's own skylight_url is therefore always
    empty, which is why the account page showed everyone but the admin as
    "not linked".
    """
    system = session.get(User, 1)
    if not (system and system.skylight_email and system.skylight_pass_enc):
        return "not_set_up"
    return "connected" if user.skylight_enabled else "off"


# profile pictures

AVATAR_MAX_BYTES = 1_500_000
_AVATAR_PRESET = re.compile(r"^[a-z0-9-]{1,40}$")
_IMAGE_MAGIC = {
    b"\x89PNG\r\n\x1a\n": "image/png",
    b"\xff\xd8\xff": "image/jpeg",
    b"RIFF": "image/webp",
}


class AvatarChoice(BaseModel):
    preset: str


class AvatarUpload(BaseModel):
    #: A data URL ("data:image/png;base64,...") as a canvas or file reader makes it.
    data_url: str


def _image_type(data: bytes) -> str | None:
    for magic, mime in _IMAGE_MAGIC.items():
        if data.startswith(magic):
            if mime == "image/webp" and data[8:12] != b"WEBP":
                return None
            return mime
    return None


@app.put("/api/users/me/avatar", response_model=UserRead)
def choose_avatar(
    body: AvatarChoice,
    request: Request,
    user: User = Depends(require_api_key),
    session: Session = Depends(get_session),
):
    """Pick one of the built-in pictures. An uploaded photo is kept until the
    user removes it, so switching back to it later needs no re-upload."""
    if not _AVATAR_PRESET.match(body.preset):
        raise HTTPException(status_code=422, detail="Unknown picture.")
    user.avatar = f"preset:{body.preset}"
    session.add(user)
    session.commit()
    session.refresh(user)
    _audit(request, session, "user.avatar", user.username, actor=user, note=f"preset {body.preset}")
    return get_me(user, session)


@app.post("/api/users/me/avatar/image", response_model=UserRead)
def upload_avatar(
    body: AvatarUpload,
    request: Request,
    user: User = Depends(require_api_key),
    session: Session = Depends(get_session),
):
    """Keep an uploaded photo as the profile picture (PNG, JPEG or WebP)."""
    head, _, payload = body.data_url.partition(",")
    if not head.startswith("data:") or ";base64" not in head:
        raise HTTPException(status_code=422, detail="Send the picture as a base64 data URL.")
    try:
        data = base64.b64decode(payload, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise HTTPException(status_code=422, detail="That picture could not be read.") from exc
    if len(data) > AVATAR_MAX_BYTES:
        raise HTTPException(status_code=413, detail="That picture is too large; the limit is 1.5 MB.")
    mime = _image_type(data)
    if mime is None:
        raise HTTPException(status_code=415, detail="Use a PNG, JPEG or WebP picture.")
    row = session.get(UserAvatarImage, user.username) or UserAvatarImage(username=user.username, mime=mime, data=data)
    row.mime, row.data, row.updated_at = mime, data, datetime.now(UTC)
    session.add(row)
    user.avatar = f"upload:{int(time.time())}"
    session.add(user)
    session.commit()
    session.refresh(user)
    _audit(request, session, "user.avatar", user.username, actor=user, note=f"uploaded {mime}, {len(data)} bytes")
    return get_me(user, session)


@app.delete("/api/users/me/avatar", response_model=UserRead)
def remove_avatar(request: Request, user: User = Depends(require_api_key), session: Session = Depends(get_session)):
    """Back to initials; an uploaded photo is deleted, not just hidden."""
    row = session.get(UserAvatarImage, user.username)
    if row is not None:
        session.delete(row)
    user.avatar = None
    session.add(user)
    session.commit()
    session.refresh(user)
    _audit(request, session, "user.avatar", user.username, actor=user, note="removed")
    return get_me(user, session)


@app.get("/api/users/{username}/avatar/image")
def avatar_image(username: str, _viewer: User = Depends(require_api_key), session: Session = Depends(get_session)):
    """A family member's uploaded picture, for anyone signed in to the household."""
    row = session.get(UserAvatarImage, username)
    if row is None:
        raise HTTPException(status_code=404, detail="No uploaded picture.")
    return Response(content=row.data, media_type=row.mime, headers={"Cache-Control": "private, max-age=86400"})

async def _voice_signature_of(file: UploadFile) -> dict:
    """The upload's voice signature, computed by the execution service.

    The audio goes no further than this server: execution decodes it and runs
    the local model, and only the resulting vector comes back to be stored.
    """
    from services.config import EXECUTION_SVC_URL

    audio = await file.read()
    if not audio:
        raise HTTPException(status_code=400, detail="The recording is empty.")
    form = aiohttp.FormData()
    form.add_field("file", audio, filename=file.filename or "voice.webm",
                   content_type=file.content_type or "application/octet-stream")
    try:
        async with aiohttp.ClientSession() as client:
            resp = await client.post(
                f"{EXECUTION_SVC_URL}/voice/signature",
                data=form,
                headers={"X-Internal-Secret": INTERNAL_SECRET},
                timeout=aiohttp.ClientTimeout(total=120.0),
            )
            body = await resp.json(content_type=None)
    except (TimeoutError, aiohttp.ClientError, ValueError) as e:
        log.error(f"[voice] signature service unavailable: {type(e).__name__}: {e}")
        raise HTTPException(status_code=502, detail="The voice signature service is unavailable right now.") from None
    if resp.status == 422:
        raise HTTPException(status_code=422, detail=body.get("detail") or "That recording could not be used.")
    if resp.status != 200 or body.get("model") != voice.MODEL:
        log.error(f"[voice] signature service answered {resp.status}: {str(body)[:200]}")
        raise HTTPException(status_code=502, detail="The voice signature service gave an unusable answer.")
    try:
        body["embedding"] = voice.valid_embedding(body.get("embedding"))
    except ValueError as e:
        raise HTTPException(status_code=502, detail=f"The voice signature service gave an unusable answer: {e}") from None
    return body


def _voice_profiles(session: Session) -> list[dict]:
    names = {u.username: (u.display_name or u.username) for u in session.exec(select(User)).all()}
    return [
        {"username": row.username, "name": names[row.username], "samples": voice.load_samples(row.samples)}
        for row in session.exec(select(VoiceSignature).where(VoiceSignature.model == voice.MODEL)).all()
        if row.username in names
    ]


def _voice_status(row: VoiceSignature | None) -> dict:
    samples = voice.load_samples(row.samples) if row and row.model == voice.MODEL else []
    return {
        "enrolled": len(samples) >= voice.MIN_SAMPLES,
        "samples": len(samples),
        "min_samples": voice.MIN_SAMPLES,
        "max_samples": voice.MAX_SAMPLES,
        "consistency": voice.consistency(samples),
        "model": voice.MODEL,
        "updated_at": row.updated_at.isoformat() if row and samples else None,
    }


@app.get("/api/users/me/voice")
def my_voice(user: User = Depends(require_api_key), session: Session = Depends(get_session)):
    """How far the caller's voice enrollment has got."""
    return _voice_status(session.get(VoiceSignature, user.username))


@app.post("/api/users/me/enroll")
async def enroll_voice(
    request: Request,
    file: UploadFile = File(...),
    user: User = Depends(require_api_key),
    session: Session = Depends(get_session),
):
    """Add one recording to the caller's voice profile.

    A take that does not sound like the person's earlier takes is refused
    rather than averaged in: a profile polluted by someone else's voice (or a
    noisy room) would make every later identification less certain.
    """
    signature = await _voice_signature_of(file)
    row = session.get(VoiceSignature, user.username)
    samples = voice.load_samples(row.samples) if row and row.model == voice.MODEL else []
    if samples:
        score = voice.cosine(signature["embedding"], voice.centroid(samples))
        if score < voice.CONSISTENCY_FLOOR:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"That take does not sound like your earlier ones (similarity {score:.2f}). "
                    "Record again somewhere quiet, or start over if the earlier takes were not you."
                ),
            )
    samples.append({
        "embedding": signature["embedding"],
        "speech_seconds": signature.get("speech_seconds"),
        "recorded_at": datetime.now(UTC).isoformat(),
    })
    samples = samples[-voice.MAX_SAMPLES:]
    if row is None:
        row = VoiceSignature(username=user.username, model=voice.MODEL)
    row.model = voice.MODEL
    row.samples = json.dumps(samples)
    row.updated_at = datetime.now(UTC)
    session.add(row)
    user.voice_fingerprint = voice.fingerprint(samples) if len(samples) >= voice.MIN_SAMPLES else None
    session.add(user)
    session.commit()
    _audit(request, session, "user.voice_enroll", user.username, actor=user,
           changes=[{"field": "voice_samples", "change": str(len(samples))}])
    log.info(f"[voice] {user.username} enrolled sample {len(samples)} ({signature.get('speech_seconds')}s of speech)")
    return {"status": "SUCCESS", "speech_seconds": signature.get("speech_seconds"), **_voice_status(row)}


@app.delete("/api/users/me/voice")
def forget_my_voice(request: Request, user: User = Depends(require_api_key), session: Session = Depends(get_session)):
    """Erase the caller's voice profile entirely."""
    row = session.get(VoiceSignature, user.username)
    if row is not None:
        session.delete(row)
    user.voice_fingerprint = None
    session.add(user)
    session.commit()
    _audit(request, session, "user.voice_forget", user.username, actor=user,
           changes=[{"field": "voice_samples", "change": "cleared"}])
    return {"status": "SUCCESS", **_voice_status(None)}


@app.post("/api/users/me/voice/check")
async def check_my_voice(
    file: UploadFile = File(...),
    user: User = Depends(require_api_key),
    session: Session = Depends(get_session),
):
    """Say something and see who the household thinks is talking.

    Scored against everyone enrolled, exactly as identification does, so a
    pass here means the caller would be recognised rather than merely that
    they resemble their own profile.
    """
    profiles = _voice_profiles(session)
    if not any(p["username"] == user.username and len(p["samples"]) >= voice.MIN_SAMPLES for p in profiles):
        raise HTTPException(status_code=409, detail=f"Record at least {voice.MIN_SAMPLES} samples before testing.")
    signature = await _voice_signature_of(file)
    result = voice.decide(voice.rank(signature["embedding"], profiles))
    return {**result, "is_me": result["matched_username"] == user.username}


class VoiceIdentifyRequest(BaseModel):
    embedding: list[float]
    model: str = voice.MODEL
    threshold: float | None = None


class VoiceLearnRequest(BaseModel):
    username: str
    embedding: list[float]
    speech_seconds: float | None = None
    model: str = voice.MODEL


@app.post("/api/voice/learn", dependencies=[Depends(require_internal)])
def learn_voice(request: Request, req: VoiceLearnRequest, session: Session = Depends(get_session)):
    """Add a confidently recognised command to an enrolled profile (internal).

    The speech-to-text proxy calls this only while that person has an open
    learning session. A take is still checked against the profile they already
    have, so a misheard command or a television in the background is refused
    rather than averaged in.
    """
    if req.model != voice.MODEL:
        raise HTTPException(status_code=422, detail=f"Signatures are {voice.MODEL}, not {req.model}.")
    try:
        embedding = voice.valid_embedding(req.embedding)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from None
    row = session.get(VoiceSignature, req.username)
    samples = voice.load_samples(row.samples) if row and row.model == voice.MODEL else []
    if len(samples) < voice.MIN_SAMPLES:
        raise HTTPException(
            status_code=409,
            detail="That voice does not have an enrolled profile yet; record the first samples in Settings.",
        )
    score = voice.cosine(embedding, voice.centroid(samples))
    if score < voice.CONSISTENCY_FLOOR:
        raise HTTPException(
            status_code=422,
            detail=f"That take does not sound like the enrolled voice (similarity {score:.2f}).",
        )
    if max(voice.cosine(embedding, s["embedding"]) for s in samples) >= voice.DUPLICATE_SAMPLE:
        return {"status": "SKIPPED", "reason": "already known", **_voice_status(row)}
    samples.append({
        "embedding": embedding,
        "speech_seconds": req.speech_seconds,
        "recorded_at": datetime.now(UTC).isoformat(),
        "source": "learned",
    })
    samples = samples[-voice.MAX_SAMPLES:]
    row.model = voice.MODEL
    row.samples = json.dumps(samples)
    row.updated_at = datetime.now(UTC)
    session.add(row)
    user = session.exec(select(User).where(User.username == req.username)).first()
    if user is not None:
        user.voice_fingerprint = voice.fingerprint(samples) if len(samples) >= voice.MIN_SAMPLES else None
        session.add(user)
    session.commit()
    _audit(request, session, "user.voice_learn", req.username, note=f"learned sample {len(samples)}")
    log.info(f"[voice] learned sample {len(samples)} for {req.username} ({req.speech_seconds}s of speech)")
    return {"status": "STORED", "speech_seconds": req.speech_seconds, **_voice_status(row)}


@app.post("/api/voice/identify", dependencies=[Depends(require_internal)])
def identify_voice(req: VoiceIdentifyRequest, session: Session = Depends(get_session)):
    """Which enrolled family member a signature belongs to (internal)."""
    if req.model != voice.MODEL:
        raise HTTPException(status_code=422, detail=f"Signatures are {voice.MODEL}, not {req.model}.")
    try:
        embedding = voice.valid_embedding(req.embedding)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from None
    if req.threshold is not None and not 0.0 < req.threshold <= 1.0:
        raise HTTPException(status_code=422, detail="threshold must be in (0, 1].")
    candidates = voice.rank(embedding, _voice_profiles(session))
    if not candidates:
        raise HTTPException(status_code=404, detail="Nobody has enrolled a voice yet.")
    return voice.decide(candidates, req.threshold)


@app.get("/api/voice/profiles", dependencies=[Depends(require_internal)])
def voice_profiles(session: Session = Depends(get_session)):
    """Who can be recognised by voice (internal; no signatures included)."""
    return {
        "profiles": [
            {"username": p["username"], "name": p["name"], "samples": len(p["samples"])}
            for p in _voice_profiles(session)
            if len(p["samples"]) >= voice.MIN_SAMPLES
        ]
    }


def _client_of(request: Request | None) -> tuple[str | None, str | None]:
    if request is None:
        return None, None
    forwarded = request.headers.get("X-Forwarded-For") or request.headers.get("X-Real-IP") or ""
    client = forwarded.split(",")[0].strip() or (request.client.host if request.client else None)
    return client, request.headers.get("User-Agent")


def _actor_of(request: Request | None, session: Session, fallback: User | None = None) -> tuple[str, str]:
    """Who is acting: the API key's user, an internal service, or nobody."""
    if fallback is not None:
        return fallback.username, "user"
    if request is None:
        return "internal", "internal"
    if _matches_internal_secret(request.headers.get("X-Internal-Secret")):
        return "internal", "internal"
    auth = request.headers.get("Authorization") or ""
    if auth.startswith("Bearer "):
        found = _find_user_for_api_key(session, auth.split(" ", 1)[1])
        if found:
            return found.username, "user"
    return "anonymous", "anonymous"


def _audit(
    request: Request | None,
    session: Session,
    action: str,
    target: str | None,
    *,
    actor: User | None = None,
    before: dict | None = None,
    after: dict | None = None,
    changes: list[dict] | None = None,
    note: str | None = None,
) -> None:
    """Record an account action. Never raises: losing an audit line must not
    undo the change it describes (the log line still records the failure)."""
    try:
        who, kind = _actor_of(request, session, actor)
        client, agent = _client_of(request)
        audit.record(
            session,
            actor=who,
            actor_kind=kind,
            action=action,
            target=target,
            changes=changes if changes is not None else audit.diff(before or {}, after or {}),
            source=f"{request.method} {request.url.path}" if request is not None else None,
            client=client,
            user_agent=agent,
            note=note,
        )
    except Exception as exc:  # pragma: no cover - defensive
        log.error(f"[audit] could not record {action} on {target}: {exc}")


# Granted only by an admin (PATCH /api/users/{username}), never self-assigned.
PRIVILEGE_FIELDS = ("is_admin", "is_system_default")


def _apply_user_update(user: User, update_data: dict, clear_fields: list[str] | None) -> None:
    """Apply a profile/credential update with the rules both PATCH routes share.

    A blank value means "leave it alone", never "erase it": every form loads
    secrets blank (they are never sent to the browser), so treating blank as
    erase wiped a saved password whenever anyone saved the form for any other
    reason. Erasing is explicit -- the field is named in ``clear_fields``.
    """
    for field in clear_fields or []:
        if field in CREDENTIAL_COLUMNS:
            setattr(user, CREDENTIAL_COLUMNS[field], None)
        elif field in User.model_fields and field not in ("id", "username", "password_hash", *PRIVILEGE_FIELDS):
            setattr(user, field, None)

    for plain, enc in CREDENTIAL_COLUMNS.items():
        if plain in update_data:
            val = update_data.pop(plain)
            val = val.strip() if isinstance(val, str) else val
            if val:
                setattr(user, enc, encrypt(val))

    for key, value in update_data.items():
        value = _coerce_profile_value(key, value)
        if value is None and key != "display_name":
            continue  # blank: unchanged (use clear_fields to erase)
        setattr(user, key, value)


def _redacted(update_data: dict) -> list[str]:
    """Field names only, for logging what a request touched."""
    return sorted(update_data)


@app.patch("/api/users/me", response_model=UserRead)
def update_me(body: UserUpdate, request: Request, session: Session = Depends(get_session), user: User = Depends(require_api_key)):
    update_data = body.model_dump(exclude_unset=True)
    clear_fields = update_data.pop("clear_fields", None)
    # Field names only: this line used to print the whole body, passwords included.
    log.info(f"[update_me] {user.username} updating {_redacted(update_data)} clearing {clear_fields or []}")

    # Privilege fields are never self-assignable here; admins use
    # PATCH /api/users/{username} which enforces is_admin.
    for privilege_field in PRIVILEGE_FIELDS:
        if privilege_field in update_data:
            log.warning(f"[update_me] {user.username} attempted self-update of '{privilege_field}'; ignored")
            del update_data[privilege_field]

    # Prevent non-default users from changing system skylight integration credentials
    if any(k in update_data for k in ["skylight_url", "skylight_email", "skylight_pass"]) and user.id != 1 and user.username != "default":
        raise HTTPException(status_code=403, detail="Only the default system user (User 1) can configure Skylight system integration.")

    before = audit.snapshot(user)
    _apply_user_update(user, update_data, clear_fields)
    session.add(user)
    session.commit()
    session.refresh(user)
    _audit(request, session, "user.self_update", user.username, actor=user, before=before, after=audit.snapshot(user))
    return user

@app.patch("/api/users/{username}", response_model=UserRead)
def update_user(username: str, body: UserUpdate, request: Request, session: Session = Depends(get_session), admin: User = Depends(require_api_key)):
    if not admin.is_admin:
        raise HTTPException(status_code=403, detail="Admin only")

    user = session.exec(select(User).where(User.username == username.lower())).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    update_data = body.model_dump(exclude_unset=True)
    clear_fields = update_data.pop("clear_fields", None)

    # Prevent non-default users from changing system skylight integration credentials
    if any(k in update_data for k in ["skylight_url", "skylight_email", "skylight_pass"]) and user.id != 1 and user.username != "default":
        raise HTTPException(status_code=403, detail="Only the default system user (User 1) can configure Skylight system integration.")

    before = audit.snapshot(user)
    _apply_user_update(user, update_data, clear_fields)
    session.add(user)
    session.commit()
    session.refresh(user)
    _audit(request, session, "user.update", user.username, actor=admin, before=before, after=audit.snapshot(user))
    return user


@app.post("/api/users/{username}/reveal")
def reveal_credential(username: str, body: dict, request: Request, session: Session = Depends(get_session), caller: User = Depends(require_api_key)):
    """A saved credential's value, for the show/hide eye on a password field.

    Only the account's owner or an admin may ask, one field at a time, and
    every reveal is audited (the value never is).
    """
    target = session.exec(select(User).where(User.username == username.lower())).first()
    if not target:
        raise HTTPException(status_code=404, detail="User not found")
    if not caller.is_admin and caller.username != target.username:
        raise HTTPException(status_code=403, detail="Only the account owner or an admin can reveal a credential")
    field = str(body.get("field") or "")
    if field not in CREDENTIAL_COLUMNS:
        raise HTTPException(status_code=422, detail=f"field must be one of {', '.join(sorted(CREDENTIAL_COLUMNS))}")
    stored = getattr(target, CREDENTIAL_COLUMNS[field], None)
    value = decrypt(stored) if stored else ""
    _audit(request, session, "credential.reveal", target.username, actor=caller, changes=[{"field": field, "change": "revealed", "secret": True}])
    return {"field": field, "value": value or ""}


@app.get("/api/admin/audit")
def list_audit(
    target: str | None = None,
    actor: str | None = None,
    action: str | None = None,
    limit: int = Query(200, ge=1, le=1000),
    session: Session = Depends(get_session),
    caller: User = Depends(require_api_key),
):
    """The account audit trail, newest first. Admin only."""
    if not caller.is_admin:
        raise HTTPException(status_code=403, detail="Admin only")
    query = select(AuditEvent)
    if target:
        query = query.where(AuditEvent.target == target.lower())
    if actor:
        query = query.where(AuditEvent.actor == actor.lower())
    if action:
        query = query.where(AuditEvent.action == action)
    rows = session.exec(query.order_by(AuditEvent.id.desc()).limit(limit)).all()
    return {"events": [audit.as_dict(r) for r in rows]}


@app.get("/api/users/me/audit")
def my_audit(limit: int = Query(100, ge=1, le=500), session: Session = Depends(get_session), user: User = Depends(require_api_key)):
    """What has been done to your account, and by whom."""
    rows = session.exec(
        select(AuditEvent).where(AuditEvent.target == user.username).order_by(AuditEvent.id.desc()).limit(limit)
    ).all()
    return {"events": [audit.as_dict(r) for r in rows]}

@app.get("/api/users/{username}/credential-shares")
def get_credential_shares(
    username: str, session: Session = Depends(get_session), caller: User = Depends(require_api_key)
):
    """Which shared (User 1) services this user may borrow. Self or admin."""
    target = session.exec(select(User).where(User.username == username.lower())).first()
    if not target:
        raise HTTPException(status_code=404, detail="User not found")
    if not caller.is_admin and caller.username != target.username:
        raise HTTPException(status_code=403, detail="Admin only")

    row = session.exec(
        select(UserCredentialShare).where(UserCredentialShare.username == target.username)
    ).first()
    shared_owner = _system_default_user(session)
    return CredentialSharesRead(
        username=target.username,
        services=_granted_services(session, target.username),
        granted_by=row.granted_by if row else None,
        granted_at=row.granted_at if row else None,
        note=row.note if row else None,
        shared_owner=shared_owner.username if shared_owner is not None else None,
    )


@app.put("/api/users/{username}/credential-shares")
def update_credential_shares(
    username: str,
    body: CredentialSharesUpdate,
    request: Request,
    session: Session = Depends(get_session),
    caller: User = Depends(require_api_key),
):
    """Grant (or revoke) a user's permission to use the shared credentials.

    Admin only. An admin may opt itself in, or grant another user; an empty
    ``services`` list revokes every grant for that user.
    """
    if not caller.is_admin:
        raise HTTPException(
            status_code=403,
            detail="Only an admin can grant permission to use the shared credentials",
        )

    target = session.exec(select(User).where(User.username == username.lower())).first()
    if not target:
        raise HTTPException(status_code=404, detail="User not found")

    requested = {str(s).strip().lower() for s in body.services if str(s).strip()}
    unknown = sorted(requested - set(SHARED_CREDENTIAL_SERVICES))
    if unknown:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Unknown service(s): {', '.join(unknown)}. "
                f"Valid services: {', '.join(SHARED_CREDENTIAL_SERVICES)}"
            ),
        )

    services = sorted(requested)
    existing = session.exec(
        select(UserCredentialShare).where(UserCredentialShare.username == target.username)
    ).first()

    if not services:
        if existing:
            session.delete(existing)
            session.commit()
            log.info(
                f"[credentials] {caller.username} revoked {target.username}'s shared-credential grants"
            )
    else:
        note = body.note.strip() if isinstance(body.note, str) and body.note.strip() else None
        if existing:
            existing.services = json.dumps(services)
            existing.granted_by = caller.username
            existing.granted_at = datetime.now(UTC).isoformat()
            existing.note = note
            row = existing
        else:
            row = UserCredentialShare(
                username=target.username,
                services=json.dumps(services),
                granted_by=caller.username,
                granted_at=datetime.now(UTC).isoformat(),
                note=note,
            )
        session.add(row)
        session.commit()
        log.info(
            f"[credentials] {caller.username} granted {target.username} permission to use "
            f"the shared {', '.join(services)} credentials"
        )

    _audit(request, session, "credential.share", target.username, actor=caller,
           changes=[{"field": "shared_services", "change": "set" if services else "cleared", "to": services}])
    shared_owner = _system_default_user(session)
    return CredentialSharesRead(
        username=target.username,
        services=_granted_services(session, target.username),
        granted_by=caller.username if services else None,
        granted_at=row.granted_at if (services and row) else None,
        note=body.note if services else None,
        shared_owner=shared_owner.username if shared_owner is not None else None,
    )


@app.delete("/api/users/{username}")
def delete_user(username: str, request: Request, session: Session = Depends(get_session), admin: User = Depends(require_api_key)):
    if not admin.is_admin:
        raise HTTPException(status_code=403, detail="Admin only")

    user = session.exec(select(User).where(User.username == username.lower())).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if user.is_system_default:
        raise HTTPException(status_code=400, detail="Cannot delete system default user")

    # Per-user telemetry state lives in Redis (report jobs, reports,
    # notifications, push subscriptions). Remove it so a deleted user leaves
    # nothing behind that could still surface their data.
    try:
        import redis as _redis_sync

        from services.config import REDIS_URL as _REDIS_URL

        r = _redis_sync.from_url(_REDIS_URL, decode_responses=True)
        try:
            job_ids = r.smembers(f"tel:jobs:user:{username.lower()}") or set()
            for job_id in job_ids:
                r.delete(f"tel:job:{job_id}")
            r.delete(
                f"tel:jobs:user:{username.lower()}",
                f"tel:reports:user:{username.lower()}",
                f"tel:notify:{username.lower()}",
                f"tel:push:sub:{username.lower()}",
            )
        finally:
            r.close()
    except Exception as e:  # cleanup is best-effort; deletion must still succeed
        log.warning(f"Telemetry cleanup for deleted user {username} failed: {e}")

    # SQLite can hand a deleted user's id to the next new one, which would
    # then inherit these.
    for row in session.exec(select(UserRole).where(UserRole.user_id == (user.id or 0))).all():
        session.delete(row)
    for row in session.exec(select(DeviceAssignment).where(DeviceAssignment.user_id == (user.id or 0))).all():
        session.delete(row)
    session.delete(user)
    session.commit()

    # A re-created user with the same username must not inherit the old
    # shared-credential grants.
    stale_grant = session.exec(
        select(UserCredentialShare).where(UserCredentialShare.username == user.username)
    ).first()
    if stale_grant:
        session.delete(stale_grant)
        session.commit()
    stale_voice = session.get(VoiceSignature, user.username)
    if stale_voice:
        session.delete(stale_voice)
        session.commit()
    push.forget_user(session, user.username)
    _audit(request, session, "user.delete", username.lower(), actor=admin,
           changes=[{"field": "account", "change": "cleared"}])
    return {"status": "SUCCESS"}

@app.get("/api/users", response_model=list[UserRead])
def list_users(session: Session = Depends(get_session), _: bool = Depends(require_admin_or_internal)):
    return [_user_read(session, u) for u in session.exec(select(User)).all()]


# --- Roles (services/identity/roles.py) ---------------------------------------
# Only admins manage roles and who holds them: a role can grant control of
# every device in the house.

def _require_admin(user: User = Depends(require_api_key)) -> User:
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Admin only")
    return user


def _role_read(session: Session, role: Role) -> RoleRead:
    members = session.exec(
        select(User.username).join(UserRole, UserRole.user_id == User.id).where(UserRole.role_id == role.id)
    ).all()
    return RoleRead(
        id=role.id or 0,
        name=role.name,
        description=role.description,
        permissions=roles.role_permissions(role),
        builtin=role.builtin is not None,
        builtin_key=role.builtin,
        members=sorted(members),
    )


def _write_role(session: Session, role: Role, body: RoleWrite) -> None:
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="A role needs a name")
    clash = session.exec(select(Role).where(Role.name == name, Role.id != role.id)).first()
    if clash:
        raise HTTPException(status_code=409, detail=f"There is already a role called {name}")
    try:
        role.permissions = json.dumps(roles.clean_permissions(body.permissions))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    role.name = name
    role.description = body.description.strip()
    session.add(role)
    session.commit()
    session.refresh(role)


@app.get("/api/users/roles")
def list_roles(session: Session = Depends(get_session), _: User = Depends(_require_admin)):
    """Every role, and the permissions a role can be given."""
    return {
        "permissions": [{"key": k, "description": d} for k, d in roles.PERMISSIONS.items()],
        "roles": [_role_read(session, r) for r in session.exec(select(Role).order_by(Role.name)).all()],
    }


@app.post("/api/users/roles", response_model=RoleRead)
def create_role(body: RoleWrite, request: Request, session: Session = Depends(get_session), admin: User = Depends(_require_admin)):
    role = Role(name="")
    _write_role(session, role, body)
    _audit(request, session, "role.create", role.name, actor=admin, before={}, after={"permissions": role.permissions})
    return _role_read(session, role)


@app.patch("/api/users/roles/{role_id}", response_model=RoleRead)
def update_role(role_id: int, body: RoleWrite, request: Request, session: Session = Depends(get_session), admin: User = Depends(_require_admin)):
    role = session.get(Role, role_id)
    if not role:
        raise HTTPException(status_code=404, detail="Role not found")
    before = {"name": role.name, "permissions": role.permissions}
    _write_role(session, role, body)
    _audit(request, session, "role.update", role.name, actor=admin, before=before,
           after={"name": role.name, "permissions": role.permissions})
    return _role_read(session, role)


@app.delete("/api/users/roles/{role_id}")
def delete_role(role_id: int, request: Request, session: Session = Depends(get_session), admin: User = Depends(_require_admin)):
    role = session.get(Role, role_id)
    if not role:
        raise HTTPException(status_code=404, detail="Role not found")
    if role.builtin:
        raise HTTPException(status_code=409, detail="A built-in role can be changed but not deleted")
    for row in session.exec(select(UserRole).where(UserRole.role_id == role_id)).all():
        session.delete(row)
    session.delete(role)
    session.commit()
    _audit(request, session, "role.delete", role.name, actor=admin,
           changes=[{"field": "role", "change": "cleared"}])
    return {"status": "SUCCESS"}


@app.put("/api/users/{username}/roles", response_model=UserRead)
def set_user_roles(username: str, body: UserRolesUpdate, request: Request, session: Session = Depends(get_session), admin: User = Depends(_require_admin)):
    """Replace the roles a user holds."""
    user = session.exec(select(User).where(User.username == username.lower())).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    wanted = set(body.role_ids)
    known = set(session.exec(select(Role.id).where(Role.id.in_(wanted))).all()) if wanted else set()  # type: ignore[union-attr]
    if wanted - known:
        raise HTTPException(status_code=404, detail="Role not found")
    before = roles.user_role_ids(session, user)
    for row in session.exec(select(UserRole).where(UserRole.user_id == (user.id or 0))).all():
        if row.role_id not in wanted:
            session.delete(row)
    for role_id in wanted - set(before):
        session.add(UserRole(user_id=user.id or 0, role_id=role_id))
    # A person's family built-in role mirrors their relationship; re-assert it
    # so a role edit cannot silently strip a Mother/Teen of the household role.
    roles.sync_family_role(session, user)
    session.commit()
    _audit(request, session, "user.roles", user.username, actor=admin,
           before={"role_ids": before}, after={"role_ids": sorted(wanted)})
    return _user_read(session, user)


@app.get("/api/users/service-defaults")
def service_defaults(session: Session = Depends(get_session), _caller: User = Depends(require_api_key)):
    """The household's service URLs, to prefill login forms.

    They come from the system config (.env, see seed.system_service_urls),
    falling back to the system default user's for any the config leaves blank.
    Addresses only, no credentials; any signed-in user may read them, and
    ``source`` says where each came from so the form can warn about changing it.
    """
    config = {k: (v or "").strip() or None for k, v in system_service_urls().items()}
    owner = _system_default_user(session)
    urls: dict[str, str | None] = {}
    source: dict[str, str] = {}
    for field, value in config.items():
        if value:
            urls[field], source[field] = value, "system_config"
        elif owner is not None and getattr(owner, field, None):
            urls[field], source[field] = getattr(owner, field), "default_user"
        else:
            urls[field] = None
    return {**urls, "source": source}


@app.get("/api/users/family-directory")
def family_directory(session: Session = Depends(get_session), _caller: User = Depends(require_api_key)):
    """Everyone in the household, as chat draws them: name, profile picture,
    and the Nextcloud account their Talk messages arrive from.

    Profile pictures and names are meant to be seen by the family; nothing
    else about an account (URLs, credentials, keys) is included.
    """
    return [
        {
            "username": u.username,
            "display_name": u.display_name or u.username,
            "nextcloud_user": u.nextcloud_user or None,
            "avatar": u.avatar or None,
        }
        for u in session.exec(select(User)).all()
    ]


@app.get("/api/users/sharing-recipients", response_model=list[ShareRecipient])
def list_sharing_recipients(
    session: Session = Depends(get_session),
    caller: User = Depends(require_api_key),
):
    """List the accounts an activity-sharing grant may name.

    ``GET /api/users`` is admin-only, which left the sharing picker rendering
    zero people for every non-admin -- so the only choice available to a normal
    user was "Everyone", the opposite of the consent model we want.

    Any authenticated caller may read this: naming someone in a grant requires
    knowing they exist, and the response is deliberately just username +
    display name. It carries no integration URLs, no credential fields, no
    voice fingerprint and no API key, none of which ``UserRead`` omits.

    The caller is excluded: you cannot grant yourself anything.
    """
    people = session.exec(select(User)).all()
    return [
        ShareRecipient(username=u.username, display_name=u.display_name or u.username)
        for u in people
        if u.username != (caller.username or "").lower()
    ]

# --- family roles and relationships ---------------------------------------

_FAMILY_ORDER = {"father": 0, "mother": 1, "adult_child": 2, "teen": 3, "child": 4, "other": 5}


def _family_read(user: User) -> FamilyMemberRead:
    role = user.family_role if user.family_role in roles.FAMILY_ROLES else "other"
    return FamilyMemberRead(
        username=user.username,
        display_name=user.display_name or user.username,
        family_role=role,  # type: ignore[arg-type]
        mother_username=user.mother_username or None,
        is_minor=role in roles.MINOR_FAMILY_ROLES,
        sharing_locked=role in roles.MINOR_FAMILY_ROLES,
        is_admin=bool(user.is_admin),
        avatar=user.avatar or None,
    )


def _require_family_manager(user: User, session: Session) -> None:
    if not (user.is_admin or roles.has_permission(session, user, "family.manage")):
        raise HTTPException(status_code=403, detail="Only a parent or household manager can change family roles")


def _check_mother(session: Session, target_username: str, mother_username: str) -> User:
    username = mother_username.strip().lower()
    if not username:
        raise HTTPException(status_code=422, detail="mother_username cannot be blank")
    if username == target_username.strip().lower():
        raise HTTPException(status_code=422, detail="A person cannot be their own mother")
    mother = session.exec(select(User).where(User.username == username)).first()
    if mother is None:
        raise HTTPException(status_code=422, detail="That mother account does not exist")
    if mother.family_role != "mother":
        raise HTTPException(status_code=422, detail="That account is not marked as a mother")
    return mother


def _force_minor_activity_sharing(session: Session, user: User) -> None:
    """Non-adult children cannot switch circle sharing off.

    This is separate from health/cycle sharing: it is the household safety
    rule that a minor's totals stay visible to the family circle. The row is
    written, not just projected, so the geo feed sees the same policy.
    """
    row = session.exec(
        select(UserActivitySharing).where(UserActivitySharing.username == user.username)
    ).first()
    if row is None:
        row = UserActivitySharing(username=user.username, data="{}")
        session.add(row)
    data = _activity_sharing_data(row)
    data["enabled"] = True
    data["audience"] = "circle"
    data["user_ids"] = []
    if not data["share"]:
        data["share"] = ["totals"]
    row.data = json.dumps(data)


@app.get("/api/users/family", response_model=list[FamilyMemberRead])
def list_family(
    session: Session = Depends(get_session),
    caller: User = Depends(require_api_key),
):
    """The household roster with relationship roles, for a parent/manager.

    It deliberately does not expose integration URLs, credentials, keys or the
    raw admin flag beyond what a family panel needs.
    """
    _require_family_manager(caller, session)
    people = session.exec(select(User)).all()
    people.sort(key=lambda u: (_FAMILY_ORDER.get(u.family_role or "other", 9), (u.display_name or u.username).lower()))
    return [_family_read(u) for u in people]


@app.patch("/api/users/{username}/family", response_model=FamilyMemberRead)
def update_family(
    username: str,
    body: FamilyUpdate,
    request: Request,
    session: Session = Depends(get_session),
    caller: User = Depends(require_api_key),
):
    """Set a person's family role and (for a child/teen) their mother link.

    Only an admin or a household manager may call this. A mother cannot edit
    another parent or an admin, and cannot promote anyone to father/mother:
    that would be a privilege change, not a relationship edit.
    """
    _require_family_manager(caller, session)
    target = session.exec(select(User).where(User.username == username.strip().lower())).first()
    if target is None:
        raise HTTPException(status_code=404, detail="User not found")

    if not caller.is_admin:
        if target.is_admin or target.family_role in ("father", "mother"):
            raise HTTPException(status_code=403, detail="Only an admin can change a parent account")
        if target.username == caller.username:
            raise HTTPException(status_code=403, detail="You cannot change your own family role")
        if body.family_role in ("father", "mother"):
            raise HTTPException(status_code=403, detail="Only an admin can make someone a parent")

    next_role = body.family_role if body.family_role is not None else (target.family_role or "other")
    if next_role == "father" and not target.is_admin:
        raise HTTPException(status_code=422, detail="Make this account an admin before marking it Father")

    mother_changed = "mother_username" in body.model_fields_set or body.clear_mother
    if mother_changed:
        if body.clear_mother or not (body.mother_username or "").strip():
            target.mother_username = None
        else:
            if next_role not in roles.MINOR_FAMILY_ROLES:
                raise HTTPException(status_code=422, detail="A mother link only applies to a child or teen")
            target.mother_username = _check_mother(session, target.username, body.mother_username or "").username

    if body.family_role is not None:
        target.family_role = next_role
        if target.family_role not in roles.MINOR_FAMILY_ROLES:
            target.mother_username = None

    if target.family_role in roles.MINOR_FAMILY_ROLES:
        _force_minor_activity_sharing(session, target)

    roles.sync_family_role(session, target)
    session.add(target)
    session.commit()
    session.refresh(target)
    _audit(
        request,
        session,
        "user.family",
        target.username,
        actor=caller,
        changes=[
            {"field": "family_role", "change": "set", "value": target.family_role},
            {"field": "mother_username", "change": "set", "value": target.mother_username},
        ],
    )
    return _family_read(target)


@app.post("/api/users", response_model=UserRead)
def create_user(body: UserCreate, request: Request, session: Session = Depends(get_session), admin: User = Depends(require_api_key)):
    if not admin.is_admin:
        raise HTTPException(status_code=403, detail="Admin only")

    def _coerce(val):
        if isinstance(val, str):
            val = val.strip()
        return val if val else None

    if any(_coerce(k) for k in [body.skylight_url, body.skylight_email, body.skylight_pass]) and admin.id != 1 and admin.username != "default":
        raise HTTPException(status_code=403, detail="Only the default system user (User 1) can configure Skylight system integration.")

    if body.family_role == "father" and not body.is_admin:
        raise HTTPException(status_code=422, detail="Make this account an admin before marking it Father")
    if body.mother_username and body.family_role not in roles.MINOR_FAMILY_ROLES:
        raise HTTPException(status_code=422, detail="A mother link only applies to a child or teen")
    if body.mother_username:
        _check_mother(session, body.username.lower(), body.mother_username)

    user = User(
        username=body.username.lower(),
        display_name=body.display_name,
        is_admin=body.is_admin,
        is_system_default=body.is_system_default,
        family_role=body.family_role,
        mother_username=(body.mother_username or "").strip().lower() or None,
        password_hash=hash_password(body.password) if body.password else None,
        nextcloud_url=_coerce(body.nextcloud_url),
        nextcloud_user=_coerce(body.nextcloud_user),
        nextcloud_pass_enc=encrypt(_coerce(body.nextcloud_pass)) if _coerce(body.nextcloud_pass) else None,
        ha_url=_coerce(body.ha_url),
        ha_token_enc=encrypt(_coerce(body.ha_token)) if _coerce(body.ha_token) else None,
        github_url=_coerce(body.github_url),
        github_user=_coerce(body.github_user),
        github_token_enc=encrypt(_coerce(body.github_token)) if _coerce(body.github_token) else None,
        gitlab_url=_coerce(body.gitlab_url),
        gitlab_user=_coerce(body.gitlab_user),
        gitlab_token_enc=encrypt(_coerce(body.gitlab_token)) if _coerce(body.gitlab_token) else None,
        audiobookshelf_url=normalize_abs_url(_coerce(body.audiobookshelf_url)),
        audiobookshelf_user=_coerce(body.audiobookshelf_user),
        audiobookshelf_pass_enc=encrypt(_coerce(body.audiobookshelf_pass)) if _coerce(body.audiobookshelf_pass) else None,
        audiobookshelf_api_key_enc=encrypt(_coerce(body.audiobookshelf_api_key)) if _coerce(body.audiobookshelf_api_key) else None,
        mailcow_url=_coerce(body.mailcow_url),
        mailcow_api_key_enc=encrypt(_coerce(body.mailcow_api_key)) if _coerce(body.mailcow_api_key) else None,
        mail_user=_coerce(body.mail_user),
        mail_pass_enc=encrypt(_coerce(body.mail_pass)) if _coerce(body.mail_pass) else None,
        mass_url=_coerce(body.mass_url),
        mass_token_enc=encrypt(_coerce(body.mass_token)) if _coerce(body.mass_token) else None,
        huggingface_token_enc=encrypt(_coerce(body.huggingface_token)) if _coerce(body.huggingface_token) else None,
        skylight_url=_coerce(body.skylight_url),
        skylight_email=_coerce(body.skylight_email),
        skylight_pass_enc=encrypt(_coerce(body.skylight_pass)) if _coerce(body.skylight_pass) else None,
        skylight_enabled=body.skylight_enabled
    )
    _store_user_api_key(user, body.api_key or os.urandom(24).hex())
    session.add(user)
    session.commit()
    session.refresh(user)
    if user.family_role in roles.MINOR_FAMILY_ROLES:
        _force_minor_activity_sharing(session, user)
        session.commit()
    if roles.sync_family_role(session, user):
        session.commit()
    _audit(request, session, "user.create", user.username, actor=admin, before={}, after=audit.snapshot(user))
    return user

def _require_assignment_admin(
    user: User = Depends(require_api_key),
    session: Session = Depends(get_session),
) -> User:
    """Dependency: only someone with devices.assign may grant or revoke a device.

    Writing an assignment IS granting control of a real device. Without this
    any authenticated user could assign an entity to themselves and walk
    straight past both DeviceAssignment and entity protection.
    """
    if not roles.has_permission(session, user, "devices.assign"):
        raise HTTPException(status_code=403, detail="You can't assign devices to users.")
    return user


def _assignment_read(row: DeviceAssignment) -> DeviceAssignmentRead:
    return DeviceAssignmentRead(
        id=row.id or 0,
        device_id=row.device_id,
        user_id=row.user_id or 0,
        username=row.user.username if row.user else "",
        revoked=row.revoked,
    )


def _assignments_for(session: Session, device_id: str, username: str | None = None) -> list[DeviceAssignment]:
    """A device's assignments, or only the named user's."""
    query = select(DeviceAssignment).where(DeviceAssignment.device_id == device_id)
    if username:
        user = session.exec(select(User).where(User.username == username.lower())).first()
        if not user:
            return []
        query = query.where(DeviceAssignment.user_id == (user.id or 0))
    return list(session.exec(query).all())


def _grant_device(session: Session, body: DeviceAssignmentCreate, *, keep_revoked: bool = False) -> DeviceAssignmentRead:
    """Let one more user control a device; the others keep it.

    ``keep_revoked`` is for automatic grants: rediscovering an entity must not
    undo an admin's revoke.
    """
    target = session.exec(select(User).where(User.username == body.username.lower())).first()
    if not target:
        raise HTTPException(status_code=404, detail="User not found")
    row = session.exec(
        select(DeviceAssignment).where(
            DeviceAssignment.device_id == body.device_id,
            DeviceAssignment.user_id == (target.id or 0),
        )
    ).first()
    if row is None:
        row = DeviceAssignment(device_id=body.device_id, user_id=target.id or 0, revoked=body.revoked)
    elif not keep_revoked:
        row.revoked = body.revoked
    session.add(row)
    session.commit()
    session.refresh(row)
    return _assignment_read(row)


@app.get("/api/devices", response_model=list[DeviceAssignmentRead])
def list_devices(session: Session = Depends(get_session), _: User = Depends(require_api_key)):
    return [_assignment_read(d) for d in session.exec(select(DeviceAssignment)).all()]


@app.post("/api/devices", response_model=DeviceAssignmentRead)
def add_device(
    body: DeviceAssignmentCreate,
    session: Session = Depends(get_session),
    admin: User = Depends(_require_assignment_admin),
):
    return _grant_device(session, body)


@app.delete("/api/devices/{device_id}")
def remove_device(
    device_id: str,
    username: str | None = None,
    session: Session = Depends(get_session),
    admin: User = Depends(_require_assignment_admin),
):
    """Take the device away from ``username``, or from everyone when omitted."""
    rows = _assignments_for(session, device_id, username)
    if not rows:
        raise HTTPException(status_code=404, detail="Device assignment not found.")
    for row in rows:
        session.delete(row)
    session.commit()
    return {"status": "SUCCESS"}


@app.post("/api/devices/{device_id}/revoke")
def revoke_device(
    device_id: str,
    username: str | None = None,
    session: Session = Depends(get_session),
    admin: User = Depends(_require_assignment_admin),
):
    """Revoke ``username``'s use of the device, or everyone's when omitted."""
    rows = _assignments_for(session, device_id, username)
    if not rows:
        raise HTTPException(status_code=404, detail="Device assignment not found.")
    revoked = [row for row in rows if not row.revoked]
    if not revoked:
        return {"status": "SUCCESS", "message": "Device already revoked."}
    for row in revoked:
        row.revoked = True
        session.add(row)
    session.commit()
    names = ", ".join(sorted(row.user.username for row in revoked if row.user)) or "unknown"
    return {"status": "SUCCESS", "message": f"Device '{device_id}' revoked (was assigned to {names})."}

# --- Entity Protection --------------------------------------------------------
# A protected entity is one whose physical state only admins (plus the users on
# its own permit list) may change. This is the single authority for that rule:
# Execution asks for it per request and hides the entity from users who have no
# permit, so a stale DeviceAssignment row cannot re-open a locked entity.

# A Home Assistant entity id is one `domain.object_id` segment, so the route
# deliberately uses the *default* path converter rather than ``:path``: an id
# containing ``/`` then fails to match the route at all. ``:path`` looked
# harmless, but URL normalisation resolves ``a/../b`` before routing, so it let
# a single request lock `light.kitchen` while naming `climate.hallway`. The
# pattern below is anchored for the same reason.
_ENTITY_ID_RE = re.compile(r"^[a-z0-9_]+\.[a-z0-9_]+$")


def _permitted_usernames(row: EntityProtection) -> list[str]:
    """Decode the JSON permit list. A corrupt value is an error, not a grant."""
    raw = (row.permitted_usernames or "[]").strip()
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as e:
        raise HTTPException(
            status_code=500,
            detail=(
                f"Entity protection for '{row.entity_id}' has a corrupt "
                f"permitted_usernames value. Fix it in Identity -> Entity Protection."
            ),
        ) from e
    if not isinstance(parsed, list):
        raise HTTPException(
            status_code=500,
            detail=(
                f"Entity protection for '{row.entity_id}' has a non-list "
                f"permitted_usernames value. Fix it in Identity -> Entity Protection."
            ),
        )
    return [str(name).strip().lower() for name in parsed if str(name).strip()]


def _protection_read(row: EntityProtection) -> EntityProtectionRead:
    return EntityProtectionRead(
        entity_id=row.entity_id,
        permitted_usernames=_permitted_usernames(row),
        granted_by=row.granted_by,
        granted_at=row.granted_at,
        note=row.note,
    )


def _entity_protection_rows(session: Session) -> list[EntityProtection]:
    return list(session.exec(select(EntityProtection)).all())


def _permitted_protected_entity_ids(session: Session, user: User) -> list[str]:
    """Protected entity ids this user may control, beyond the admin bypass.

    The system default user is always permitted: it is the household's shared
    account and the one an admin uses to bootstrap the home.
    """
    rows = _entity_protection_rows(session)
    if roles.has_permission(session, user, "devices.use_locked") or _is_system_default_user(user):
        return [row.entity_id for row in rows]
    username = (user.username or "").lower()
    return [
        row.entity_id
        for row in rows
        if username in _permitted_usernames(row)
    ]


def _require_protection_admin(
    user: User = Depends(require_api_key),
    session: Session = Depends(get_session),
) -> User:
    """Dependency: entity protection is admin-only, for reads and writes alike.

    Protection is the only thing standing between a normal user and the house,
    so a non-admin must never be able to lock, unlock, or widen a permit list.
    """
    if not roles.has_permission(session, user, "devices.lock"):
        raise HTTPException(status_code=403, detail="You can't lock or unlock devices.")
    return user


@app.get("/api/entity-protection", response_model=list[EntityProtectionRead])
def list_entity_protection(
    session: Session = Depends(get_session),
    admin: User = Depends(_require_protection_admin),
):
    """Every protected entity and who may still control it. Admin only."""
    return [_protection_read(row) for row in _entity_protection_rows(session)]


@app.put("/api/entity-protection/{entity_id}", response_model=EntityProtectionRead)
def set_entity_protection(
    entity_id: str,
    body: EntityProtectionUpdate,
    session: Session = Depends(get_session),
    admin: User = Depends(_require_protection_admin),
):
    """Lock or release one entity. Admin only.

    `protected: false` releases the lock and forgets the permit list, so the
    entity reverts to plain DeviceAssignment rules with no stale state left.
    """
    entity_id = entity_id.strip()
    if not _ENTITY_ID_RE.match(entity_id):
        raise HTTPException(
            status_code=400,
            detail=(
                f"'{entity_id}' is not a Home Assistant entity id "
                "(expected something like 'climate.hallway')."
            ),
        )

    row = session.exec(
        select(EntityProtection).where(EntityProtection.entity_id == entity_id)
    ).first()
    if row and not body.protected:
        session.delete(row)
        session.commit()
        return EntityProtectionRead(
            entity_id=entity_id,
            permitted_usernames=[],
            note=f"Released by @{admin.username} on {dt.now(UTC).isoformat()}",
        )

    # Unknown usernames are rejected rather than stored, so a typo cannot
    # silently hand a permit to nobody.
    wanted = [str(name).strip().lower() for name in body.permitted_usernames if str(name).strip()]
    unknown = [
        name for name in wanted
        if not session.exec(select(User).where(User.username == name)).first()
    ]
    if unknown:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown username(s) in the permit list: {', '.join(sorted(unknown))}.",
        )

    now = dt.now(UTC).isoformat()
    if row is None:
        row = EntityProtection(
            entity_id=entity_id,
            permitted_usernames=json.dumps(wanted),
            granted_by=admin.username,
            granted_at=now,
            note=body.note,
        )
        session.add(row)
    else:
        row.permitted_usernames = json.dumps(wanted)
        row.granted_by = admin.username
        row.granted_at = now
        if body.note is not None:
            row.note = body.note
        session.add(row)
    session.commit()
    session.refresh(row)
    return _protection_read(row)


# --- Device Matrix (UI Contract) ---
@app.get("/api/internal/router")
def internal_router(session: Session = Depends(get_session), _: None = Depends(require_internal)):
    """Internal: the home router's read-only login for the control plane's
    monitor (docs/NETWORK.md). Kept out of /api/resolve, which hands a user's
    credentials to every service on every request."""
    user = session.get(User, 1)
    if not user or not user.router_url:
        return {"url": None, "user": None, "password": None, "cert_sha256": None}
    return {
        "url": user.router_url,
        "user": user.router_user,
        "password": decrypt(user.router_pass_enc) if user.router_pass_enc else None,
        "cert_sha256": user.router_cert_sha256,
    }


@app.get("/api/internal/owned-devices/{username}")
def internal_owned_devices(username: str, session: Session = Depends(get_session), _: None = Depends(require_internal)):
    """Internal: the phones and tablets assigned to a person (the sleep estimate
    joins their overnight use)."""
    rows = session.exec(
        select(Device).where(
            Device.owner_username == username.strip().lower(),
            Device.kind.in_(MOBILE_DEVICE_KINDS),
        )
    ).all()
    return {"devices": [row.device_key for row in rows]}


@app.get("/api/internal/device-registered/{device_key}")
def internal_device_registered(device_key: str, session: Session = Depends(get_session), _: None = Depends(require_internal)):
    """Internal: whether a phone or tablet with this key has registered."""
    row = session.exec(select(Device).where(Device.device_key == device_key.strip())).first()
    return {"registered": bool(row and row.kind in MOBILE_DEVICE_KINDS), "owner": row.owner_username if row else None}


@app.get("/api/internal/user-device-assignments")
def internal_user_device_assignments(
    username: str,
    session: Session = Depends(get_session),
    _: None = Depends(require_internal),
):
    """Internal: what this user may control.

    `device_ids` is the plain DeviceAssignment grant (unprotected entities).
    `protected_entity_ids` is every locked entity; a caller must not treat it
    as a grant, it only says the entity exists and is locked.
    `permitted_entity_ids` is the subset of locked entities this user may
    actually control. Admins and the system default user get all of them.
    """
    user = session.exec(select(User).where(User.username == username)).first()
    if not user:
        return {
            "device_ids": [],
            "protected_entity_ids": [],
            "permitted_entity_ids": [],
        }
    rows = session.exec(
        select(DeviceAssignment).where(
            DeviceAssignment.user_id == (user.id or 0),
            DeviceAssignment.revoked == False,  # noqa: E712
        )
    ).all()
    protections = _entity_protection_rows(session)
    return {
        "device_ids": [r.device_id for r in rows],
        "protected_entity_ids": [p.entity_id for p in protections],
        "permitted_entity_ids": _permitted_protected_entity_ids(session, user),
        # Every unlocked entity, assigned or not (role permission).
        "all_unprotected": roles.has_permission(session, user, "devices.use_all"),
    }


@app.get("/api/internal/validate-api-key")
def internal_validate_api_key(
    api_key: str | None = Header(None, alias="X-Api-Key"),
    session: Session = Depends(get_session),
    _: None = Depends(require_internal),
):
    """Internal: is this API key real, and whose is it?

    Deliberately has **no** fallback of any kind, which is what makes it safe
    to use as an authentication gate. `POST /api/resolve` still uses the
    system-default (admin) fallback for a request with no identifier at all, so
    a caller that validated a *missing* key through it would receive the
    administrator. This endpoint answers only "did this exact key
    authenticate", and 401s otherwise.

    Do not add a fallback here; that is the whole point of the endpoint.

    The key arrives in the ``X-Api-Key`` header, never the query string:
    a URL goes into the access log, and this is called ~1,000 times an hour.
    """
    if not api_key or not api_key.strip():
        raise HTTPException(status_code=400, detail="api_key is required")
    user = _find_user_for_api_key(session, api_key)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid API key")
    return {
        "user": user.username,
        "user_id": user.id or 0,
        "is_admin": bool(user.is_admin),
        "is_system_default": bool(user.is_system_default),
    }


@app.get("/api/users/devices", response_model=list[DeviceAssignmentRead])
def list_devices_ui(session: Session = Depends(get_session), user: User = Depends(require_api_key)):
    query = select(DeviceAssignment)
    if not roles.has_permission(session, user, "devices.assign"):
        query = query.where(DeviceAssignment.user_id == (user.id or 0))
    return [_assignment_read(d) for d in session.exec(query).all()]

@app.post("/api/users/devices", response_model=DeviceAssignmentRead)
def add_device_ui(
    body: DeviceAssignmentCreate,
    session: Session = Depends(get_session),
    authorization: str = Header(None),
    x_internal_secret: str = Header(None, alias="X-Internal-Secret")
):
    # Internal calls (Gateway's entity auto-discovery) send only
    # X-Internal-Secret; a user request must be an admin or home manager,
    # because writing an assignment grants control of a real device.
    is_internal = x_internal_secret == INTERNAL_SECRET
    if not is_internal:
        if not authorization:
            raise HTTPException(status_code=401, detail="Missing authorization")
        caller = require_api_key(authorization, session)
        if not roles.has_permission(session, caller, "devices.assign"):
            raise HTTPException(status_code=403, detail="You can't assign devices to users.")
    return _grant_device(session, body, keep_revoked=is_internal)

# Auth & Discovery

# Sign-in failures, pruned to a window. In-process like _UNLOCK_FAILURES: this
# is one household on one server, and a restart clearing the counters is
# acceptable. Two keys are kept so neither a single username nor a single
# source can be hammered.
_LOGIN_FAILURES: dict[str, list[float]] = {}
_LOGIN_WINDOW_S = 900
_LOGIN_MAX_PER_USER = 5
_LOGIN_MAX_PER_IP = 20


def _recent_failures(key: str, now: float) -> list[float]:
    recent = [t for t in _LOGIN_FAILURES.get(key, []) if now - t < _LOGIN_WINDOW_S]
    _LOGIN_FAILURES[key] = recent
    return recent


@app.post("/api/auth/login", response_model=LoginResponse)
def login(req: LoginRequest, request: Request, session: Session = Depends(get_session)):
    now = time.monotonic()
    ip = (request.client.host if request.client else "") or "unknown"
    user_key = f"user:{req.username.strip().lower()}"
    ip_key = f"ip:{ip}"
    if (
        len(_recent_failures(user_key, now)) >= _LOGIN_MAX_PER_USER
        or len(_recent_failures(ip_key, now)) >= _LOGIN_MAX_PER_IP
    ):
        raise HTTPException(
            status_code=429,
            detail="Too many sign-in attempts. Wait fifteen minutes, then try again.",
        )

    user = session.exec(select(User).where(User.username == req.username)).first()
    if not user or not user.password_hash:
        _recent_failures(user_key, now).append(now)
        _recent_failures(ip_key, now).append(now)
        raise HTTPException(status_code=401, detail="Invalid username or password")

    if not verify_password(req.password, user.password_hash):
        _recent_failures(user_key, now).append(now)
        _recent_failures(ip_key, now).append(now)
        raise HTTPException(status_code=401, detail="Invalid username or password")

    _LOGIN_FAILURES.pop(user_key, None)
    _LOGIN_FAILURES.pop(ip_key, None)

    session_key = _get_user_api_key(user)
    if not session_key:
        session_key = _store_user_api_key(user, os.urandom(24).hex())
        session.add(user)
        session.commit()
        session.refresh(user)

    return LoginResponse(
        api_key=session_key or "",
        username=user.username,
        is_admin=user.is_admin
    )

@app.post("/api/auth/change-password")
def change_password(body: ChangePasswordRequest, session: Session = Depends(get_session), user: User = Depends(require_api_key)):
    # Body model, not query param: the gateway proxies JSON bodies here and a
    # bare scalar would put the new password in URLs/access logs.
    user.password_hash = hash_password(body.new_password)
    # Rotate the session key. The key used to survive a password change, so
    # changing it did not sign anybody out - which is the one thing a person
    # expects it to do. The caller gets the new key back so the device they are
    # on stays signed in.
    new_key = _store_user_api_key(user, os.urandom(24).hex())
    session.add(user)
    session.commit()
    return {"status": "SUCCESS", "message": "Password updated", "api_key": new_key or ""}

def _is_secret_config_key(key: str) -> bool:
    """A test-connection field whose value must never reach a log line.

    Everything that holds a credential is masked: ``*_token``, ``*_pass``,
    ``*_api_key``, ``*_key`` and a bare ``api_key``. A field that does not look
    like a secret but was filled from a saved ``*_enc`` column is masked by the
    caller as well.
    """
    lowered = key.lower()
    return lowered == "api_key" or lowered.endswith(("_token", "_pass", "_api_key", "_key"))


#: Public SaaS git hosts, whose TLS certificate must be verified. A self-hosted
#: GitHub Enterprise / GitLab host keeps the insecure client for its own cert.
_PUBLIC_GIT_HOSTS = {"github.com", "api.github.com", "gitlab.com"}


def _test_connection_client(service: str, config: dict):
    """The pooled client a connection test should use for ``service``.

    GitHub and GitLab on their public SaaS hosts use the verifying
    ``get_client()``; private self-hosted services (Home Assistant, Nextcloud,
    Audiobookshelf, Music Assistant, Skylight) keep ``get_client_insecure()``
    for their self-signed certificates (services/common/http.py).
    """
    url = {
        "GitHub": config.get("github_url") or "https://api.github.com",
        "GitLab": config.get("gitlab_url") or "https://gitlab.com",
    }.get(service)
    if url and (urlparse(url).hostname or "").lower() in _PUBLIC_GIT_HOSTS:
        return get_client()
    return get_client_insecure()


@app.post("/api/auth/test-connection")
async def test_connection(req: dict, session: Session = Depends(get_session), admin: User = Depends(require_api_key)):
    """Test a connection before saving."""
    service = req.get("service")
    config = dict(req.get("config") or {})
    # Saved secrets are never sent to the browser, so "Test" on an already
    # connected integration arrives with the token/password blank. Test with
    # the caller's own saved value instead of failing "Token is required".
    filled_from_saved: set[str] = set()
    for key in list(config):
        if not config.get(key) and isinstance(getattr(admin, f"{key}_enc", None), str):
            config[key] = _decrypt_field(getattr(admin, f"{key}_enc"))
            filled_from_saved.add(key)
    # Never log a secret: mask anything named like one, plus anything just
    # filled from a saved *_enc column (a decrypted value must not be logged).
    log.info(
        f"[test_connection] Testing {service} with config: "
        f"{ {k: '***' if _is_secret_config_key(k) or k in filled_from_saved else v for k, v in config.items()} }"
    )

    try:
        async with _test_connection_client(service, config) as client:
            if service == "Home Assistant":
                url = config.get("ha_url")
                token = config.get("ha_token")
                if not url or not token:
                    return {"status": "ERROR", "message": "URL and Token are required"}

                resp = await client.get(
                    f"{url.rstrip('/')}/api/config",
                    headers={"Authorization": f"Bearer {token}"},
                    timeout=aiohttp.ClientTimeout(total=5.0),
                )
                log.info(f"[test_connection] HA response: {resp.status}")
                if resp.status == 200:
                    return {"status": "SUCCESS", "message": "Connected to Home Assistant"}
                else:
                    return {"status": "ERROR", "message": f"HA returned {resp.status}: {(await resp.text())[:100]}"}

            elif service == "Nextcloud":
                url = config.get("nextcloud_url")
                user = config.get("nextcloud_user")
                password = config.get("nextcloud_pass")
                if not url or not user or not password:
                    return {"status": "ERROR", "message": "URL, User, and Password are required"}

                # /ocs/v1.php/cloud/users is admin-only provisioning and
                # reports a permission failure as HTTP 200 with
                # ocs.meta.statuscode 997/998, so read the caller's own account
                # and check the OCS status code instead (the same parse as
                # execution's nextcloud_client).
                resp = await client.get(
                    f"{url.rstrip('/')}/ocs/v2.php/cloud/user?format=json",
                    headers={"OCS-APIRequest": "true"},
                    auth=aiohttp.BasicAuth(user, password),
                    timeout=aiohttp.ClientTimeout(total=5.0),
                )
                if resp.status != 200:
                    return {"status": "ERROR", "message": f"Nextcloud returned {resp.status}"}
                try:
                    payload = await resp.json()
                    meta = (payload.get("ocs") or {}).get("meta") or {}
                    statuscode = int(meta.get("statuscode") or 0)
                except (AttributeError, TypeError, ValueError):
                    return {"status": "ERROR", "message": "Nextcloud returned an unexpected response"}
                if statuscode in (100, 200):
                    return {"status": "SUCCESS", "message": "Connected to Nextcloud"}
                return {"status": "ERROR", "message": f"Nextcloud returned OCS status {statuscode}"}

            elif service == "GitHub":
                url = config.get("github_url") or "https://api.github.com"
                token = config.get("github_token")
                if not token:
                    return {"status": "ERROR", "message": "Personal Token is required"}

                resp = await client.get(
                    f"{url.rstrip('/')}/user",
                    headers={"Authorization": f"token {token}"},
                    timeout=aiohttp.ClientTimeout(total=5.0),
                )
                if resp.status == 200:
                    json_data = await resp.json()
                    return {"status": "SUCCESS", "message": f"Connected to GitHub as {json_data.get('login')}"}
                else:
                    return {"status": "ERROR", "message": f"GitHub returned {resp.status}"}

            elif service == "GitLab":
                url = config.get("gitlab_url") or "https://gitlab.com"
                token = config.get("gitlab_token")
                if not token:
                    return {"status": "ERROR", "message": "Access Token is required"}

                resp = await client.get(
                    f"{url.rstrip('/')}/api/v4/user",
                    headers={"PRIVATE-TOKEN": token},
                    timeout=aiohttp.ClientTimeout(total=5.0),
                )
                if resp.status == 200:
                    json_data = await resp.json()
                    return {"status": "SUCCESS", "message": f"Connected to GitLab as {json_data.get('username')}"}
                else:
                    return {"status": "ERROR", "message": f"GitLab returned {resp.status}"}

            elif service == "Audiobookshelf":
                url = config.get("audiobookshelf_url") or config.get("abs_url")
                api_key = config.get("audiobookshelf_api_key")
                username = config.get("audiobookshelf_user") or config.get("abs_user")
                password = config.get("audiobookshelf_pass") or config.get("abs_pass")
                if not url:
                    return {"status": "ERROR", "message": "URL is required"}
                if api_key:
                    # An API key is enough on its own: ABS accepts it as a
                    # Bearer token on the authenticated /api/me route
                    # (verified live 2026-09-28; a bad key gets 401).
                    resp = await client.get(
                        f"{url.rstrip('/')}/api/me",
                        headers={"Authorization": f"Bearer {api_key}"},
                        timeout=aiohttp.ClientTimeout(total=5.0),
                    )
                    if resp.status == 200:
                        data = await resp.json()
                        return {"status": "SUCCESS", "message": f"Connected to Audiobookshelf as {data.get('username')}"}
                    return {"status": "ERROR", "message": f"Audiobookshelf returned {resp.status}: {(await resp.text())[:100]}"}
                if not username or not password:
                    return {"status": "ERROR", "message": "An API key, or username and password, are required"}

                # ABS logs in at the root /login, not /api/login (verified
                # live: /login -> 200, /api/login -> 401; see abs_client.py).
                resp = await client.post(
                    f"{url.rstrip('/')}/login",
                    json={"username": username, "password": password},
                    timeout=aiohttp.ClientTimeout(total=5.0),
                )
                if resp.status == 200:
                    data = await resp.json()
                    user_info = data.get("user", {})
                    return {"status": "SUCCESS", "message": f"Connected to Audiobookshelf as {user_info.get('username')}"}
                else:
                    return {"status": "ERROR", "message": f"Audiobookshelf returned {resp.status}: {(await resp.text())[:100]}"}

            elif service == "Music Assistant":
                # MA is token-only. Its JSON-RPC endpoint rejects a bad Bearer
                # token with 401 (verified live against MA 2.10.4 on
                # 2026-09-28), so one cheap read proves both reachability and
                # the token.
                url = config.get("mass_url")
                token = config.get("mass_token")
                if not url or not token:
                    return {"status": "ERROR", "message": "mass_url and mass_token are required"}

                resp = await client.post(
                    f"{url.rstrip('/')}/api",
                    json={
                        "message_id": uuid.uuid4().hex,
                        "command": "music/playlists/library_items",
                        "args": {},
                    },
                    headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
                    timeout=aiohttp.ClientTimeout(total=10.0),
                )
                if resp.status == 200:
                    return {"status": "SUCCESS", "message": "Connected to Music Assistant"}
                return {"status": "ERROR", "message": f"Music Assistant returned {resp.status}: {(await resp.text())[:100]}"}

            return {"status": "ERROR", "message": f"Service {service} not testable yet"}

    except aiohttp.InvalidURL:
        # A URL with no scheme (or otherwise unparseable) reaches here; tell the
        # user what to fix instead of echoing aiohttp's InvalidURL internals.
        log.error(f"[test_connection] {service}: invalid URL")
        return {"status": "ERROR", "message": "URL must start with http:// or https://"}
    except Exception as e:
        # Log the detail server-side; never echo exception text (it can carry
        # hosts, URLs or other request internals) back to the client.
        log.error(f"[test_connection] {service} failed: {type(e).__name__}: {e}")
        return {"status": "ERROR", "message": f"Could not connect to {service}. Check the URL and credentials."}

# Credential Seeding

SEEDABLE_CREDENTIALS = {
    "nextcloud_pass": ("nextcloud_pass_enc", "nextcloud_pass"),
    "ha_token": ("ha_token_enc", "ha_token"),
    "github_token": ("github_token_enc", "github_token"),
    "gitlab_token": ("gitlab_token_enc", "gitlab_token"),
    "audiobookshelf_url": ("audiobookshelf_url", "audiobookshelf_url"),
    "audiobookshelf_user": ("audiobookshelf_user", "audiobookshelf_user"),
    "audiobookshelf_pass": ("audiobookshelf_pass_enc", "audiobookshelf_pass"),
    "audiobookshelf_api_key": ("audiobookshelf_api_key_enc", "audiobookshelf_api_key"),
    "mailcow_url": ("mailcow_url", "mailcow_url"),
    "mailcow_api_key": ("mailcow_api_key_enc", "mailcow_api_key"),
    "mail_user": ("mail_user", "mail_user"),
    "mail_pass": ("mail_pass_enc", "mail_pass"),
    "mass_token": ("mass_token_enc", "mass_token"),
    "git_token": ("git_token_enc", "git_token"),
    "huggingface_token": ("huggingface_token_enc", "huggingface_token"),
    "skylight_pass": ("skylight_pass_enc", "skylight_pass"),
    "router_url": ("router_url", "router_url"),
    "router_user": ("router_user", "router_user"),
    "router_pass": ("router_pass_enc", "router_pass"),
    "router_cert_sha256": ("router_cert_sha256", "router_cert_sha256"),
}

@app.post("/api/admin/seed-credential")
def seed_credential(body: dict, request: Request, session: Session = Depends(get_session), _allowed: bool = Depends(require_admin_or_internal)):
    """Seed a single credential for the default user (User 1) without re-seeding the entire DB.

    Accepts a credential field name and its plain text value. The value is encrypted and stored.
    ``require_admin_or_internal`` admits only an admin or an internal caller; it
    returns True, not a user, so the old ``admin.is_admin`` check here raised on
    every call and the endpoint never worked.
    """

    field = body.get("field")
    value = body.get("value")

    if not field:
        raise HTTPException(status_code=400, detail="field is required")
    if field not in SEEDABLE_CREDENTIALS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid field. Valid fields: {', '.join(SEEDABLE_CREDENTIALS.keys())}"
        )
    if value is None:
        raise HTTPException(status_code=400, detail="value is required")

    enc_field, _plain_field = SEEDABLE_CREDENTIALS[field]

    # Get the default user
    user = session.exec(select(User).where(User.id == 1)).first()
    if not user:
        # Fall back to 'default' username
        user = session.exec(select(User).where(User.username == "default")).first()

    if not user:
        raise HTTPException(status_code=404, detail="Default user (ID 1) not found in database. Run full seed first.")

    # Encrypt credentials only. Plain fields (URLs, usernames) are stored as
    # given: encrypting them stored ciphertext as User 1's Audiobookshelf URL.
    secret = enc_field.endswith("_enc")
    if field == "router_url" and value != user.router_url:
        # Another router: its certificate is pinned afresh on first contact.
        user.router_cert_sha256 = None
    if value:
        setattr(user, enc_field, encrypt(value) if secret else value)
        log.info(f"[seed-credential] Updated {field} for user {user.username}")
    else:
        setattr(user, enc_field, None)
        log.info(f"[seed-credential] Cleared {field} for user {user.username}")

    session.add(user)
    session.commit()
    session.refresh(user)
    _audit(request, session, "credential.seed", user.username,
           changes=[{"field": field, "change": "set" if value else "cleared", **({"secret": True} if secret else {})}])

    return {
        "status": "SUCCESS",
        "message": f"Seeded {field} for user {user.username}",
        "field": field,
        "has_value": bool(value)
    }

# API Key Management

@app.get("/api/users/me/keys")
def get_my_keys(session: Session = Depends(get_session), user: User = Depends(require_api_key)):
    """Return list of API keys for the current user.

    Admin users see all keys with associated usernames.
    Non-admin users see only their own keys.
    """
    from sqlmodel import select

    if user.is_admin:
        # Admin sees all keys with associated usernames
        all_keys = session.exec(select(APIKey, User).join(User, APIKey.user_id == User.id)).all()  # type: ignore[arg-type]
        result = []
        for api_key, user_obj in all_keys:
            result.append({
                "id": api_key.id,
                "label": api_key.label,
                "prefix": api_key.key_prefix or _api_key_prefix(api_key.key_value) or "unavailable",
                "created_at": api_key.created_at,
                "owner_username": user_obj.username,
                "owner_id": user_obj.id
            })
        return result
    else:
        # Non-admin users see only their own keys
        return [
            {
                "id": k.id,
                "label": k.label,
                "prefix": k.key_prefix or _api_key_prefix(k.key_value) or "unavailable",
                "created_at": k.created_at,
                "owner_username": user.username,
                "owner_id": user.id
            } for k in user.api_keys
        ]

@app.post("/api/users/me/keys")
def generate_key(body: dict, request: Request, session: Session = Depends(get_session), user: User = Depends(require_api_key)):
    """Generate a new API key for the current user."""
    import secrets
    new_key_value = "sk-" + secrets.token_hex(24)
    new_key = APIKey(label=body.get("label", "New Key"), user_id=user.id or 0)
    _store_generated_api_key(new_key, new_key_value)
    session.add(new_key)
    session.commit()
    session.refresh(new_key)
    _audit(request, session, "api_key.create", user.username, actor=user,
           changes=[{"field": "api_key", "change": "set", "secret": True}], note=f"label: {new_key.label}")
    return {"id": new_key.id, "label": new_key.label, "key": new_key_value} # Only show full key once!

@app.delete("/api/users/me/keys/{key_id}")
def revoke_key(key_id: int, request: Request, session: Session = Depends(get_session), user: User = Depends(require_api_key)):
    """Revoke an API key."""
    key = session.exec(select(APIKey).where(APIKey.id == key_id, APIKey.user_id == user.id)).first()
    if not key:
        raise HTTPException(status_code=404, detail="Key not found")
    session.delete(key)
    session.commit()
    _audit(request, session, "api_key.revoke", user.username, actor=user,
           changes=[{"field": "api_key", "change": "cleared", "secret": True}], note=f"label: {key.label}")
    return {"success": True}

@app.get("/api/auth/discover", response_model=DiscoverResponse)
async def discover_users(session: Session = Depends(get_session), admin: User = Depends(require_api_key)):
    """Scan Home Assistant, Nextcloud, Audiobookshelf and Mailcow for users to import.

    Sources are scanned with the admin's own credentials, falling back to the
    default user's, so one admin can onboard the whole family without typing
    every service's login. Usernames are merged across sources and existing
    Jarvis users are always skipped.
    """
    if not admin.is_admin:
        raise HTTPException(status_code=403, detail="Admin only")

    warnings: list[str] = []
    errors: list[str] = []

    # Resolve credentials to use (prefer admin's, fallback to default)
    default_user = session.exec(select(User).where(User.username == "default")).first()

    def _pick(attr: str, encrypted: bool = False):
        mine = getattr(admin, attr, None)
        if mine:
            return decrypt(mine) if encrypted and mine else mine
        theirs = getattr(default_user, attr, None) if default_user else None
        if not theirs:
            return None
        return decrypt(theirs) if encrypted and theirs else theirs

    ha_url = _pick("ha_url")
    ha_token = _pick("ha_token_enc", encrypted=True)

    nc_url = _pick("nextcloud_url")
    nc_user = _pick("nextcloud_user")
    nc_pass = _pick("nextcloud_pass_enc", encrypted=True)

    abs_url = _pick("audiobookshelf_url")
    abs_user = _pick("audiobookshelf_user")
    abs_pass = _pick("audiobookshelf_pass_enc", encrypted=True)
    abs_key = _pick("audiobookshelf_api_key_enc", encrypted=True)

    # Mailcow lives beside Nextcloud, but the address is configuration, not a
    # guess: without a URL the scan is skipped with a warning rather than
    # silently probing somewhere.
    mailcow_url = _pick("mailcow_url")
    mailcow_key = _pick("mailcow_api_key_enc", encrypted=True)

    log.info(f"[discovery] Starting scan. HA_URL: {ha_url}, NC_URL: {nc_url}, ABS_URL: {abs_url}, MAILCOW_URL: {mailcow_url}")

    # Collect users from each source into dicts keyed by lowercase username
    ha_users: dict[str, dict] = {}
    nc_users: dict[str, dict] = {}
    abs_users: dict[str, dict] = {}
    mail_users: dict[str, dict] = {}

    # 1. Scan Home Assistant (Person entities)
    if ha_url and ha_token:
        try:
            async with get_client() as client:
                resp = await client.get(
                    f"{ha_url.rstrip('/')}/api/states",
                    headers={"Authorization": f"Bearer {ha_token}"},
                    timeout=aiohttp.ClientTimeout(total=10.0),
                )
                if resp.status == 200:
                    for state in await resp.json():
                        if state['entity_id'].startswith('person.'):
                            username = state['entity_id'].split('.')[1]
                            ha_users[username] = {
                                "display_name": state.get('attributes', {}).get('friendly_name', username),
                                "entity_id": state['entity_id'],
                            }
        except Exception as e:
            log.error(f"[discovery] HA Error: {e!s}")
            errors.append(f"Home Assistant: {e!s}")

    # 2. Scan Nextcloud (Provisioning API)
    if nc_url and nc_user and nc_pass:
        try:
            async with get_client() as client:
                resp = await client.get(
                    f"{nc_url.rstrip('/')}/ocs/v1.php/cloud/users?format=json",
                    headers={"OCS-APIRequest": "true"},
                    auth=aiohttp.BasicAuth(nc_user, nc_pass),
                    timeout=aiohttp.ClientTimeout(total=10.0),
                )
                if resp.status == 200:
                    data = await resp.json()
                    # Handle Nextcloud API error responses
                    meta = data.get("ocs", {}).get("meta", {})
                    if meta.get("status") == "failure":
                        msg = meta.get("message", "Unknown error")
                        warn_text = f"Nextcloud: {msg}"
                        log.warning(f"[discovery] {warn_text}")
                        warnings.append(warn_text)
                    else:
                        usernames = data.get("ocs", {}).get("data", {}).get("users", [])
                        for username in usernames:
                            nc_users[username.lower()] = {"nc_username": username}
                            # Fetch detailed info for each user
                            try:
                                detail_resp = await client.get(
                                    f"{nc_url.rstrip('/')}/ocs/v1.php/cloud/users/{username}",
                                    headers={"OCS-APIRequest": "true", "Accept": "application/json"},
                                    auth=aiohttp.BasicAuth(nc_user, nc_pass),
                                    params={"format": "json"}
                                )
                                if detail_resp.status == 200:
                                    nc_data = (await detail_resp.json()).get("ocs", {}).get("data", {})
                                    nc_users[username.lower()]["display_name"] = nc_data.get("display-name") or nc_data.get("displayname")
                                    nc_users[username.lower()]["email"] = nc_data.get("email")
                            except Exception:
                                pass
        except Exception as e:
            log.error(f"[discovery] Nextcloud Error: {e!s}")
            errors.append(f"Nextcloud: {e!s}")

    # 3. Scan Audiobookshelf (library users): basic auth, or the API key header
    if abs_url and (abs_key or (abs_user and abs_pass)):
        try:
            headers = {"Authorization": f"Bearer {abs_key}"} if abs_key else {}
            auth = None if abs_key else aiohttp.BasicAuth(abs_user, abs_pass)
            async with get_client() as client:
                resp = await client.get(
                    f"{abs_url.rstrip('/')}/api/users",
                    headers=headers,
                    auth=auth,
                    timeout=aiohttp.ClientTimeout(total=10.0),
                )
                if resp.status == 200:
                    payload = await resp.json()
                    for entry in payload.get("users", []):
                        username = (entry.get("username") or "").strip()
                        if not username:
                            continue
                        abs_users[username.lower()] = {
                            "abs_username": username,
                            "display_name": entry.get("name") or username,
                        }
                else:
                    warnings.append(f"Audiobookshelf returned {resp.status}")
        except Exception as e:
            log.error(f"[discovery] ABS Error: {e!s}")
            errors.append(f"Audiobookshelf: {e!s}")

    # 4. Scan Mailcow (mailboxes become the user's mail address)
    if mailcow_url and mailcow_key:
        try:
            async with get_client() as client:
                resp = await client.get(
                    f"{mailcow_url.rstrip('/')}/api/v1/mailbox",
                    headers={"Authorization": f"Token {mailcow_key}"},
                    timeout=aiohttp.ClientTimeout(total=10.0),
                )
                if resp.status == 200:
                    for entry in (await resp.json()).get("items", []):
                        address = (entry.get("local_part") or "").strip()
                        if not address or address in ("admin", "postmaster"):
                            continue
                        mail_users[address.lower()] = {
                            "mail_address": entry.get("email") or address,
                            "display_name": entry.get("name") or address,
                            "active": bool(entry.get("active", True)),
                        }
                else:
                    warnings.append(f"Mailcow returned {resp.status}")
        except Exception as e:
            log.error(f"[discovery] Mailcow Error: {e!s}")
            errors.append(f"Mailcow: {e!s}")
    elif not mailcow_url:
        warnings.append("Mailcow: not configured; set mailcow_url to include mailboxes in onboarding")

    # 5. Merge users: one entry per username, combining every source.
    #    Display names are matched loosely (case/punctuation) so "Mom" in HA
    #    and "mom" in Nextcloud land on the same person instead of two rows.
    all_usernames = set(ha_users) | set(nc_users) | set(abs_users) | set(mail_users)
    def _norm(name: str) -> str:
        return "".join(ch for ch in str(name).lower() if ch.isalnum())

    known_norms = {_norm(u) for u in all_usernames}
    aliases: dict[str, str] = {}
    for username in sorted(all_usernames):
        bucket = {**ha_users.get(username, {}), **nc_users.get(username, {}),
                  **abs_users.get(username, {}), **mail_users.get(username, {})}
        for candidate in (bucket.get("display_name"), *sorted(all_usernames)):
            if not candidate or username == candidate:
                continue
            # Never alias onto something that is itself a discovered login;
            # otherwise "Mom" and "kiddo" can collapse into a single person.
            if _norm(candidate) in known_norms and _norm(candidate) != _norm(username):
                continue
            aliases.setdefault(_norm(candidate), username)

    buckets: dict[str, dict] = {}
    for username in all_usernames:
        canonical = aliases.get(_norm(username), username)
        merged = buckets.setdefault(canonical, {"sources": [], "ha": {}, "nc": {}, "abs": {}, "mail": {}})
        for key, store in (("ha", ha_users), ("nc", nc_users), ("abs", abs_users), ("mail", mail_users)):
            if username in store:
                merged[key] = store[username]
                merged["sources"].append(key)

    discovered = []
    for username, merged in sorted(buckets.items()):
        existing = session.exec(select(User).where(User.username == username)).first()
        if existing:
            continue

        ha_data = merged["ha"]
        nc_data = merged["nc"]
        abs_data = merged["abs"]
        mail_data = merged["mail"]

        labels = {"ha": "Home Assistant", "nc": "Nextcloud", "abs": "Audiobookshelf", "mail": "Mailcow"}
        source = " + ".join(labels[s] for s in merged["sources"])

        # Prefer a real human name over a login handle
        display_name = (
            nc_data.get("display_name")
            or ha_data.get("display_name")
            or abs_data.get("display_name")
            or mail_data.get("display_name")
            or username.capitalize()
        )
        email = nc_data.get("email") or mail_data.get("mail_address")

        discovered.append(DiscoverUser(
            username=username,
            source=source,
            display_name=display_name,
            email=email,
            ha_person_id=ha_data.get("entity_id"),
            nc_username=nc_data.get("nc_username"),
            abs_username=abs_data.get("abs_username"),
            mail_address=mail_data.get("mail_address"),
            mailcow_address=mail_data.get("mailcow_address"),
        ))

    log.info(f"[discovery] Discovery complete. Found {len(discovered)} users.")
    return DiscoverResponse(users=discovered, warnings=warnings, errors=errors)

# Onboarding: one-time password → per-user token

# Every exchange trades a password the user typed once for a long-lived token
# that Jarvis can use afterwards. The password is never stored.
_TOKEN_EXCHANGES = ("home_assistant", "nextcloud", "audiobookshelf")

HA_CLIENT_ID = "http://homeassistant.local/"
HA_REDIRECT_URI = "homeassistant://auth-callback"


async def _exchange_home_assistant_token(url: str, username: str, password: str) -> str:
    """HA's documented native-app login flow, ending in a long-lived token.

    HA has no admin API to mint tokens for other users, so the only automation
    available is trading the user's own password for one. Any HA refusal is
    surfaced verbatim instead of being papered over.
    """
    base = url.rstrip("/")
    auth: dict = {"client_id": HA_CLIENT_ID, "handler": ["homeassistant", "credential"]}
    async with get_client_insecure() as client:
        resp = await client.post(
            f"{base}/auth/login_flow",
            json={
                **auth,
                "redirect_uri": HA_REDIRECT_URI,
                "type": "credentials",
                "username": username,
                "password": password,
            },
            timeout=aiohttp.ClientTimeout(total=15.0),
        )
        if resp.status != 200:
            raise HTTPException(status_code=400, detail=f"Home Assistant rejected the login ({resp.status})")
        flow = await resp.json()
        result_id = flow.get("result_id")
        if not result_id:
            # Wrong username/password: HA answers 400 with an auth_invalid marker.
            raise HTTPException(status_code=400, detail="Home Assistant did not accept those credentials")

        resp = await client.post(f"{base}/auth/login_flow/{result_id}", json=auth, timeout=aiohttp.ClientTimeout(total=15.0))
        if resp.status != 200:
            raise HTTPException(status_code=400, detail="Home Assistant did not complete the login flow")
        auth_code = (await resp.json()).get("result")
        if not auth_code:
            raise HTTPException(status_code=400, detail="Home Assistant did not return an authorization code")

        resp = await client.post(
            f"{base}/auth/token",
            data={"client_id": HA_CLIENT_ID, "grant_type": "authorization_code", "code": auth_code},
            timeout=aiohttp.ClientTimeout(total=15.0),
        )
        if resp.status != 200:
            raise HTTPException(status_code=400, detail=f"Home Assistant refused the token exchange ({resp.status})")
        token = (await resp.json()).get("access_token")
        if not token:
            raise HTTPException(status_code=400, detail="Home Assistant returned no access token")
        return token


async def _exchange_audiobookshelf_token(url: str, username: str, password: str) -> str:
    """ABS issues a bearer token from a plain login."""
    async with get_client_insecure() as client:
        resp = await client.post(
            f"{url.rstrip('/')}/login",
            json={"username": username, "password": password},
            timeout=aiohttp.ClientTimeout(total=15.0),
        )
        if resp.status != 200:
            raise HTTPException(status_code=400, detail=f"Audiobookshelf rejected the login ({resp.status})")
        token = (await resp.json()).get("token")
        if not token:
            raise HTTPException(status_code=400, detail="Audiobookshelf returned no token")
        return token


async def _create_nextcloud_app_password(admin: User, nc_username: str) -> str:
    """Nextcloud lets an admin mint an app password for any user.

    No password from the user is needed at all here; this is the one service
    where onboarding is fully hands-off.
    """
    base = (admin.nextcloud_url or "").rstrip("/")
    admin_user = admin.nextcloud_user
    admin_pass = decrypt(admin.nextcloud_pass_enc) if admin.nextcloud_pass_enc else None
    if not base or not admin_user or not admin_pass:
        raise HTTPException(status_code=400, detail="Nextcloud is not configured on this admin account")
    async with get_client_insecure() as client:
        resp = await client.post(
            f"{base}/ocs/v1.php/cloud/users/{nc_username}/app-passwords",
            data={"name": "jarvis-onboarding"},
            headers={"OCS-APIRequest": "true", "Accept": "application/json"},
            auth=aiohttp.BasicAuth(admin_user, admin_pass),
            timeout=aiohttp.ClientTimeout(total=15.0),
        )
        if resp.status != 200:
            raise HTTPException(status_code=400, detail=f"Nextcloud refused to create an app password ({resp.status})")
        body = await resp.json()
        meta = body.get("ocs", {}).get("meta", {})
        if meta.get("status") == "failure":
            raise HTTPException(status_code=400, detail=f"Nextcloud: {meta.get('message', 'unknown error')}")
        password = body.get("ocs", {}).get("data", {}).get("password")
        if not password:
            raise HTTPException(status_code=400, detail="Nextcloud returned no app password")
        return password


@app.post("/api/users/{username}/service-token")
async def create_service_token(
    username: str,
    body: dict,
    request: Request,
    session: Session = Depends(get_session),
    admin: User = Depends(require_api_key),
):
    """Trade a one-time password for a long-lived per-user token.

    `service` is home_assistant, nextcloud or audiobookshelf. The password is
    used once inside the exchange and never persisted: only the resulting
    token is stored (encrypted). Callers may be the user themselves or an
    admin onboarding them.
    """
    service = str(body.get("service") or "").strip()
    if service not in _TOKEN_EXCHANGES:
        raise HTTPException(status_code=422, detail=f"service must be one of {', '.join(_TOKEN_EXCHANGES)}")

    target = session.exec(select(User).where(User.username == username.lower())).first()
    if not target:
        raise HTTPException(status_code=404, detail="User not found")
    if not admin.is_admin and admin.username != target.username:
        raise HTTPException(status_code=403, detail="You can only set up your own account")

    password = str(body.get("password") or "")

    if service == "home_assistant":
        if not password:
            raise HTTPException(status_code=422, detail="A password is required to exchange for a Home Assistant token")
        ha_url = body.get("ha_url") or target.ha_url or (admin.ha_url if admin.is_admin else None)
        if not ha_url:
            raise HTTPException(status_code=422, detail="Home Assistant is not configured (set ha_url first)")
        token = await _exchange_home_assistant_token(ha_url, body.get("source_username") or target.username, password)
        target.ha_token_enc = encrypt(token)
    elif service == "audiobookshelf":
        if not password:
            raise HTTPException(status_code=422, detail="A password is required to exchange for an Audiobookshelf token")
        abs_url = body.get("audiobookshelf_url") or target.audiobookshelf_url or (admin.audiobookshelf_url if admin.is_admin else None)
        if not abs_url:
            raise HTTPException(status_code=422, detail="Audiobookshelf is not configured (set audiobookshelf_url first)")
        token = await _exchange_audiobookshelf_token(abs_url, body.get("source_username") or target.username, password)
        target.audiobookshelf_api_key_enc = encrypt(token)
    else:  # nextcloud
        nc_username = body.get("source_username") or target.nextcloud_user or target.username
        app_password = await _create_nextcloud_app_password(admin, nc_username)
        target.nextcloud_user = nc_username
        target.nextcloud_pass_enc = encrypt(app_password)

    session.add(target)
    session.commit()
    _audit(request, session, "credential.service_token", target.username, actor=admin,
           changes=[{"field": f"{service}_token", "change": "set", "secret": True}])

    return {
        "success": True,
        "username": target.username,
        "service": service,
        "message": f"{service} token stored for {target.username}; the password was not saved",
    }


# Admin

@app.get("/api/settings", response_model=list[GlobalSettingRead])
def get_settings(
    session: Session = Depends(get_session),
    x_internal_secret: str = Header(None, alias="X-Internal-Secret"),
    auth: bool = Depends(require_admin_or_internal)
):
    settings = session.exec(select(GlobalSetting)).all()
    # Mask sensitive keys for non-internal (UI) requests
    if x_internal_secret != INTERNAL_SECRET:
        for s in settings:
            if s.key == "llm_cloud_api_key" and s.value:
                s.value = "sk-***"
            if s.key == "spotify_client_secret" and s.value:
                s.value = "***"
    return settings

# Keys that must never be written with a blank/empty value.
# The UI dropdowns are always populated with real values, so a blank write
# here means a UI bug or a bad direct API call; both must fail loudly.
_MODEL_KEYS = {
    "assistant_model", "librarian_model", "coding_model", "vision_ocr_model",
    "telemetry_model",
}

@app.post("/api/settings")
def update_settings_bulk(
    body: dict[str, str],
    session: Session = Depends(get_session),
    auth: bool = Depends(require_admin_or_internal)
):
    """
    Securely accept raw keys and commit them to the database without logging the raw payload.
    """
    for key, value in body.items():
        if key in _MODEL_KEYS and not (value or "").strip():
            raise HTTPException(
                status_code=400,
                detail=f"Model setting '{key}' cannot be blank. Select a valid model from the dropdown."
            )
        setting = session.exec(select(GlobalSetting).where(GlobalSetting.key == key)).first()
        if not setting:
            setting = GlobalSetting(key=key, value=value)
        else:
            setting.value = value
        session.add(setting)

    session.commit()
    return {"status": "SUCCESS"}

@app.get("/api/settings/{key}", response_model=GlobalSettingRead)
def get_setting(
    key: str,
    session: Session = Depends(get_session),
    auth: bool = Depends(require_admin_or_internal),
    x_internal_secret: str = Header(None, alias="X-Internal-Secret")
):
    setting = session.exec(select(GlobalSetting).where(GlobalSetting.key == key)).first()
    if not setting:
        raise HTTPException(status_code=404, detail="Setting not found")

    # Mask sensitive keys for non-internal (UI) requests
    if x_internal_secret != INTERNAL_SECRET and key == "llm_cloud_api_key" and setting.value:
        setting.value = "sk-***"

    return setting

@app.patch("/api/settings/{key}", response_model=GlobalSettingRead)
def update_setting(key: str, body: GlobalSettingUpdate, session: Session = Depends(get_session), auth: bool = Depends(require_admin_or_internal)):
    if key in _MODEL_KEYS and not (body.value or "").strip():
        raise HTTPException(
            status_code=400,
            detail=f"Model setting '{key}' cannot be blank. Select a valid model from the dropdown."
        )

    setting = session.exec(select(GlobalSetting).where(GlobalSetting.key == key)).first()
    if not setting:
        setting = GlobalSetting(key=key, value=body.value)
    else:
        setting.value = body.value

    session.add(setting)
    session.commit()
    session.refresh(setting)
    return setting

class _SeedRequest(BaseModel):
    force: bool = False

@app.post("/api/admin/seed", dependencies=[Depends(require_internal)])
def manual_seed(body: _SeedRequest | None = None, force: bool = False, session: Session = Depends(get_session)):
    # Accept force from either JSON body or query param
    should_force = (body.force if body else False) or force
    count = seed_from_env(session, force=should_force)
    return {"status": "SUCCESS", "count": count}

# DNS Management

def validate_ip(value: str) -> bool:
    """Validate IPv4 address."""
    pattern = r'^(\d{1,3}\.){3}\d{1,3}$'
    if not re.match(pattern, value):
        return False
    parts = value.split('.')
    return all(0 <= int(part) <= 255 for part in parts)

def validate_hostname(value: str) -> bool:
    """Validate hostname."""
    pattern = r'^[a-zA-Z0-9]([a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?(\.[a-zA-Z0-9]([a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?)*$'
    return bool(re.match(pattern, value))

def validate_value(value: str, record_type: str) -> bool:
    """Validate a DNS record value based on type."""
    if record_type == "A":
        return validate_ip(value)
    elif record_type == "CNAME":
        return validate_hostname(value)
    return True

class DnsRecordCreate(BaseModel):
    domain: str
    record_type: str = "A"
    values: list[str] = [""]
    ttl: int = 300

class DnsRecordRead(BaseModel):
    id: int
    domain: str
    record_type: str
    values: list[str]
    ttl: int
    is_active: bool
    created_at: str
    updated_at: str

class DnsRecordUpdate(BaseModel):
    domain: str | None = None
    record_type: str | None = None
    values: list[str] | None = None
    ttl: int | None = None
    is_active: bool | None = None

@app.get("/api/dns", response_model=list[DnsRecordRead])
def list_dns_records(session: Session = Depends(get_session), auth: bool = Depends(require_admin_or_internal)):
    """List all DNS records."""
    records = session.exec(select(DnsRecord)).all()
    result = []
    for r in records:
        try:
            values = json.loads(r.values) if r.values else []
        except (json.JSONDecodeError, TypeError):
            values = []
        result.append(DnsRecordRead(
            id=r.id,
            domain=r.domain_name,
            record_type=r.record_type,
            values=values,
            ttl=r.ttl,
            is_active=r.is_active,
            created_at=r.created_at,
            updated_at=r.updated_at,
        ))
    return result

@app.post("/api/dns", response_model=DnsRecordRead, status_code=201)
def create_dns_record(body: DnsRecordCreate, session: Session = Depends(get_session), auth: bool = Depends(require_admin_or_internal)):
    """Create a new DNS record."""
    if not body.domain:
        raise HTTPException(status_code=400, detail="Domain is required")

    if body.record_type not in ["A", "CNAME"]:
        raise HTTPException(status_code=400, detail="Record type must be A or CNAME")

    # Validate values
    if body.record_type == "A":
        if not body.values or body.values == [""]:
            raise HTTPException(status_code=400, detail="At least one IP address is required for A records")
        for value in body.values:
            if not validate_value(value, "A"):
                raise HTTPException(status_code=400, detail=f"Invalid IP address: {value}")
    elif body.record_type == "CNAME":
        if not body.values or body.values == [""] or len(body.values) > 1:
            raise HTTPException(status_code=400, detail="CNAME records require exactly one hostname")
        if not validate_value(body.values[0], "CNAME"):
            raise HTTPException(status_code=400, detail=f"Invalid hostname: {body.values[0]}")

    # Check for duplicate domain
    existing = session.exec(select(DnsRecord).where(DnsRecord.domain_name == body.domain)).first()
    if existing:
        raise HTTPException(status_code=409, detail=f"DNS record for '{body.domain}' already exists")

    record = DnsRecord(
        domain_name=body.domain,
        record_type=body.record_type,
        values=json.dumps(body.values),
        ttl=body.ttl,
        is_active=True,
        created_at=dt.now().isoformat(),
        updated_at=dt.now().isoformat(),
    )
    session.add(record)
    session.commit()
    session.refresh(record)

    return DnsRecordRead(
        id=record.id,
        domain=record.domain_name,
        record_type=record.record_type,
        values=body.values,
        ttl=record.ttl,
        is_active=record.is_active,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )

@app.put("/api/dns/{record_id}", response_model=DnsRecordRead)
def update_dns_record(record_id: int, body: DnsRecordUpdate, session: Session = Depends(get_session), auth: bool = Depends(require_admin_or_internal)):
    """Update a DNS record."""
    record = session.exec(select(DnsRecord).where(DnsRecord.id == record_id)).first()
    if not record:
        raise HTTPException(status_code=404, detail="DNS record not found")

    if body.domain is not None:
        if body.domain != record.domain_name:
            existing = session.exec(select(DnsRecord).where(DnsRecord.domain_name == body.domain)).first()
            if existing:
                raise HTTPException(status_code=409, detail=f"DNS record for '{body.domain}' already exists")
        record.domain_name = body.domain

    if body.record_type is not None:
        if body.record_type not in ["A", "CNAME"]:
            raise HTTPException(status_code=400, detail="Record type must be A or CNAME")
        record.record_type = body.record_type

    if body.values is not None:
        if body.record_type == "A":
            if not body.values or body.values == [""]:
                raise HTTPException(status_code=400, detail="At least one IP address is required for A records")
            for value in body.values:
                if not validate_value(value, "A"):
                    raise HTTPException(status_code=400, detail=f"Invalid IP address: {value}")
        elif body.record_type == "CNAME":
            if not body.values or body.values == [""] or len(body.values) > 1:
                raise HTTPException(status_code=400, detail="CNAME records require exactly one hostname")
            if not validate_value(body.values[0], "CNAME"):
                raise HTTPException(status_code=400, detail=f"Invalid hostname: {body.values[0]}")
        record.values = json.dumps(body.values)

    if body.ttl is not None:
        record.ttl = body.ttl

    if body.is_active is not None:
        record.is_active = body.is_active

    record.updated_at = dt.now().isoformat()
    session.add(record)
    session.commit()
    session.refresh(record)

    values = json.loads(record.values) if record.values else []
    return DnsRecordRead(
        id=record.id,
        domain=record.domain_name,
        record_type=record.record_type,
        values=values,
        ttl=record.ttl,
        is_active=record.is_active,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )

@app.delete("/api/dns/{record_id}")
def delete_dns_record(record_id: int, session: Session = Depends(get_session), auth: bool = Depends(require_admin_or_internal)):
    """Delete a DNS record."""
    record = session.exec(select(DnsRecord).where(DnsRecord.id == record_id)).first()
    if not record:
        raise HTTPException(status_code=404, detail="DNS record not found")

    session.delete(record)
    session.commit()
    return {"status": "SUCCESS", "message": f"DNS record for '{record.domain_name}' deleted"}

# Every dashboard widget in display order with its default size. Mirrors
# `defaultWidgetDefs` in the UI's widgetStore: a key missing here is saved by
# the PUT but never read back, so the user's change reverts on the next load.
WIDGET_DEFAULT_SIZES: dict[str, str] = {
    'energy_insights': 'medium',
    'ambient_timer': 'medium',
    'quick_notes': 'medium',
    'active_media': 'wide',
    'chores_progress': 'tall',
    'upcoming_events': 'wide',
    'quick_assistant': 'medium',
    'device_control': 'tall',
    'workspaces': 'medium',
    'health_activity': 'medium',
    'health_today': 'medium',
    'climate': 'medium',
    'presence': 'medium',
    'bible_daily': 'medium',
    'recommendations': 'medium',
}
_WIDGET_ORDER = {key: index for index, key in enumerate(WIDGET_DEFAULT_SIZES)}
WIDGET_SIZES = frozenset({'small', 'medium', 'wide', 'tall'})
WIDGET_VISIBILITIES = frozenset({'visible', 'hidden', 'removed'})

#: Widgets a child sees before they touch the dashboard: chores, calendar,
#: music/media, notes, the verse of the day, their own suggestions and device
#: control for the devices they are assigned.
CHILD_WIDGETS = frozenset({
    'chores_progress', 'upcoming_events', 'active_media', 'quick_notes',
    'bible_daily', 'recommendations', 'device_control',
})
#: Teens get the child set plus climate control.
TEEN_WIDGETS = CHILD_WIDGETS | {'climate'}
#: Adults see everything except these: Workspaces is opt-in for anyone who is
#: not an admin, and the assistant is governed by its own enabled flag.
ADULT_HIDDEN_WIDGETS = frozenset({'workspaces', 'quick_assistant'})


def _default_widget_visibility(widget_key: str, user: User) -> str:
    """The default for one widget when the person has no saved row for it.

    Only a default: any row the person saves wins, and Workspaces can be added
    back from the dashboard's widget sheet by anyone.
    """
    if widget_key == 'quick_assistant':
        return 'hidden'
    if user.is_admin:
        return 'visible'
    role = user.family_role or 'other'
    if role == 'child':
        return 'visible' if widget_key in CHILD_WIDGETS else 'hidden'
    if role == 'teen':
        return 'visible' if widget_key in TEEN_WIDGETS else 'hidden'
    return 'hidden' if widget_key in ADULT_HIDDEN_WIDGETS else 'visible'


@app.get("/api/widgets/settings", response_model=WidgetSettingsRead)
def get_widget_settings(session: Session = Depends(get_session), user: User = Depends(require_api_key)):
    """Get all widget settings for the current user."""
    widgets = session.exec(select(UserWidget).where(UserWidget.username == user.username)).all()

    # Build settings dict keyed by widget_key
    settings_map = {}
    for w in widgets:
        parsed_pinned = []
        if w.pinned_devices:
            try:
                parsed_pinned = json.loads(w.pinned_devices)
                if not isinstance(parsed_pinned, list):
                    parsed_pinned = []
            except (json.JSONDecodeError, TypeError):
                parsed_pinned = []

        parsed_config = {}
        if w.config:
            try:
                parsed_config = json.loads(w.config)
                if not isinstance(parsed_config, dict):
                    parsed_config = {}
            except (json.JSONDecodeError, TypeError):
                parsed_config = {}

        settings_map[w.widget_key] = UserWidgetRead(
            widget_key=w.widget_key,
            visibility=w.visibility,
            order_index=w.order_index,
            size=w.size,
            is_pinned=w.is_pinned,
            sort_mode=w.sort_mode,
            pinned_devices=parsed_pinned,
            config=parsed_config,
            updated_at=w.updated_at,
        )

    # Return as list, ensure every known widget key has an entry
    result = []
    for index, key in enumerate(WIDGET_DEFAULT_SIZES):
        if key in settings_map:
            result.append(settings_map[key])
        else:
            result.append(UserWidgetRead(
                widget_key=key,
                visibility=_default_widget_visibility(key, user),
                order_index=index,
                size=WIDGET_DEFAULT_SIZES[key],
                is_pinned=False,
                sort_mode=None,
                pinned_devices=[],
                config={},
                updated_at=0,
            ))

    # Check if quick_assistant is enabled
    quick_assistant_enabled = any(
        w.widget_key == 'quick_assistant' and w.visibility == 'visible' for w in widgets
    )

    return {"widgets": result, "quick_assistant_enabled": quick_assistant_enabled}

@app.put("/api/widgets/settings/{widget_key}")
def update_widget_settings(
    widget_key: str,
    body: UserWidgetUpdate,
    session: Session = Depends(get_session),
    user: User = Depends(require_api_key)
):
    """Update widget settings for the current user."""
    if widget_key not in WIDGET_DEFAULT_SIZES:
        raise HTTPException(status_code=404, detail=f"Unknown widget '{widget_key}'")
    if body.size is not None and body.size not in WIDGET_SIZES:
        raise HTTPException(status_code=400, detail=f"Invalid widget size '{body.size}'")
    if body.visibility is not None and body.visibility not in WIDGET_VISIBILITIES:
        raise HTTPException(status_code=400, detail=f"Invalid widget visibility '{body.visibility}'")
    if body.quick_assistant_enabled is not None:
        setting = session.exec(
            select(UserWidget).where(
                UserWidget.username == user.username,
                UserWidget.widget_key == 'quick_assistant'
            )
        ).first()
        visibility = 'visible' if body.quick_assistant_enabled else 'hidden'
        if setting:
            setting.visibility = visibility
        else:
            setting = UserWidget(
                username=user.username,
                widget_key='quick_assistant',
                visibility=visibility,
                order_index=_WIDGET_ORDER['quick_assistant'],
                size=WIDGET_DEFAULT_SIZES['quick_assistant'],
                is_pinned=False,
                sort_mode=None,
                pinned_devices='[]',
                config='{}',
                updated_at=int(datetime.now().timestamp() * 1000),
            )
        session.add(setting)
        session.commit()
        return {"status": "SUCCESS"}

    existing = session.exec(
        select(UserWidget).where(
            UserWidget.username == user.username,
            UserWidget.widget_key == widget_key
        )
    ).first()

    # Not columns: `quick_assistant_enabled` is handled above, `user_id` is
    # never trusted (the row always belongs to the caller).
    update_data = body.model_dump(exclude_unset=True, exclude={'quick_assistant_enabled', 'user_id'})

    if not update_data:
        return {"status": "SUCCESS"}

    # Serialize JSON fields
    if 'pinned_devices' in update_data:
        update_data['pinned_devices'] = json.dumps(update_data['pinned_devices'])
    if 'config' in update_data:
        update_data['config'] = json.dumps(update_data['config'])

    # Creating a quick_assistant row without an explicit visibility must not
    # reveal the widget (model default is 'visible'). An existing row keeps its
    # visibility: resizing an enabled assistant must not switch it off.
    if widget_key == 'quick_assistant' and 'visibility' not in update_data and not existing:
        update_data['visibility'] = 'hidden'

    if existing:
        for key, value in update_data.items():
            setattr(existing, key, value)
        existing.updated_at = int(datetime.now().timestamp() * 1000)
        session.add(existing)
    else:
        # The first change to a widget creates its row. Fields the change does
        # not name keep the widget's defaults: the model's own defaults (size
        # 'medium', order 0) silently resized and reordered every widget the
        # first time anything about it was saved.
        update_data.setdefault('size', WIDGET_DEFAULT_SIZES.get(widget_key, 'medium'))
        update_data.setdefault('order_index', _WIDGET_ORDER.get(widget_key, len(_WIDGET_ORDER)))
        new_widget = UserWidget(
            username=user.username,
            widget_key=widget_key,
            **update_data,
        )
        session.add(new_widget)

    session.commit()
    return {"status": "SUCCESS"}

# Raven Missions (Autonomous Ops & User Tasks)

# Calendar Integration Settings (per-user)

@app.get("/api/calendar/settings")
def get_calendar_settings(
    session: Session = Depends(get_session),
    user: User = Depends(require_api_key),
):
    """Get the current user's calendar integration preferences."""
    row = session.exec(
        select(UserCalendarSetting).where(UserCalendarSetting.username == user.username)
    ).first()
    data = json.loads(row.data) if row and row.data else {}
    return {"status": "SUCCESS", "settings": data}


@app.put("/api/calendar/settings")
def update_calendar_settings(
    body: dict,
    session: Session = Depends(get_session),
    user: User = Depends(require_api_key),
):
    """Update the current user's calendar integration preferences.

    Accepted keys: default (integration name), disabled (list[str]),
    priority (dict[name->int]), ical_urls (list[str]), people (list of
    calendar-owner person records: {id, name, color, accounts}),
    reminder_minutes (int, minutes before an event its phone reminder goes
    out; 0 = off, unset = 30) and timezone (IANA name the reminders use;
    unset = the household's). Unknown keys are ignored; missing keys are
    preserved.
    """
    row = session.exec(
        select(UserCalendarSetting).where(UserCalendarSetting.username == user.username)
    ).first()
    if row is None:
        row = UserCalendarSetting(username=user.username, data={})
        session.add(row)

    data = json.loads(row.data) if (row and row.data) else {}
    allowed = {"default", "disabled", "priority", "ical_urls", "people", "reminder_minutes", "timezone"}
    if "reminder_minutes" in (body or {}):
        minutes = body["reminder_minutes"]
        if isinstance(minutes, bool) or not isinstance(minutes, int) or not 0 <= minutes <= 1440:
            raise HTTPException(status_code=422, detail="reminder_minutes must be a whole number from 0 to 1440")
    if (body or {}).get("timezone"):
        try:
            ZoneInfo(str(body["timezone"]))
        except (ZoneInfoNotFoundError, ValueError):
            raise HTTPException(status_code=422, detail=f"Unknown timezone: {body['timezone']}") from None
    for key, value in (body or {}).items():
        if key in allowed:
            data[key] = value
    row.data = json.dumps(data)
    session.add(row)
    session.commit()
    return {"status": "SUCCESS", "settings": data}


# Where each user left off in a workspace (private, per user + workspace)


# Workspace chats (one conversation per tab, owned by its user)

_CHAT_MAX_PARTS_BYTES = 512 * 1024
_CHAT_ROLES = ("user", "assistant")


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _own_chat(session: Session, user: User, chat_id: str) -> WorkspaceChat:
    chat = session.get(WorkspaceChat, chat_id)
    # Someone else's chat is "not found", not "forbidden": its existence is private too.
    if not chat or chat.username != user.username:
        raise HTTPException(status_code=404, detail="Chat not found")
    return chat


def _chat_summary(chat: WorkspaceChat, count: int | None = None) -> dict:
    data = {"id": chat.id, "workspace_id": chat.workspace_id, "title": chat.title,
            "created_at": chat.created_at, "updated_at": chat.updated_at}
    if count is not None:
        data["message_count"] = count
    return data


def _message_dict(m: WorkspaceChatMessage) -> dict:
    return {"id": m.id, "role": m.role, "parts": json.loads(m.parts or "[]"),
            "meta": json.loads(m.meta or "{}"), "created_at": m.created_at}


@app.get("/api/users/me/workspace-chats")
def list_workspace_chats(workspace_id: str, session: Session = Depends(get_session), user: User = Depends(require_api_key)):
    chats = session.exec(
        select(WorkspaceChat)
        .where(WorkspaceChat.username == user.username, WorkspaceChat.workspace_id == workspace_id)
        .order_by(WorkspaceChat.updated_at.desc())
        .limit(100)
    ).all()
    out = []
    for chat in chats:
        count = len(session.exec(select(WorkspaceChatMessage.id).where(WorkspaceChatMessage.chat_id == chat.id)).all())
        out.append(_chat_summary(chat, count))
    return {"chats": out}


@app.post("/api/users/me/workspace-chats")
def create_workspace_chat(body: dict, session: Session = Depends(get_session), user: User = Depends(require_api_key)):
    workspace_id = str(body.get("workspace_id") or "").strip()
    if not workspace_id:
        raise HTTPException(status_code=422, detail="workspace_id is required")
    now = _now_iso()
    chat = WorkspaceChat(
        id=uuid.uuid4().hex,
        workspace_id=workspace_id,
        username=user.username,
        title=(str(body.get("title") or "").strip() or "New chat")[:120],
        created_at=now,
        updated_at=now,
    )
    session.add(chat)
    session.commit()
    return _chat_summary(chat, 0)


@app.get("/api/users/me/workspace-chats/{chat_id}")
def get_workspace_chat(chat_id: str, session: Session = Depends(get_session), user: User = Depends(require_api_key)):
    chat = _own_chat(session, user, chat_id)
    messages = session.exec(
        select(WorkspaceChatMessage).where(WorkspaceChatMessage.chat_id == chat.id).order_by(WorkspaceChatMessage.id)
    ).all()
    return {**_chat_summary(chat, len(messages)), "messages": [_message_dict(m) for m in messages]}


@app.patch("/api/users/me/workspace-chats/{chat_id}")
def rename_workspace_chat(chat_id: str, body: dict, session: Session = Depends(get_session), user: User = Depends(require_api_key)):
    chat = _own_chat(session, user, chat_id)
    title = str(body.get("title") or "").strip()
    if not title:
        raise HTTPException(status_code=422, detail="title is required")
    chat.title = title[:120]
    chat.updated_at = _now_iso()
    session.add(chat)
    session.commit()
    return _chat_summary(chat)


@app.delete("/api/users/me/workspace-chats/{chat_id}")
def delete_workspace_chat(chat_id: str, session: Session = Depends(get_session), user: User = Depends(require_api_key)):
    chat = _own_chat(session, user, chat_id)
    for m in session.exec(select(WorkspaceChatMessage).where(WorkspaceChatMessage.chat_id == chat.id)).all():
        session.delete(m)
    session.delete(chat)
    session.commit()
    return {"status": "SUCCESS"}


@app.post("/api/users/me/workspace-chats/{chat_id}/messages")
def append_workspace_chat_message(chat_id: str, body: dict, session: Session = Depends(get_session), user: User = Depends(require_api_key)):
    chat = _own_chat(session, user, chat_id)
    role = str(body.get("role") or "")
    if role not in _CHAT_ROLES:
        raise HTTPException(status_code=422, detail="role must be user or assistant")
    parts = body.get("parts")
    if not isinstance(parts, list):
        raise HTTPException(status_code=422, detail="parts must be a list")
    encoded = json.dumps(parts)
    if len(encoded) > _CHAT_MAX_PARTS_BYTES:
        raise HTTPException(status_code=413, detail="message too large")
    message = WorkspaceChatMessage(
        chat_id=chat.id, role=role, parts=encoded, meta=json.dumps(body.get("meta") or {}), created_at=_now_iso()
    )
    chat.updated_at = message.created_at
    # The first thing asked names the chat, the way most chat apps title a thread.
    if role == "user" and chat.title == "New chat":
        first = next((p.get("text", "") for p in parts if isinstance(p, dict) and p.get("type") == "text"), "")
        if first.strip():
            chat.title = " ".join(first.split())[:60]
    session.add(message)
    session.add(chat)
    session.commit()
    session.refresh(message)
    return _message_dict(message)


@app.get("/api/users/me/workspace-sessions")
def get_workspace_sessions(
    session: Session = Depends(get_session),
    user: User = Depends(require_api_key),
):
    """Every workspace session this user has saved, keyed by workspace id.

    A row whose JSON cannot be parsed is skipped rather than failing the whole
    list: one corrupt entry should cost that entry, not the other workspaces.
    """
    rows = session.exec(
        select(UserWorkspaceSession).where(UserWorkspaceSession.username == user.username)
    ).all()
    sessions: dict[str, object] = {}
    for row in rows:
        try:
            sessions[row.workspace_id] = json.loads(row.data) if row.data else {}
        except (TypeError, ValueError):
            log.warning(
                f"[workspace-session] skipping unreadable entry for "
                f"{user.username}/{row.workspace_id}"
            )
    return {"status": "SUCCESS", "sessions": sessions}


@app.put("/api/users/me/workspace-sessions/{workspace_id}")
def put_workspace_session(
    workspace_id: str,
    body: dict,
    session: Session = Depends(get_session),
    user: User = Depends(require_api_key),
):
    """Save where this user left off in one workspace.

    The body is the session blob the client produced; it is stored whole
    because the client is the only thing that knows its shape and it validates
    every field again on the way back in.
    """
    row = session.exec(
        select(UserWorkspaceSession).where(
            UserWorkspaceSession.username == user.username,
            UserWorkspaceSession.workspace_id == workspace_id,
        )
    ).first()
    if row is None:
        row = UserWorkspaceSession(username=user.username, workspace_id=workspace_id, data="{}")
        session.add(row)
    row.data = json.dumps(body or {})
    row.updated_at = datetime.now(UTC).isoformat()
    session.add(row)
    session.commit()
    return {"status": "SUCCESS", "workspace_id": workspace_id}


@app.delete("/api/users/me/workspace-sessions/{workspace_id}")
def delete_workspace_session(
    workspace_id: str,
    session: Session = Depends(get_session),
    user: User = Depends(require_api_key),
):
    """Forget one workspace session for this user."""
    row = session.exec(
        select(UserWorkspaceSession).where(
            UserWorkspaceSession.username == user.username,
            UserWorkspaceSession.workspace_id == workspace_id,
        )
    ).first()
    if row is not None:
        session.delete(row)
        session.commit()
    return {"status": "SUCCESS", "workspace_id": workspace_id}


# Per-user website/widget theme (same pack schema as the web client)
@app.get("/api/users/me/theme")
def get_user_theme(
    session: Session = Depends(get_session),
    user: User = Depends(require_api_key),
):
    """Get the current user's website/widget theme preference."""
    row = session.exec(
        select(UserThemeSetting).where(UserThemeSetting.username == user.username)
    ).first()
    data = json.loads(row.data) if row and row.data else {}
    return {
        "status": "SUCCESS",
        "theme_id": data.get("theme_id") or "aurora",
        "packs": data.get("packs") or [],
    }


@app.put("/api/users/me/theme")
def update_user_theme(
    body: dict,
    session: Session = Depends(get_session),
    user: User = Depends(require_api_key),
):
    """Update theme_id and/or imported user theme packs for this user.

    Packs must satisfy the web schematic (kind=jarvis.health-theme-pack).
    Unknown keys are ignored; missing keys are preserved.
    """
    row = session.exec(
        select(UserThemeSetting).where(UserThemeSetting.username == user.username)
    ).first()
    if row is None:
        row = UserThemeSetting(username=user.username, data="{}")
        session.add(row)

    data = json.loads(row.data) if row and row.data else {}
    allowed = {"theme_id", "packs"}
    for key, value in (body or {}).items():
        if key not in allowed:
            continue
        if key == "theme_id":
            if not isinstance(value, str) or not value.strip():
                raise HTTPException(status_code=422, detail="theme_id must be a non-empty string")
            data["theme_id"] = value.strip()
        elif key == "packs":
            if not isinstance(value, list):
                raise HTTPException(status_code=422, detail="packs must be a list of theme packs")
            for pack in value:
                if not isinstance(pack, dict) or pack.get("kind") != "jarvis.health-theme-pack":
                    raise HTTPException(
                        status_code=422,
                        detail='each pack must be an object with kind "jarvis.health-theme-pack"',
                    )
                if pack.get("schemaVersion") != 1:
                    raise HTTPException(status_code=422, detail="pack.schemaVersion must be 1")
            data["packs"] = value
    row.data = json.dumps(data)
    session.add(row)
    session.commit()
    return {
        "status": "SUCCESS",
        "theme_id": data.get("theme_id") or "aurora",
        "packs": data.get("packs") or [],
    }


# Per-user opt-in activity sharing (private by default)

ACTIVITY_SHARE_SCOPES = {"totals", "workouts", "achievements", "bible"}
ACTIVITY_AUDIENCES = {"circle", "users"}


def _activity_sharing_data(row: UserActivitySharing | None) -> dict:
    raw = json.loads(row.data) if row and row.data else {}
    share = raw.get("share")
    if not isinstance(share, list):
        share = ["totals"]
    user_ids = raw.get("user_ids")
    if not isinstance(user_ids, list):
        user_ids = []
    return {
        "enabled": bool(raw.get("enabled", False)),
        "audience": raw.get("audience") if raw.get("audience") in ACTIVITY_AUDIENCES else "circle",
        "user_ids": [str(u).strip() for u in user_ids if str(u).strip()],
        "share": [s for s in share if s in ACTIVITY_SHARE_SCOPES],
    }


@app.get("/api/users/me/activity-sharing")
def get_activity_sharing(
    session: Session = Depends(get_session),
    user: User = Depends(require_api_key),
):
    """Current user's activity-sharing preference (defaults to off).

    A child or teen is projected as locked-on to the family circle even if
    they have no row yet, so the UI and the geo feed agree before the first
    save. Adult children and other adults keep the private default.
    """
    row = session.exec(
        select(UserActivitySharing).where(UserActivitySharing.username == user.username)
    ).first()
    data = _activity_sharing_data(row)
    if user.family_role in roles.MINOR_FAMILY_ROLES:
        data.update({"enabled": True, "audience": "circle", "user_ids": []})
    return {"status": "SUCCESS", "sharing_locked": user.family_role in roles.MINOR_FAMILY_ROLES, **data}


@app.put("/api/users/me/activity-sharing")
def update_activity_sharing(
    body: dict,
    session: Session = Depends(get_session),
    user: User = Depends(require_api_key),
):
    """Opt in/out of sharing activity. Nothing is visible to others until enabled."""
    row = session.exec(
        select(UserActivitySharing).where(UserActivitySharing.username == user.username)
    ).first()
    if row is None:
        row = UserActivitySharing(username=user.username, data="{}")
        session.add(row)

    data = _activity_sharing_data(row)
    minor = user.family_role in roles.MINOR_FAMILY_ROLES
    for key, value in (body or {}).items():
        if key == "enabled":
            if not isinstance(value, bool):
                raise HTTPException(status_code=422, detail="enabled must be a boolean")
            if minor and value is False:
                raise HTTPException(status_code=403, detail="A child or teen's family sharing cannot be turned off")
            data["enabled"] = value
        elif key == "audience":
            if value not in ACTIVITY_AUDIENCES:
                raise HTTPException(status_code=422, detail='audience must be "circle" or "users"')
            data["audience"] = value
        elif key == "user_ids":
            if not isinstance(value, list) or not all(isinstance(u, str) and u.strip() for u in value):
                raise HTTPException(status_code=422, detail="user_ids must be a list of usernames")
            data["user_ids"] = [u.strip() for u in value]
        elif key == "share":
            if (
                not isinstance(value, list)
                or not value
                or any(s not in ACTIVITY_SHARE_SCOPES for s in value)
            ):
                raise HTTPException(
                    status_code=422,
                    detail=f"share must be a non-empty subset of {sorted(ACTIVITY_SHARE_SCOPES)}",
                )
            data["share"] = sorted(set(value))
    if minor:
        data.update({"enabled": True, "audience": "circle", "user_ids": []})
    row.data = json.dumps(data)
    session.add(row)
    session.commit()
    return {"status": "SUCCESS", "sharing_locked": minor, **data}


def may_view_raw_data(viewer: User | None, subject: User | None) -> bool:
    """Whether ``viewer`` may see ``subject``'s collected raw data (phone usage,
    the activity stream) -- the Phase 11 visibility rule (docs/HEALTH_HUB.md).

    A minor's raw data is open to the parents and no one else: an admin, the
    father, and the linked mother (any mother when none is linked). The minor
    sees their suggestions, not the stream behind them, and siblings never see
    each other's. An adult's raw data is their own.
    """
    if viewer is None or subject is None:
        return False
    if (subject.family_role or "other") in roles.MINOR_FAMILY_ROLES:
        if viewer.username == subject.username:
            return False
        if viewer.is_admin or viewer.family_role == "father":
            return True
        if viewer.family_role == "mother":
            return not subject.mother_username or subject.mother_username == viewer.username
        return False
    return viewer.username == subject.username


def may_view_technical_data(viewer: User | None, subject: User | None) -> bool:
    """Whether ``viewer`` may also see the technical side of ``subject``'s data
    (devices, app versions, guard and permission states, sync times; the
    family member page, docs/FAMILY_MEMBER_PAGE.md).

    Narrower than :func:`may_view_raw_data`: of the people who may see a
    child's data, only an admin and the father see the technical side. The
    mother sees every personal detail and every control, without it. A member
    always sees their own.
    """
    if viewer is None or subject is None:
        return False
    if viewer.username == subject.username:
        return True
    return may_view_raw_data(viewer, subject) and (bool(viewer.is_admin) or viewer.family_role == "father")


# --- screen time: budgets, routines, more-time requests (Phase 11 step 4) ---

_HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
_WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


def _may_manage_screen_time(viewer: User | None, subject: User | None) -> bool:
    """Budgets are for minors, and set by their parents (the same rule as
    their collected data)."""
    return (
        subject is not None
        and (subject.family_role or "other") in roles.MINOR_FAMILY_ROLES
        and may_view_raw_data(viewer, subject)
    )


def _may_edit_screen_time(viewer: User | None, subject: User | None) -> bool:
    """Who may change a member's screen-time rules: a minor's parents, or a
    grown member themselves. A grown member's own phone is theirs to manage
    (they may clear their rules and turn monitoring off); they may not touch
    anyone else's, and a minor still may not touch their own."""
    if subject is not None and (subject.family_role or "other") not in roles.MINOR_FAMILY_ROLES:
        return viewer is not None and viewer.username == subject.username
    return _may_manage_screen_time(viewer, subject)


def _screen_subject(session: Session, username: str) -> User:
    subject = session.exec(select(User).where(User.username == username.strip().lower())).first()
    if subject is None:
        raise HTTPException(status_code=404, detail="No such member")
    return subject


def _screen_rules(session: Session, username: str) -> dict:
    row = session.get(ScreenRules, username)
    try:
        return json.loads(row.data) if row and row.data else {}
    except ValueError:
        return {}


def _save_screen_rules(session: Session, username: str, rules: dict, by: str) -> None:
    """Replace a member's rules (the caller commits)."""
    row = session.get(ScreenRules, username) or ScreenRules(username=username)
    row.data = json.dumps(rules)
    row.updated_by = by
    row.updated_at = datetime.now(UTC).isoformat()
    session.add(row)


def _visible_requests(session: Session, caller: User, rows: list) -> list:
    """Of a list of requests (more time, ask-a-parent), the caller's own and
    those of the children they manage, newest first, at most 100."""
    people = {u.username: u for u in session.exec(select(User)).all()}
    visible = [
        r for r in rows
        if r.username == caller.username or _may_manage_screen_time(caller, people.get(r.username))
    ]
    visible.sort(key=lambda r: r.created_at, reverse=True)
    return visible[:100]


def _granted_minutes(session: Session, username: str, day: str) -> int:
    rows = session.exec(
        select(ScreenTimeRequest).where(
            ScreenTimeRequest.username == username,
            ScreenTimeRequest.day == day,
            ScreenTimeRequest.status == "granted",
        )
    ).all()
    return sum(r.granted_minutes for r in rows)


# An Android package name: dotted segments of letters, digits and underscores.
_PACKAGE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z0-9_]+)+$")
# A domain the web filter matches (and every name under it).
_DOMAIN = re.compile(r"^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}\.?$")


def _clean_screen_rules(body: dict) -> dict:
    """Only known keys, in range; a bad value is a 422, never silently kept."""
    out: dict = {}
    for key in ("daily_limit_min", "weekend_limit_min"):
        if body.get(key) is not None:
            value = body[key]
            if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= 1440:
                raise HTTPException(status_code=422, detail=f"{key} must be whole minutes from 0 to 1440")
            out[key] = value
    # The weekly schedule (Family Link's "a limit per day"): {weekday: minutes},
    # each day overriding the daily and weekend limits.
    if body.get("day_limits") is not None:
        days = body["day_limits"]
        if not isinstance(days, dict) or any(
            d not in _WEEKDAYS or not isinstance(v, int) or isinstance(v, bool) or not 0 <= v <= 1440 for d, v in days.items()
        ):
            raise HTTPException(status_code=422, detail="day_limits must map mon..sun to whole minutes from 0 to 1440")
        out["day_limits"] = {d: days[d] for d in _WEEKDAYS if d in days}
    for key in ("bedtime_start", "bedtime_end", "school_start", "school_end"):
        if body.get(key):
            if not isinstance(body[key], str) or not _HHMM.match(body[key]):
                raise HTTPException(status_code=422, detail=f"{key} must be HH:MM")
            out[key] = body[key]
    if body.get("school_days") is not None:
        days = body["school_days"]
        if not isinstance(days, list) or any(d not in _WEEKDAYS for d in days):
            raise HTTPException(status_code=422, detail="school_days must be mon..sun")
        out["school_days"] = list(dict.fromkeys(days))
    if body.get("always_allowed") is not None:
        apps = body["always_allowed"]
        if not isinstance(apps, list) or any(not isinstance(a, str) or not a.strip() for a in apps):
            raise HTTPException(status_code=422, detail="always_allowed must be package names")
        out["always_allowed"] = [a.strip() for a in apps][:50]
    # App control (step 5): apps blocked outright, and per-app daily minutes.
    if body.get("blocked_apps") is not None:
        apps = body["blocked_apps"]
        if not isinstance(apps, list) or any(not isinstance(a, str) or not _PACKAGE.match(a.strip()) for a in apps):
            raise HTTPException(status_code=422, detail="blocked_apps must be package names")
        out["blocked_apps"] = list(dict.fromkeys(a.strip() for a in apps))[:200]
    if body.get("app_limits") is not None:
        limits = body["app_limits"]
        if not isinstance(limits, dict) or any(
            not isinstance(k, str) or not _PACKAGE.match(k.strip())
            or not isinstance(v, int) or isinstance(v, bool) or not 0 <= v <= 1440
            for k, v in limits.items()
        ):
            raise HTTPException(status_code=422, detail="app_limits must map package names to whole minutes from 0 to 1440")
        out["app_limits"] = {k.strip(): v for k, v in list(limits.items())[:200]}
    # The phone's web filter (step 5): on/off, the family-safe resolver for adult
    # and malware sites, safe search, and the parent's own block and allow lists.
    if body.get("web_filter") is not None:
        wf = body["web_filter"]
        if not isinstance(wf, dict):
            raise HTTPException(status_code=422, detail="web_filter must be an object")
        clean: dict = {}
        for key in ("enabled", "adult", "safe_search"):
            if wf.get(key) is not None:
                if not isinstance(wf[key], bool):
                    raise HTTPException(status_code=422, detail=f"web_filter.{key} must be true or false")
                clean[key] = wf[key]
        for key in ("block", "allow"):
            if wf.get(key) is not None:
                domains = wf[key]
                if not isinstance(domains, list) or any(not isinstance(d, str) or not _DOMAIN.match(d.strip().lower()) for d in domains):
                    raise HTTPException(status_code=422, detail=f"web_filter.{key} must be domain names")
                clean[key] = list(dict.fromkeys(d.strip().lower().rstrip(".") for d in domains))[:500]
        # Sites a parent allowed for a while ("ask a parent"): {domain: ISO end}.
        # Ones that already ran out are dropped here, so the list stays short.
        if wf.get("allow_until") is not None:
            until = wf["allow_until"]
            if not isinstance(until, dict) or any(
                not isinstance(d, str) or not _DOMAIN.match(d.strip().lower()) or not isinstance(v, str) for d, v in until.items()
            ):
                raise HTTPException(status_code=422, detail="web_filter.allow_until must map domain names to ISO times")
            now = datetime.now(UTC)
            kept: dict = {}
            for d, v in until.items():
                try:
                    ends = datetime.fromisoformat(v)
                except ValueError as exc:
                    raise HTTPException(status_code=422, detail="web_filter.allow_until must map domain names to ISO times") from exc
                if (ends if ends.tzinfo else ends.replace(tzinfo=UTC)) > now:
                    kept[d.strip().lower().rstrip(".")] = v
            clean["allow_until"] = kept
        out["web_filter"] = clean
    # Install approvals (step 5): on unless a parent switches them off.
    if body.get("install_approval") is not None:
        if not isinstance(body["install_approval"], bool):
            raise HTTPException(status_code=422, detail="install_approval must be true or false")
        out["install_approval"] = body["install_approval"]
    # Permissions a new grant of which waits for a parent (camera, location, ...).
    if body.get("permission_gating") is not None:
        groups = body["permission_gating"]
        if not isinstance(groups, list) or any(g not in PERMISSION_GROUPS for g in groups):
            raise HTTPException(status_code=422, detail=f"permission_gating must be among {', '.join(PERMISSION_GROUPS)}")
        out["permission_gating"] = list(dict.fromkeys(groups))
    # Content monitoring (step 6): what pages a parent at once.
    if body.get("monitoring") is not None:
        if body["monitoring"] not in ("all", "severe", "off"):
            raise HTTPException(status_code=422, detail="monitoring must be all, severe or off")
        out["monitoring"] = body["monitoring"]
    return out


def _parents_of(session: Session, subject: User) -> list[str]:
    """Who hears a child's request: the people the rule lets manage them."""
    return [u.username for u in session.exec(select(User)).all() if _may_manage_screen_time(u, subject)]


@app.get("/api/screen-time/{username}/rules")
def get_screen_rules(username: str, day: str = "", session: Session = Depends(get_session), caller: User = Depends(require_api_key)):
    """A member's rules: theirs to read, their parents' to read and change."""
    subject = _screen_subject(session, username)
    if caller.username != subject.username and not _may_manage_screen_time(caller, subject):
        raise HTTPException(status_code=403, detail="Only a parent can see this")
    return {
        "username": subject.username,
        "rules": _screen_rules(session, subject.username),
        "bonus_min": _granted_minutes(session, subject.username, day) if day else 0,
        # A grown member manages their own rules; a minor's are their parents'.
        "can_manage": _may_edit_screen_time(caller, subject),
        # Whether the caller sees the technical side (the gateway strips it
        # for the mother; docs/FAMILY_MEMBER_PAGE.md).
        "technical": may_view_technical_data(caller, subject),
        # A minor's phone keeps Jarvis protected (no uninstall, no switching
        # the guard off) whether or not any time rules are set.
        "protected": (subject.family_role or "other") in roles.MINOR_FAMILY_ROLES,
        # The parents' answers on new apps and permissions, for the phone's guard.
        "approvals": _guard_approvals(session, subject.username),
    }


@app.put("/api/screen-time/{username}/rules")
def put_screen_rules(username: str, body: dict, session: Session = Depends(get_session), caller: User = Depends(require_api_key)):
    subject = _screen_subject(session, username)
    if not _may_edit_screen_time(caller, subject):
        raise HTTPException(status_code=403, detail="Only a parent can set a minor's screen time")
    rules = _clean_screen_rules(body or {})
    current = _screen_rules(session, subject.username)
    for key in ("paused", "paused_until"):  # a pause survives a rules edit
        if key in current:
            rules[key] = current[key]
    _save_screen_rules(session, subject.username, rules, caller.username)
    session.commit()
    return {"username": subject.username, "rules": rules}


@app.post("/api/screen-time/{username}/pause")
def pause_screen_time(username: str, body: dict, session: Session = Depends(get_session), caller: User = Depends(require_api_key)):
    """Lock the phone now: ``{"until": ISO}`` for a while, ``{}`` until lifted,
    ``{"resume": true}`` to lift it."""
    subject = _screen_subject(session, username)
    if not _may_edit_screen_time(caller, subject):
        raise HTTPException(status_code=403, detail="Only a parent can pause a minor's phone")
    rules = _screen_rules(session, subject.username)
    rules.pop("paused", None)
    rules.pop("paused_until", None)
    if not (body or {}).get("resume"):
        until = (body or {}).get("until")
        if until:
            try:
                datetime.fromisoformat(str(until))
            except ValueError as exc:
                raise HTTPException(status_code=422, detail="until must be an ISO time") from exc
            rules["paused_until"] = str(until)
        else:
            rules["paused"] = True
    _save_screen_rules(session, subject.username, rules, caller.username)
    session.commit()
    return {"username": subject.username, "rules": rules}


def _request_read(r: ScreenTimeRequest) -> dict:
    return {
        "id": r.id, "username": r.username, "day": r.day, "minutes": r.minutes, "note": r.note,
        "status": r.status, "granted_minutes": r.granted_minutes, "decided_by": r.decided_by,
        "created_at": r.created_at, "decided_at": r.decided_at,
    }


@app.post("/api/screen-time/requests")
def ask_for_screen_time(body: dict, session: Session = Depends(get_session), caller: User = Depends(require_api_key)):
    """A member asks for more time today; the answer names who to tell."""
    minutes = (body or {}).get("minutes")
    day = str((body or {}).get("day") or "")
    if not isinstance(minutes, int) or isinstance(minutes, bool) or not 5 <= minutes <= 240:
        raise HTTPException(status_code=422, detail="minutes must be 5 to 240")
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", day):
        raise HTTPException(status_code=422, detail="day must be YYYY-MM-DD")
    pending = session.exec(
        select(ScreenTimeRequest).where(
            ScreenTimeRequest.username == caller.username,
            ScreenTimeRequest.day == day,
            ScreenTimeRequest.status == "pending",
        )
    ).first()
    if pending is not None:
        raise HTTPException(status_code=409, detail="You already asked; a parent will answer soon")
    request = ScreenTimeRequest(
        username=caller.username, day=day, minutes=minutes, note=str((body or {}).get("note") or "")[:200]
    )
    session.add(request)
    session.commit()
    session.refresh(request)
    return {"request": _request_read(request), "notify": _parents_of(session, caller)}


@app.get("/api/screen-time/requests")
def list_screen_time_requests(status: str = "pending", session: Session = Depends(get_session), caller: User = Depends(require_api_key)):
    """The caller's own requests and those of the children they manage."""
    rows = session.exec(select(ScreenTimeRequest).where(ScreenTimeRequest.status == status)).all()
    return {"requests": [_request_read(r) for r in _visible_requests(session, caller, rows)]}


@app.post("/api/screen-time/requests/{request_id}")
def decide_screen_time_request(request_id: int, body: dict, session: Session = Depends(get_session), caller: User = Depends(require_api_key)):
    """A parent grants (optionally fewer minutes) or declines a request."""
    request = session.get(ScreenTimeRequest, request_id)
    if request is None:
        raise HTTPException(status_code=404, detail="No such request")
    subject = _screen_subject(session, request.username)
    if not _may_manage_screen_time(caller, subject):
        raise HTTPException(status_code=403, detail="Only a parent can answer")
    if request.status != "pending":
        raise HTTPException(status_code=409, detail="Already answered")
    grant = bool((body or {}).get("grant"))
    minutes = (body or {}).get("minutes", request.minutes)
    if grant and (not isinstance(minutes, int) or isinstance(minutes, bool) or not 1 <= minutes <= 240):
        raise HTTPException(status_code=422, detail="minutes must be 1 to 240")
    request.status = "granted" if grant else "declined"
    request.granted_minutes = minutes if grant else 0
    request.decided_by = caller.username
    request.decided_at = datetime.now(UTC).isoformat()
    session.add(request)
    session.commit()
    return {"request": _request_read(request), "notify": [request.username]}


@app.post("/api/screen-time/{username}/bonus")
def give_screen_time_bonus(username: str, body: dict, session: Session = Depends(get_session), caller: User = Depends(require_api_key)):
    """A parent adds minutes to today without being asked (Family Link's bonus
    time); a grown member may add their own."""
    subject = _screen_subject(session, username)
    if not _may_edit_screen_time(caller, subject):
        raise HTTPException(status_code=403, detail="Only a parent can add a minor's time")
    minutes = (body or {}).get("minutes")
    day = str((body or {}).get("day") or "")
    if not isinstance(minutes, int) or isinstance(minutes, bool) or not 1 <= minutes <= 240:
        raise HTTPException(status_code=422, detail="minutes must be 1 to 240")
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", day):
        raise HTTPException(status_code=422, detail="day must be YYYY-MM-DD")
    now = datetime.now(UTC).isoformat()
    bonus = ScreenTimeRequest(
        username=subject.username, day=day, minutes=minutes, note="Bonus time from a parent",
        status="granted", granted_minutes=minutes, decided_by=caller.username, decided_at=now,
    )
    session.add(bonus)
    session.commit()
    session.refresh(bonus)
    return {"request": _request_read(bonus), "notify": [subject.username]}


# --- asking a parent first: new apps, permissions, blocked sites (Phase 11 step 5) ---

GUARD_REQUEST_KINDS = ("app", "permission", "site")


def _guard_request_read(r: GuardRequest) -> dict:
    return {
        "id": r.id, "username": r.username, "kind": r.kind, "target": r.target, "label": r.label,
        "note": r.note, "status": r.status, "until": r.until, "decided_by": r.decided_by,
        "created_at": r.created_at, "decided_at": r.decided_at,
    }


def _guard_approvals(session: Session, username: str) -> dict:
    """The latest answer per app and per app permission: {"apps": {pkg: status},
    "permissions": {"pkg|group": status}}. Pending counts as an answer too, so
    the phone knows it already asked."""
    rows = session.exec(
        select(GuardRequest).where(GuardRequest.username == username, GuardRequest.kind.in_(("app", "permission")))
    ).all()
    out: dict = {"apps": {}, "permissions": {}}
    for r in sorted(rows, key=lambda r: (r.created_at, r.id or 0)):
        out["apps" if r.kind == "app" else "permissions"][r.target] = r.status
    return out


def _clean_guard_target(kind: str, target: str) -> str:
    target = target.strip()
    if kind == "app":
        if not _PACKAGE.match(target):
            raise HTTPException(status_code=422, detail="target must be an app package")
        return target
    if kind == "permission":
        pkg, _, group = target.partition("|")
        if not _PACKAGE.match(pkg) or group not in PERMISSION_GROUPS:
            raise HTTPException(status_code=422, detail="target must be package|permission group")
        return f"{pkg}|{group}"
    domain = target.lower().rstrip(".")
    domain = domain[4:] if domain.startswith("www.") else domain
    if not _DOMAIN.match(domain):
        raise HTTPException(status_code=422, detail="target must be a domain name")
    return domain


@app.post("/api/guard/requests")
def ask_a_parent(body: dict, session: Session = Depends(get_session), caller: User = Depends(require_api_key)):
    """A child asks a parent first: ``{"kind": "app"|"permission"|"site", "target", "label"?, "note"?}``.

    The phone asks again after a restart, so a request already waiting is
    returned as it is (``notify`` empty: the parents heard the first time), and
    an app or permission a parent already answered is not asked again.
    """
    kind = str((body or {}).get("kind") or "")
    if kind not in GUARD_REQUEST_KINDS:
        raise HTTPException(status_code=422, detail="kind must be app, permission or site")
    target = _clean_guard_target(kind, str((body or {}).get("target") or ""))
    parents = _parents_of(session, caller)
    if not parents:
        raise HTTPException(status_code=409, detail="There is no parent to ask")
    existing = session.exec(
        select(GuardRequest).where(
            GuardRequest.username == caller.username, GuardRequest.kind == kind, GuardRequest.target == target
        )
    ).all()
    latest = max(existing, key=lambda r: (r.created_at, r.id or 0)) if existing else None
    if latest is not None and (latest.status == "pending" or kind != "site"):
        return {"request": _guard_request_read(latest), "notify": []}
    request = GuardRequest(
        username=caller.username,
        kind=kind,
        target=target,
        label=str((body or {}).get("label") or "")[:120],
        note=str((body or {}).get("note") or "")[:200],
    )
    session.add(request)
    session.commit()
    session.refresh(request)
    return {"request": _guard_request_read(request), "notify": parents}


@app.get("/api/guard/requests")
def list_guard_requests(status: str = "pending", session: Session = Depends(get_session), caller: User = Depends(require_api_key)):
    """The caller's own requests and those of the children they manage."""
    if status not in ("pending", "approved", "denied"):
        raise HTTPException(status_code=422, detail="status must be pending, approved or denied")
    rows = session.exec(select(GuardRequest).where(GuardRequest.status == status)).all()
    return {"requests": [_guard_request_read(r) for r in _visible_requests(session, caller, rows)]}


@app.post("/api/guard/requests/{request_id}")
def decide_guard_request(request_id: int, body: dict, session: Session = Depends(get_session), caller: User = Depends(require_api_key)):
    """A parent answers: ``{"approve": bool}``; a site may add ``{"minutes": n}``
    (allowed for a while) or ``{"always": true}`` (added to the allow list)."""
    request = session.get(GuardRequest, request_id)
    if request is None:
        raise HTTPException(status_code=404, detail="No such request")
    subject = _screen_subject(session, request.username)
    if not _may_manage_screen_time(caller, subject):
        raise HTTPException(status_code=403, detail="Only a parent can answer")
    if request.status != "pending":
        raise HTTPException(status_code=409, detail="Already answered")
    approve = bool((body or {}).get("approve"))
    now = datetime.now(UTC)
    if approve and request.kind == "site":
        minutes = (body or {}).get("minutes")
        always = bool((body or {}).get("always"))
        if not always and (not isinstance(minutes, int) or isinstance(minutes, bool) or not 1 <= minutes <= 1440):
            raise HTTPException(status_code=422, detail="Allow a site for 1 to 1440 minutes, or always")
        rules = _screen_rules(session, subject.username)
        wf = dict(rules.get("web_filter") or {})
        until = dict(wf.get("allow_until") or {})
        if always:
            wf["allow"] = list(dict.fromkeys([*(wf.get("allow") or []), request.target]))
            wf["block"] = [d for d in wf.get("block") or [] if d != request.target]
            until.pop(request.target, None)
        else:
            request.until = (now + timedelta(minutes=minutes)).isoformat()
            until[request.target] = request.until
        wf["allow_until"] = until
        rules["web_filter"] = wf
        _save_screen_rules(session, subject.username, rules, caller.username)
    request.status = "approved" if approve else "denied"
    request.decided_by = caller.username
    request.decided_at = now.isoformat()
    session.add(request)
    session.commit()
    return {"request": _guard_request_read(request), "notify": [request.username]}


# --- linked outside accounts: Spotify's recently played (Phase 11, music everywhere) ---

LINK_PROVIDERS = ("spotify",)


def _linked_read(row: LinkedAccount | None) -> dict:
    """What the member sees about their own link (never the tokens)."""
    if row is None:
        return {"linked": False}
    return {
        "linked": True, "account_name": row.account_name, "linked_at": row.linked_at,
        "last_sync_at": row.last_sync_at, "last_error": row.last_error,
    }


def _link_provider(provider: str) -> str:
    if provider not in LINK_PROVIDERS:
        raise HTTPException(status_code=404, detail="No such provider")
    return provider


@app.get("/api/linked-accounts/{provider}")
def get_my_linked_account(provider: str, session: Session = Depends(get_session), caller: User = Depends(require_api_key)):
    """The caller's own link: linked or not, the account's name, the last sync."""
    return _linked_read(session.get(LinkedAccount, (caller.username, _link_provider(provider))))


@app.delete("/api/linked-accounts/{provider}")
def unlink_my_account(provider: str, session: Session = Depends(get_session), caller: User = Depends(require_api_key)):
    """Unlink: the tokens are deleted and nothing more is read."""
    row = session.get(LinkedAccount, (caller.username, _link_provider(provider)))
    if row is not None:
        session.delete(row)
        session.commit()
    return {"linked": False}


@app.put("/api/internal/linked-accounts/{provider}/{username}")
def store_linked_account(provider: str, username: str, body: dict, session: Session = Depends(get_session), _: None = Depends(require_internal)):
    """Internal (the gateway's OAuth callback): keep a member's new tokens, encrypted."""
    subject = _screen_subject(session, username)
    if not (body or {}).get("access_token") or not (body or {}).get("refresh_token"):
        raise HTTPException(status_code=422, detail="access_token and refresh_token required")
    row = session.get(LinkedAccount, (subject.username, _link_provider(provider))) or LinkedAccount(
        username=subject.username, provider=provider
    )
    row.access_token_enc = encrypt(str(body["access_token"]))
    row.refresh_token_enc = encrypt(str(body["refresh_token"]))
    row.expires_at = int(body.get("expires_at") or 0)
    row.scope = str(body.get("scope") or "")
    row.account_id = str(body.get("account_id") or "")[:120]
    row.account_name = str(body.get("account_name") or "")[:120]
    row.linked_at = datetime.now(UTC).isoformat()
    row.last_error = None
    session.add(row)
    session.commit()
    return _linked_read(row)


@app.get("/api/internal/linked-accounts/{provider}")
def list_linked_accounts(provider: str, session: Session = Depends(get_session), _: None = Depends(require_internal)):
    """Internal (telemetry's sync): every link of this provider, with its tokens."""
    rows = session.exec(select(LinkedAccount).where(LinkedAccount.provider == _link_provider(provider))).all()
    return {"accounts": [
        {
            "username": r.username, "access_token": decrypt(r.access_token_enc), "refresh_token": decrypt(r.refresh_token_enc),
            "expires_at": r.expires_at, "cursor_ms": r.cursor_ms,
        }
        for r in rows
    ]}


@app.patch("/api/internal/linked-accounts/{provider}/{username}")
def update_linked_account(provider: str, username: str, body: dict, session: Session = Depends(get_session), _: None = Depends(require_internal)):
    """Internal (telemetry's sync): a refreshed token, the new cursor, the sync's outcome."""
    row = session.get(LinkedAccount, (username.strip().lower(), _link_provider(provider)))
    if row is None:
        raise HTTPException(status_code=404, detail="Not linked")
    body = body or {}
    if body.get("access_token"):
        row.access_token_enc = encrypt(str(body["access_token"]))
        row.expires_at = int(body.get("expires_at") or 0)
    if body.get("refresh_token"):
        row.refresh_token_enc = encrypt(str(body["refresh_token"]))
    if body.get("cursor_ms") is not None:
        row.cursor_ms = max(row.cursor_ms, int(body["cursor_ms"]))
    if "last_error" in body:
        row.last_error = (str(body["last_error"])[:300] or None) if body["last_error"] else None
    if body.get("synced"):
        row.last_sync_at = datetime.now(UTC).isoformat()
    session.add(row)
    session.commit()
    return _linked_read(row)


RECOMMENDATION_LANES = ("music", "video", "podcast", "book", "game", "activity")


def _blocked_lanes(session: Session, username: str) -> list[str]:
    row = session.get(RecommendationLanes, username)
    try:
        data = json.loads(row.data) if row and row.data else {}
    except ValueError:
        data = {}
    return [lane for lane in data.get("blocked") or [] if lane in RECOMMENDATION_LANES]


@app.get("/api/recommendation-lanes/{username}")
def get_recommendation_lanes(username: str, session: Session = Depends(get_session), caller: User = Depends(require_api_key)):
    """Which suggestion lanes are on for a member: theirs to read; a minor's
    are their parents' to change, a grown member's their own."""
    subject = _screen_subject(session, username)
    if caller.username != subject.username and not _may_manage_screen_time(caller, subject):
        raise HTTPException(status_code=403, detail="Only a parent can see this")
    blocked = _blocked_lanes(session, subject.username)
    return {
        "username": subject.username,
        "lanes": {lane: lane not in blocked for lane in RECOMMENDATION_LANES},
        # A grown member manages their own lanes; a minor's are their parents'.
        "can_manage": _may_edit_screen_time(caller, subject),
    }


@app.put("/api/recommendation-lanes/{username}")
def put_recommendation_lanes(username: str, body: dict, session: Session = Depends(get_session), caller: User = Depends(require_api_key)):
    """``{"lanes": {"game": false, ...}}``: false blocks a lane, true blesses it.

    A minor's lanes are set by their parents; a grown member may set their own
    (the same rule as their screen-time rules, ``_may_edit_screen_time``)."""
    subject = _screen_subject(session, username)
    if not _may_edit_screen_time(caller, subject):
        raise HTTPException(status_code=403, detail="Only a parent can change suggestions")
    lanes = (body or {}).get("lanes")
    if not isinstance(lanes, dict) or any(k not in RECOMMENDATION_LANES or not isinstance(v, bool) for k, v in lanes.items()):
        raise HTTPException(status_code=422, detail="lanes must map music/video/podcast/book/game/activity to true or false")
    blocked = set(_blocked_lanes(session, subject.username))
    for lane, on in lanes.items():
        if on:
            blocked.discard(lane)
        else:
            blocked.add(lane)
    row = session.get(RecommendationLanes, subject.username) or RecommendationLanes(username=subject.username)
    row.data = json.dumps({"blocked": sorted(blocked)})
    row.updated_by = caller.username
    row.updated_at = datetime.now(UTC).isoformat()
    session.add(row)
    session.commit()
    return {"username": subject.username, "lanes": {lane: lane not in blocked for lane in RECOMMENDATION_LANES}}


@app.get("/api/internal/recommendation-lanes/{username}")
def internal_recommendation_lanes(username: str, session: Session = Depends(get_session), _: None = Depends(require_internal)):
    """Internal: the lanes a member's parents blocked (the gateway skips them)."""
    return {"blocked": _blocked_lanes(session, username.strip().lower())}


# Wrong parent logins typed on a child's phone, per child: five within ten
# minutes lock the unlock screen for ten minutes, so a password cannot be
# guessed at the speed of a kid's thumbs.
_UNLOCK_FAILURES: dict[str, list[float]] = {}
_UNLOCK_MAX_FAILURES = 5
_UNLOCK_WINDOW_S = 600
PARENT_UNLOCK_MINUTES = 10


@app.post("/api/guard/parent-unlock")
def guard_parent_unlock(body: dict, session: Session = Depends(get_session), caller: User = Depends(require_api_key)):
    """A parent signs in on their child's phone to lift Jarvis's protection briefly.

    The caller is the phone's signed-in child; the parent's own username and
    password are checked, and they must be one of this child's parents.
    """
    import time as _time

    now = _time.monotonic()
    recent = [t for t in _UNLOCK_FAILURES.get(caller.username, []) if now - t < _UNLOCK_WINDOW_S]
    _UNLOCK_FAILURES[caller.username] = recent
    if len(recent) >= _UNLOCK_MAX_FAILURES:
        raise HTTPException(status_code=429, detail="Too many tries. Wait ten minutes, then try again.")
    username = str((body or {}).get("username") or "").strip().lower()
    password = str((body or {}).get("password") or "")
    parent = session.exec(select(User).where(User.username == username)).first() if username else None
    ok = bool(
        parent
        and parent.password_hash
        and verify_password(password, parent.password_hash)
        and _may_manage_screen_time(parent, caller)
    )
    if not ok:
        recent.append(now)
        raise HTTPException(status_code=401, detail="That is not one of your parents' logins.")
    _UNLOCK_FAILURES.pop(caller.username, None)
    until = datetime.now(UTC) + timedelta(minutes=PARENT_UNLOCK_MINUTES)
    parents = _parents_of(session, caller)
    # Only after a real parent login: the phone keeps these to check an
    # offline code later. A child's own login never receives them.
    offline = [
        {"parent": row.username, "salt": row.salt, "iterations": row.iterations, "hash": row.hash}
        for row in session.exec(select(GuardOfflineCode).where(GuardOfflineCode.username.in_(parents))).all()
    ] if parents else []
    return {"ok": True, "parent": parent.username, "until": until.isoformat(), "notify": parents, "offline_codes": offline}


OFFLINE_CODE_ITERATIONS = 120_000
_OFFLINE_CODE = re.compile(r"^\d{6,12}$")


def _is_a_parent(session: Session, caller: User) -> bool:
    return any(_may_manage_screen_time(caller, u) for u in session.exec(select(User)).all() if u.username != caller.username)


@app.get("/api/guard/offline-code")
def get_offline_code(session: Session = Depends(get_session), caller: User = Depends(require_api_key)):
    """Whether the caller has an offline unlock code set (never the code)."""
    row = session.get(GuardOfflineCode, caller.username)
    return {"set": row is not None, "updated_at": row.updated_at if row else None, "can_set": _is_a_parent(session, caller)}


@app.put("/api/guard/offline-code")
def put_offline_code(body: dict, session: Session = Depends(get_session), caller: User = Depends(require_api_key)):
    """A parent sets their offline unlock code: 6 to 12 digits.

    Phones learn the new code at the next online parent unlock on them.
    """
    if not _is_a_parent(session, caller):
        raise HTTPException(status_code=403, detail="Only a parent sets an offline unlock code")
    code = str((body or {}).get("code") or "")
    if not _OFFLINE_CODE.match(code):
        raise HTTPException(status_code=422, detail="The code must be 6 to 12 digits")
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", code.encode(), salt, OFFLINE_CODE_ITERATIONS)
    row = session.get(GuardOfflineCode, caller.username) or GuardOfflineCode(
        username=caller.username, salt="", iterations=OFFLINE_CODE_ITERATIONS, hash=""
    )
    row.salt, row.iterations, row.hash = salt.hex(), OFFLINE_CODE_ITERATIONS, digest.hex()
    row.updated_at = datetime.now(UTC).isoformat()
    session.add(row)
    session.commit()
    return {"set": True, "updated_at": row.updated_at}


@app.delete("/api/guard/offline-code")
def delete_offline_code(session: Session = Depends(get_session), caller: User = Depends(require_api_key)):
    row = session.get(GuardOfflineCode, caller.username)
    if row is not None:
        session.delete(row)
        session.commit()
    return {"set": False}


@app.get("/api/internal/guard/parents/{username}")
def internal_guard_parents(username: str, session: Session = Depends(get_session), _: None = Depends(require_internal)):
    """Internal: who to tell when a child's phone protection changes."""
    subject = session.exec(select(User).where(User.username == username.strip().lower())).first()
    if subject is None:
        return {"parents": [], "display_name": username}
    return {"parents": _parents_of(session, subject), "display_name": subject.display_name or subject.username}


#: Tamper reports kept on a child's record for the father's Devices section.
GUARD_TAMPER_LIMIT = 50


@app.post("/api/internal/guard/tamper/{username}")
def internal_guard_tamper(username: str, body: dict, session: Session = Depends(get_session), _: None = Depends(require_internal)):
    """Internal: keep a minor's tamper report (protection switched off or worked
    around) and say who to tell. The gateway checked the event name.

    A grown member's phone is their own to manage, so a report from it is
    dropped outright: nothing is stored and nobody is told, and the answer says
    so (``dropped``) instead of looking like a kept report."""
    subject = session.exec(select(User).where(User.username == username.strip().lower())).first()
    if subject is None:
        return {"username": username, "parents": [], "notify": [], "display_name": username, "dropped": "unknown"}
    if (subject.family_role or "other") not in roles.MINOR_FAMILY_ROLES:
        return {
            "username": subject.username,
            "parents": [],
            "notify": [],
            "display_name": subject.display_name or subject.username,
            "dropped": "not_minor",
        }
    event = str((body or {}).get("event") or "")[:40]
    extra = {"event": event}
    detail = str((body or {}).get("detail") or "")[:80]
    if detail:
        extra["detail"] = detail  # the app, the DNS host or how many starts
    session.add(DeviceEvent(
        device_key=str((body or {}).get("device_key") or "")[:128],
        username=subject.username,
        event="guard_tamper",
        extra=json.dumps(extra),
    ))
    session.commit()
    parents = _parents_of(session, subject)
    return {"username": subject.username, "parents": parents, "notify": parents, "display_name": subject.display_name or subject.username}


@app.get("/api/family/devices/{username}")
def family_member_devices(username: str, session: Session = Depends(get_session), caller: User = Depends(require_api_key)):
    """A child's devices with their technical side, for the family member
    page's Devices section: model, OS, app version and build, last seen and
    address, battery, the guard state the phone reported, recent tamper
    reports, and which parents have an offline unlock code.

    Technical data is the father's and an admin's only (the mother sees every
    personal detail, not this): :func:`may_view_technical_data`.
    """
    child = _screen_subject(session, username)
    if caller.username == child.username or not may_view_technical_data(caller, child):
        raise HTTPException(status_code=403, detail="Only the father or an admin can see device details")
    devices = session.exec(select(Device).where(Device.owner_username == child.username, Device.revoked == False)).all()  # noqa: E712
    batteries = _latest_batteries(session, [d.device_key for d in devices])
    out = []
    for d in sorted(devices, key=lambda d: d.last_seen_at or "", reverse=True):
        read = _device_to_read(d)
        read.battery = batteries.get(d.device_key)
        out.append({**read.model_dump(), "os_build": d.os_build})
    tamper = session.exec(
        select(DeviceEvent)
        .where(DeviceEvent.username == child.username, DeviceEvent.event == "guard_tamper")
        .order_by(DeviceEvent.at.desc())
        .limit(GUARD_TAMPER_LIMIT)
    ).all()
    parents = _parents_of(session, child)
    coded = {r.username for r in session.exec(select(GuardOfflineCode).where(GuardOfflineCode.username.in_(parents))).all()} if parents else set()
    return {
        "username": child.username,
        "devices": out,
        "tamper": [{**_device_event_to_read(t).model_dump(), "device_key": t.device_key or None} for t in tamper],
        "offline_code": {p: p in coded for p in parents},
    }


def _journal_out(row: GuardianNote) -> dict:
    return {"id": row.id, "author": row.author, "child": row.child, "text": row.text, "shared": row.shared, "created_at": row.created_at}


@app.get("/api/guardian/journal/{username}")
def list_guardian_journal(username: str, session: Session = Depends(get_session), caller: User = Depends(require_api_key)):
    """A parent's talk journal about a child: their own notes, and the other
    parent's shared ones, newest first."""
    child = _screen_subject(session, username)
    if not _may_manage_screen_time(caller, child):
        raise HTTPException(status_code=403, detail="Only a parent can see this")
    rows = session.exec(select(GuardianNote).where(GuardianNote.child == child.username)).all()
    mine = [r for r in rows if r.author == caller.username or r.shared]
    return {"notes": [_journal_out(r) for r in sorted(mine, key=lambda r: r.created_at, reverse=True)]}


@app.post("/api/guardian/journal/{username}")
def add_guardian_journal(username: str, body: dict, session: Session = Depends(get_session), caller: User = Depends(require_api_key)):
    child = _screen_subject(session, username)
    if not _may_manage_screen_time(caller, child):
        raise HTTPException(status_code=403, detail="Only a parent can write this")
    text = str((body or {}).get("text") or "").strip()
    if not text or len(text) > 4000:
        raise HTTPException(status_code=422, detail="Write 1 to 4000 characters")
    row = GuardianNote(author=caller.username, child=child.username, text=text, shared=bool((body or {}).get("shared")))
    session.add(row)
    session.commit()
    session.refresh(row)
    return _journal_out(row)


@app.delete("/api/guardian/journal/{username}/{note_id}")
def delete_guardian_journal(username: str, note_id: int, session: Session = Depends(get_session), caller: User = Depends(require_api_key)):
    row = session.get(GuardianNote, note_id)
    if row is None or row.child != username.strip().lower() or row.author != caller.username:
        raise HTTPException(status_code=404, detail="No such note")
    session.delete(row)
    session.commit()
    return {"deleted": note_id}


@app.get("/api/internal/guardian/children")
def internal_guardian_children(session: Session = Depends(get_session), _: None = Depends(require_internal)):
    """Internal: every minor, their parents and their monitoring sensitivity
    (the weekly recap and content monitoring read this)."""
    out = []
    for child in session.exec(select(User)).all():
        if (child.family_role or "other") not in roles.MINOR_FAMILY_ROLES:
            continue
        parents = _parents_of(session, child)
        if not parents:
            continue
        out.append({
            "username": child.username,
            "display_name": child.display_name or child.username,
            "parents": parents,
            "monitoring": _screen_rules(session, child.username).get("monitoring") or "severe",
        })
    return {"children": out}


@app.get("/api/internal/screen-time/{username}")
def internal_screen_time(username: str, day: str, session: Session = Depends(get_session), _: None = Depends(require_internal)):
    """Internal: rules and today's granted minutes, for the live status."""
    subject = session.exec(select(User).where(User.username == username.strip().lower())).first()
    if subject is None:
        return {"rules": {}, "bonus_min": 0, "managed": False}
    return {
        "rules": _screen_rules(session, subject.username),
        "bonus_min": _granted_minutes(session, subject.username, day),
        "managed": (subject.family_role or "other") in roles.MINOR_FAMILY_ROLES,
    }


@app.get("/api/internal/may-view-raw")
def internal_may_view_raw(
    viewer: str,
    subject: str,
    session: Session = Depends(get_session),
    _: None = Depends(require_internal),
):
    """Internal: may ``viewer`` read ``subject``'s collected raw data?"""

    def find(username: str) -> User | None:
        return session.exec(select(User).where(User.username == username.strip().lower())).first()

    who, whose = find(viewer), find(subject)
    return {"allowed": may_view_raw_data(who, whose), "technical": may_view_technical_data(who, whose)}


@app.get("/api/internal/activity-sharing")
def internal_activity_sharing(
    session: Session = Depends(get_session),
    _: None = Depends(require_internal),
):
    """Internal: every stored activity-sharing preference (for the geo feed).

    A user with no row has never opted in and is simply absent.
    """
    rows = session.exec(select(UserActivitySharing)).all()
    return {
        "status": "SUCCESS",
        "users": [{"username": r.username, **_activity_sharing_data(r)} for r in rows],
    }


def _resolve_mission(mission_id_or_slug: str, session: Session) -> RavenMission:
    try:
        mid = int(mission_id_or_slug)
        mission = session.exec(select(RavenMission).where(RavenMission.id == mid)).first()
    except ValueError:
        mission = session.exec(select(RavenMission).where(RavenMission.slug == mission_id_or_slug)).first()

    if not mission:
        raise HTTPException(status_code=404, detail="Mission not found")
    return mission

@app.get("/api/raven/missions", response_model=list[RavenMissionListItem])
def get_missions(limit: int = 200, workspace_id: str | None = None, session: Session = Depends(get_session), _: None = Depends(require_internal)):
    from typing import Any, cast

    from sqlalchemy.orm import defer
    stmt = select(RavenMission).order_by(text("created_at DESC"))
    if workspace_id:
        stmt = stmt.where(RavenMission.workspace_id == workspace_id)
    stmt = stmt.options(defer(cast(Any, RavenMission.output_log)), defer(cast(Any, RavenMission.result)), defer(cast(Any, RavenMission.artifacts)))
    if limit and limit > 0:
        stmt = stmt.limit(limit)
    return session.exec(stmt).all()

@app.get("/api/raven/missions/{mission_id_or_slug}", response_model=RavenMissionRead)
def get_mission(mission_id_or_slug: str, session: Session = Depends(get_session), _: None = Depends(require_internal)):
    return _resolve_mission(mission_id_or_slug, session)

@app.post("/api/raven/missions", response_model=RavenMissionRead)
def create_mission(
    body: RavenMissionCreate,
    session: Session = Depends(get_session),
    _: None = Depends(require_internal),
):
    # Ensure slug uniqueness if provided
    if body.slug:
        existing = session.exec(select(RavenMission).where(RavenMission.slug == body.slug)).first()
        if existing:
            raise HTTPException(status_code=400, detail=f"Mission slug '{body.slug}' already exists")

    mission = RavenMission(**body.model_dump())
    # A mission is "queued" the moment it is created for execution.
    if not mission.queued_at:
        mission.queued_at = datetime.now(UTC).isoformat()
    session.add(mission)
    session.commit()
    session.refresh(mission)
    return mission

@app.patch("/api/raven/missions/{mission_id_or_slug}", response_model=RavenMissionRead)
def update_mission(
    mission_id_or_slug: str,
    body: RavenMissionUpdate,
    session: Session = Depends(get_session),
    _: None = Depends(require_internal),
):
    mission = _resolve_mission(mission_id_or_slug, session)

    update_data = body.model_dump(exclude_unset=True)
    # Prevent slug collision on update
    if "slug" in update_data and update_data["slug"] and update_data["slug"] != mission.slug:
        existing = session.exec(select(RavenMission).where(RavenMission.slug == update_data["slug"])).first()
        if existing:
            raise HTTPException(status_code=400, detail=f"Mission slug '{update_data['slug']}' already exists")

    for k, v in update_data.items():
        setattr(mission, k, v)

    session.add(mission)
    session.commit()
    session.refresh(mission)
    return mission


class RavenMissionTransition(BaseModel):
    from_status: str
    to_status: str


@app.post("/api/raven/missions/{mission_id}/transition")
def transition_mission(
    mission_id: int,
    body: RavenMissionTransition,
    session: Session = Depends(get_session),
    _: None = Depends(require_internal),
):
    """Move a mission from one status to another only if it is still in the
    first: one conditional UPDATE, so of two callers racing (two taps on
    Approve) exactly one wins and the other gets 409."""
    from sqlalchemy import update

    result = session.exec(
        update(RavenMission)
        .where(RavenMission.id == mission_id, RavenMission.status == body.from_status)
        .values(status=body.to_status)
    )
    session.commit()
    if result.rowcount == 1:
        return {"status": "SUCCESS", "mission_status": body.to_status}
    mission = session.get(RavenMission, mission_id)
    if not mission:
        raise HTTPException(status_code=404, detail="Mission not found")
    raise HTTPException(status_code=409, detail=f"Mission #{mission_id} is already {mission.status}")


@app.delete("/api/raven/missions/{mission_id_or_slug}")
def delete_mission(mission_id_or_slug: str, session: Session = Depends(get_session), _: None = Depends(require_internal)):

    mission = _resolve_mission(mission_id_or_slug, session)
    if not mission:
        raise HTTPException(status_code=404, detail="Mission not found")
    session.delete(mission)
    session.commit()
    return {"status": "SUCCESS"}

@app.delete("/api/raven/missions/{mission_id}")
def delete_mission_by_id(mission_id: int, session: Session = Depends(get_session), _: None = Depends(require_internal)):
    mission = session.exec(select(RavenMission).where(RavenMission.id == mission_id)).first()
    if not mission:
        raise HTTPException(status_code=404, detail="Mission not found")
    session.delete(mission)
    session.commit()
    return {"status": "SUCCESS"}


@app.post("/api/auth/import/nextcloud", response_model=ImportResponse)
async def import_nextcloud_users(x_internal_secret: str | None = Header(default=None)):
    """Import users from Nextcloud and Home Assistant, merging by username.
    Generates temp passwords and pre-fills all available user data."""
    if x_internal_secret != INTERNAL_SECRET:
        raise HTTPException(status_code=403, detail="Forbidden")

    warnings: list[str] = []

    with Session(engine) as session:
        admin = session.exec(select(User).where(User.is_admin)).first()
        default_user = session.exec(select(User).where(User.username == "default")).first()

        # Resolve Nextcloud credentials
        if not admin or not admin.nextcloud_url:
            nc_url_setting = session.exec(select(GlobalSetting).where(GlobalSetting.key == "NEXTCLOUD_URL")).first()
            nc_user_setting = session.exec(select(GlobalSetting).where(GlobalSetting.key == "NEXTCLOUD_USER")).first()
            nc_pass_setting = session.exec(select(GlobalSetting).where(GlobalSetting.key == "NEXTCLOUD_PASS")).first()
            from services.config import NEXTCLOUD_PASS, NEXTCLOUD_URL, NEXTCLOUD_USER
            nc_url = nc_url_setting.value if nc_url_setting else NEXTCLOUD_URL
            nc_admin_user = nc_user_setting.value if nc_user_setting else NEXTCLOUD_USER
            nc_admin_pass = nc_pass_setting.value if nc_pass_setting else NEXTCLOUD_PASS
        else:
            nc_url = admin.nextcloud_url
            nc_admin_user = admin.nextcloud_user
            nc_admin_pass = decrypt(admin.nextcloud_pass_enc) if admin.nextcloud_pass_enc else None

        # Resolve Home Assistant credentials
        ha_url = None
        if admin:
            ha_url = admin.ha_url
        if not ha_url and default_user:
            ha_url = default_user.ha_url
        ha_token_enc = None
        if admin:
            ha_token_enc = admin.ha_token_enc
        if not ha_token_enc and default_user:
            ha_token_enc = default_user.ha_token_enc
        ha_token = decrypt(ha_token_enc) if ha_token_enc else None

        if not nc_url or not nc_admin_user or not nc_admin_pass:
            raise HTTPException(status_code=400, detail="Nextcloud configuration missing")

        # Collect data from both sources
        ha_users: dict[str, dict] = {}
        nc_users: dict[str, dict] = {}

        # Fetch HA person entities
        if ha_url and ha_token:
            try:
                async with get_client_insecure() as client:
                    resp = await client.get(
                        f"{ha_url.rstrip('/')}/api/states",
                        headers={"Authorization": f"Bearer {ha_token}"},
                        timeout=aiohttp.ClientTimeout(total=10.0),
                    )
                    if resp.status == 200:
                        for state in await resp.json():
                            if state['entity_id'].startswith('person.'):
                                username = state['entity_id'].split('.')[1]
                                attrs = state.get('attributes', {})
                                ha_users[username] = {
                                    "display_name": attrs.get('friendly_name', username),
                                    "entity_id": state['entity_id'],
                                    "user_id": attrs.get('user_id'),
                                    "device_trackers": attrs.get('device_trackers', []),
                                }
            except Exception as e:
                log.error(f"[import] HA Error: {e!s}")

        # Fetch Nextcloud users with detailed info
        try:
            async with get_client_insecure() as client:
                resp = await client.get(
                    f"{nc_url.rstrip('/')}/ocs/v1.php/cloud/users",
                    auth=aiohttp.BasicAuth(nc_admin_user, nc_admin_pass),
                    headers={"OCS-APIRequest": "true", "Accept": "application/json"},
                    params={"format": "json"}
                )
                if resp.status != 200:
                    raise HTTPException(status_code=resp.status, detail=f"Nextcloud API error: {await resp.text()}")

                nc_resp = await resp.json()
                # Handle Nextcloud API error responses (e.g., 403 for non-admin users)
                meta = nc_resp.get("ocs", {}).get("meta", {})
                if meta.get("status") == "failure":
                    msg = meta.get("message", "Unknown error")
                    warn_text = f"Nextcloud: {msg}"
                    log.warning(f"[import] {warn_text}")
                    warnings.append(warn_text)
                    usernames = []
                else:
                    usernames = nc_resp.get("ocs", {}).get("data", {}).get("users", [])

                for nc_username in usernames:
                    nc_data = {"nc_username": nc_username}
                    try:
                        detail_resp = await client.get(
                            f"{nc_url.rstrip('/')}/ocs/v1.php/cloud/users/{nc_username}",
                            auth=aiohttp.BasicAuth(nc_admin_user, nc_admin_pass),
                            headers={"OCS-APIRequest": "true", "Accept": "application/json"},
                            params={"format": "json"}
                        )
                        if detail_resp.status == 200:
                            udata = (await detail_resp.json()).get("ocs", {}).get("data", {})
                            nc_data["display_name"] = udata.get("display-name") or udata.get("displayname")
                            nc_data["email"] = udata.get("email")
                            nc_data["phone"] = udata.get("phone")
                            nc_data["address"] = udata.get("address")
                            nc_data["website"] = udata.get("website")
                            nc_data["twitter"] = udata.get("twitter")
                            nc_data["groups"] = udata.get("groups", [])
                            nc_data["language"] = udata.get("language")
                            nc_data["locale"] = udata.get("locale")
                            nc_data["quota"] = udata.get("quota")
                            nc_data["enabled"] = udata.get("enabled")
                    except Exception:
                        pass
                    nc_users[nc_username.lower()] = nc_data
        except Exception as e:
            log.error(f"[import] Nextcloud Error: {e!s}")
            raise HTTPException(status_code=500, detail=str(e)) from None

        # Merge and import users
        all_usernames = set(ha_users.keys()) | set(nc_users.keys())
        imported = []
        for username in sorted(all_usernames):
            existing = session.exec(select(User).where(User.username == username)).first()
            if existing:
                continue

            in_ha = username in ha_users
            in_nc = username in nc_users
            ha_data = ha_users.get(username, {})
            nc_data = nc_users.get(username, {})

            # Determine source label
            source = "Home Assistant + Nextcloud" if (in_ha and in_nc) else ("Home Assistant" if in_ha else "Nextcloud")

            # Prefer NC display_name (usually more accurate), fall back to HA
            display_name = nc_data.get("display_name") or ha_data.get("display_name") or username.capitalize()
            email = nc_data.get("email")

            # Generate a secure random password for the imported user
            temp_password = os.urandom(16).hex()

            new_user = User(
                username=username,
                display_name=display_name,
                is_admin=False,
                password_hash=hash_password(temp_password),
                nextcloud_url=nc_url if in_nc else None,
                nextcloud_user=nc_data.get("nc_username") if in_nc else None,
                ha_url=ha_url if in_ha else None,
            )
            session.add(new_user)
            imported.append({
                "username": username,
                "display_name": display_name,
                "email": email,
                "source": source,
                "temp_password": temp_password,
                "nextcloud_groups": nc_data.get("groups", []),
                "ha_entity_id": ha_data.get("entity_id"),
                "ha_device_trackers": ha_data.get("device_trackers", []),
            })

        session.commit()
        for row in imported:
            _audit(None, session, "user.import", row["username"], changes=[{"field": "account", "change": "set"}],
                   note=f"imported from {row['source']}")
        return {
            "status": "SUCCESS",
            "message": f"Imported {len(imported)} users",
            "imported_users": imported,
        }


# Device & Light Grouping (Section 3.14)

@app.get("/api/groups/media")
def list_media_groups(x_internal_secret: str = Header(...)):
    _require_internal_secret(x_internal_secret)
    with Session(engine) as session:
        groups = session.exec(select(GlobalSetting).where(text("globalsetting.key LIKE 'media_group:%'"))).all()
        result = []
        for g in groups:
            data = json.loads(g.value)
            data["key"] = g.key
            result.append(data)
        return result


@app.post("/api/groups/media")
def create_media_group(group_data: dict, x_internal_secret: str = Header(...)):
    _require_internal_secret(x_internal_secret)
    group_id = group_data.get("group_id") or group_data.get("name")
    if not group_id:
        raise HTTPException(status_code=400, detail="group_id or name is required")
    key = f"media_group:{group_id}"
    with Session(engine) as session:
        existing = session.exec(select(GlobalSetting).where(GlobalSetting.key == key)).first()
        if existing:
            raise HTTPException(status_code=409, detail=f"Media group '{group_id}' already exists")
        group = GlobalSetting(
            key=key,
            value=json.dumps({
                "group_id": group_id,
                "group_name": group_data.get("group_name") or group_data.get("name", group_id),
                "member_entity_ids": group_data.get("member_entity_ids", []),
                "scope": group_data.get("scope", "user"),
                "owner_user_id": group_data.get("owner_user_id", "system"),
            }),
            description=f"Media group: {group_data.get('group_name') or group_data.get('name', '')}",
        )
        session.add(group)
        session.commit()
        return {"status": "SUCCESS", "message": f"Media group '{group_id}' created"}


@app.delete("/api/groups/media/{group_id}")
def delete_media_group(group_id: str, x_internal_secret: str = Header(...)):
    _require_internal_secret(x_internal_secret)
    key = f"media_group:{group_id}"
    with Session(engine) as session:
        group = session.exec(select(GlobalSetting).where(GlobalSetting.key == key)).first()
        if not group:
            raise HTTPException(status_code=404, detail=f"Media group '{group_id}' not found")
        session.delete(group)
        session.commit()
        return {"status": "SUCCESS", "message": f"Media group '{group_id}' deleted"}


@app.post("/api/groups/media/{group_id}/members")
def add_media_group_members(group_id: str, member_data: dict, x_internal_secret: str = Header(...)):
    _require_internal_secret(x_internal_secret)
    key = f"media_group:{group_id}"
    with Session(engine) as session:
        group = session.exec(select(GlobalSetting).where(GlobalSetting.key == key)).first()
        if not group:
            raise HTTPException(status_code=404, detail=f"Media group '{group_id}' not found")
        data = json.loads(group.value)
        existing = set(data.get("member_entity_ids", []))
        existing.update(member_data.get("entity_ids", []))
        data["member_entity_ids"] = list(existing)
        group.value = json.dumps(data)
        session.commit()
        return {"status": "SUCCESS", "message": f"Members added to '{group_id}'"}


@app.delete("/api/groups/media/{group_id}/members")
def remove_media_group_members(group_id: str, member_data: dict, x_internal_secret: str = Header(...)):
    _require_internal_secret(x_internal_secret)
    key = f"media_group:{group_id}"
    with Session(engine) as session:
        group = session.exec(select(GlobalSetting).where(GlobalSetting.key == key)).first()
        if not group:
            raise HTTPException(status_code=404, detail=f"Media group '{group_id}' not found")
        data = json.loads(group.value)
        existing = set(data.get("member_entity_ids", []))
        existing -= set(member_data.get("entity_ids", []))
        data["member_entity_ids"] = list(existing)
        group.value = json.dumps(data)
        session.commit()
        return {"status": "SUCCESS", "message": f"Members removed from '{group_id}'"}


@app.get("/api/groups/lights")
def list_light_clusters(x_internal_secret: str = Header(...)):
    _require_internal_secret(x_internal_secret)
    with Session(engine) as session:
        clusters = session.exec(select(GlobalSetting).where(text("globalsetting.key LIKE 'light_cluster:%'"))).all()
        result = []
        for c in clusters:
            data = json.loads(c.value)
            data["key"] = c.key
            result.append(data)
        return result


@app.post("/api/groups/lights")
def create_light_cluster(cluster_data: dict, x_internal_secret: str = Header(...)):
    _require_internal_secret(x_internal_secret)
    cluster_id = cluster_data.get("cluster_id") or cluster_data.get("name")
    if not cluster_id:
        raise HTTPException(status_code=400, detail="cluster_id or name is required")
    key = f"light_cluster:{cluster_id}"
    with Session(engine) as session:
        existing = session.exec(select(GlobalSetting).where(GlobalSetting.key == key)).first()
        if existing:
            raise HTTPException(status_code=409, detail=f"Light cluster '{cluster_id}' already exists")
        cluster = GlobalSetting(
            key=key,
            value=json.dumps({
                "cluster_id": cluster_id,
                "cluster_name": cluster_data.get("cluster_name") or cluster_data.get("name", cluster_id),
                "member_entity_ids": cluster_data.get("member_entity_ids", []),
                "room": cluster_data.get("room"),
                "scope": cluster_data.get("scope", "room"),
                "owner_user_id": cluster_data.get("owner_user_id", "system"),
            }),
            description=f"Light cluster: {cluster_data.get('cluster_name') or cluster_data.get('name', '')}",
        )
        session.add(cluster)
        session.commit()
        return {"status": "SUCCESS", "message": f"Light cluster '{cluster_id}' created"}


@app.delete("/api/groups/lights/{cluster_id}")
def delete_light_cluster(cluster_id: str, x_internal_secret: str = Header(...)):
    _require_internal_secret(x_internal_secret)
    key = f"light_cluster:{cluster_id}"
    with Session(engine) as session:
        cluster = session.exec(select(GlobalSetting).where(GlobalSetting.key == key)).first()
        if not cluster:
            raise HTTPException(status_code=404, detail=f"Light cluster '{cluster_id}' not found")
        session.delete(cluster)
        session.commit()
        return {"status": "SUCCESS", "message": f"Light cluster '{cluster_id}' deleted"}


@app.post("/api/groups/lights/{cluster_id}/members")
def add_light_cluster_members(cluster_id: str, member_data: dict, x_internal_secret: str = Header(...)):
    _require_internal_secret(x_internal_secret)
    key = f"light_cluster:{cluster_id}"
    with Session(engine) as session:
        cluster = session.exec(select(GlobalSetting).where(GlobalSetting.key == key)).first()
        if not cluster:
            raise HTTPException(status_code=404, detail=f"Light cluster '{cluster_id}' not found")
        data = json.loads(cluster.value)
        existing = set(data.get("member_entity_ids", []))
        existing.update(member_data.get("entity_ids", []))
        data["member_entity_ids"] = list(existing)
        cluster.value = json.dumps(data)
        session.commit()
        return {"status": "SUCCESS", "message": f"Members added to '{cluster_id}'"}


@app.delete("/api/groups/lights/{cluster_id}/members")
def remove_light_cluster_members(cluster_id: str, member_data: dict, x_internal_secret: str = Header(...)):
    _require_internal_secret(x_internal_secret)
    key = f"light_cluster:{cluster_id}"
    with Session(engine) as session:
        cluster = session.exec(select(GlobalSetting).where(GlobalSetting.key == key)).first()
        if not cluster:
            raise HTTPException(status_code=404, detail=f"Light cluster '{cluster_id}' not found")
        data = json.loads(cluster.value)
        existing = set(data.get("member_entity_ids", []))
        existing -= set(member_data.get("entity_ids", []))
        data["member_entity_ids"] = list(existing)
        cluster.value = json.dumps(data)
        session.commit()
        return {"status": "SUCCESS", "message": f"Members removed from '{cluster_id}'"}


@app.get("/api/groups/patterns")
def list_light_patterns(x_internal_secret: str = Header(...)):
    _require_internal_secret(x_internal_secret)
    with Session(engine) as session:
        patterns = session.exec(select(GlobalSetting).where(text("globalsetting.key LIKE 'light_pattern:%'"))).all()
        result = []
        for p in patterns:
            data = json.loads(p.value)
            data["key"] = p.key
            result.append(data)
        return result


@app.post("/api/groups/patterns")
def create_light_pattern(pattern_data: dict, x_internal_secret: str = Header(...)):
    _require_internal_secret(x_internal_secret)
    pattern_id = pattern_data.get("pattern_id") or pattern_data.get("name")
    if not pattern_id:
        raise HTTPException(status_code=400, detail="pattern_id or name is required")
    key = f"light_pattern:{pattern_id}"
    with Session(engine) as session:
        existing = session.exec(select(GlobalSetting).where(GlobalSetting.key == key)).first()
        if existing:
            raise HTTPException(status_code=409, detail=f"Light pattern '{pattern_id}' already exists")
        pattern = GlobalSetting(
            key=key,
            value=json.dumps({
                "pattern_id": pattern_id,
                "pattern_name": pattern_data.get("pattern_name") or pattern_data.get("name", pattern_id),
                "cluster_id": pattern_data.get("cluster_id"),
                "steps": pattern_data.get("steps", []),
                "loop": pattern_data.get("loop", False),
                "transition_ms": pattern_data.get("transition_ms", 500),
            }),
            description=f"Light pattern: {pattern_data.get('pattern_name') or pattern_data.get('name', '')}",
        )
        session.add(pattern)
        session.commit()
        return {"status": "SUCCESS", "message": f"Light pattern '{pattern_id}' created"}


@app.patch("/api/groups/patterns/{pattern_id}")
def update_light_pattern(pattern_id: str, pattern_data: dict, x_internal_secret: str = Header(...)):
    _require_internal_secret(x_internal_secret)
    key = f"light_pattern:{pattern_id}"
    with Session(engine) as session:
        pattern = session.exec(select(GlobalSetting).where(GlobalSetting.key == key)).first()
        if not pattern:
            raise HTTPException(status_code=404, detail=f"Light pattern '{pattern_id}' not found")
        data = json.loads(pattern.value)
        data.update({k: v for k, v in pattern_data.items() if v is not None})
        pattern.value = json.dumps(data)
        session.commit()
        return {"status": "SUCCESS", "message": f"Light pattern '{pattern_id}' updated"}


@app.delete("/api/groups/patterns/{pattern_id}")
def delete_light_pattern(pattern_id: str, x_internal_secret: str = Header(...)):
    _require_internal_secret(x_internal_secret)
    key = f"light_pattern:{pattern_id}"
    with Session(engine) as session:
        pattern = session.exec(select(GlobalSetting).where(GlobalSetting.key == key)).first()
        if not pattern:
            raise HTTPException(status_code=404, detail=f"Light pattern '{pattern_id}' not found")
        session.delete(pattern)
        session.commit()
        return {"status": "SUCCESS", "message": f"Light pattern '{pattern_id}' deleted"}


# Device Telemetry Monitoring (Section 3.15)

@app.get("/api/telemetry/enroll")
def list_telemetry_enrollments(x_internal_secret: str = Header(...)):
    _require_internal_secret(x_internal_secret)
    with Session(engine) as session:
        enrollments = session.exec(select(GlobalSetting).where(text("globalsetting.key LIKE 'telemetry_enroll:%'"))).all()
        result = []
        for e in enrollments:
            data = json.loads(e.value)
            data["key"] = e.key
            result.append(data)
        return {"enrollments": result}


@app.post("/api/telemetry/enroll")
def enroll_telemetry(enroll_data: dict, x_internal_secret: str = Header(...)):
    _require_internal_secret(x_internal_secret)
    entity_id = enroll_data.get("entity_id")
    if not entity_id:
        raise HTTPException(status_code=400, detail="entity_id is required")
    key = f"telemetry_enroll:{entity_id}"
    with Session(engine) as session:
        from datetime import datetime
        existing = session.exec(select(GlobalSetting).where(GlobalSetting.key == key)).first()
        if existing:
            # Re-enrolling is how the UI edits an enrollment: merge the new
            # config over the stored one instead of failing with a 409.
            try:
                record = json.loads(existing.value) or {}
            except Exception:
                record = {}
            record.update(enroll_data)
            record["entity_id"] = entity_id
            record["updated_at"] = datetime.now(UTC).isoformat()
            existing.value = json.dumps(record)
            session.add(existing)
            session.commit()
            return {
                "status": "SUCCESS",
                "message": f"Updated '{entity_id}' telemetry enrollment",
                "updated": True,
            }
        # Persist the full config (entity_id, power_tracking, etc.) so the
        # Energy Insights widget can filter enrollments by capability.
        record = dict(enroll_data)
        record["entity_id"] = entity_id
        record["enrolled_by"] = "system"
        record["enrolled_at"] = datetime.now(UTC).isoformat()
        enrollment = GlobalSetting(
            key=key,
            value=json.dumps(record),
            description=f"Telemetry enrollment: {entity_id}",
        )
        session.add(enrollment)
        session.commit()
        return {"status": "SUCCESS", "message": f"Enrolled '{entity_id}' in telemetry monitoring"}


@app.put("/api/telemetry/enroll/{entity_id}")
def update_telemetry_enrollment(
    entity_id: str,
    enroll_data: dict,
    x_internal_secret: str = Header(...),
):
    """Update an existing enrollment in place (keeps owner + created metadata)."""
    _require_internal_secret(x_internal_secret)
    key = f"telemetry_enroll:{entity_id}"
    with Session(engine) as session:
        from datetime import datetime
        existing = session.exec(select(GlobalSetting).where(GlobalSetting.key == key)).first()
        if not existing:
            raise HTTPException(status_code=404, detail=f"'{entity_id}' not enrolled")
        try:
            record = json.loads(existing.value) or {}
        except Exception:
            record = {}
        created_at = record.get("enrolled_at")
        enrolled_by = record.get("enrolled_by")
        record.update({k: v for k, v in enroll_data.items() if k != "entity_id"})
        record["entity_id"] = entity_id
        if created_at:
            record["enrolled_at"] = created_at
        if enrolled_by:
            record["enrolled_by"] = enrolled_by
        record["updated_at"] = datetime.now(UTC).isoformat()
        existing.value = json.dumps(record)
        session.add(existing)
        session.commit()
        return {
            "status": "SUCCESS",
            "message": f"Updated '{entity_id}' telemetry enrollment",
            "enrollment": record,
        }


@app.delete("/api/telemetry/enroll/{entity_id}")
def unenroll_telemetry(entity_id: str, x_internal_secret: str = Header(...)):
    _require_internal_secret(x_internal_secret)
    key = f"telemetry_enroll:{entity_id}"
    with Session(engine) as session:
        enrollment = session.exec(select(GlobalSetting).where(GlobalSetting.key == key)).first()
        if not enrollment:
            raise HTTPException(status_code=404, detail=f"'{entity_id}' not enrolled")
        session.delete(enrollment)
        session.commit()
        return {"status": "SUCCESS", "message": f"Unenrolled '{entity_id}' from telemetry monitoring"}


@app.post("/api/telemetry/snapshot")
def ingest_telemetry_snapshot(snapshot_data: dict, x_internal_secret: str = Header(...)):
    _require_internal_secret(x_internal_secret)
    entity_id = snapshot_data.get("entity_id")
    if not entity_id:
        raise HTTPException(status_code=400, detail="entity_id is required")
    import time
    key = f"telemetry_data:{entity_id}"
    with Session(engine) as session:
        existing = session.exec(select(GlobalSetting).where(GlobalSetting.key == key)).first()
        data_points = []
        if existing:
            data_points = json.loads(existing.value)
        new_point = {
            "recorded_at": time.time(),
            "power_w": snapshot_data.get("power_w"),
            "is_available": snapshot_data.get("is_available", True),
            "state": snapshot_data.get("state"),
            "source": snapshot_data.get("source", "poll"),
        }
        data_points.append(new_point)
        data_points = data_points[-1000:]
        if existing:
            existing.value = json.dumps(data_points)
        else:
            snapshot = GlobalSetting(
                key=key,
                value=json.dumps(data_points),
                description=f"Telemetry data: {entity_id}",
            )
            session.add(snapshot)
        session.commit()
        return {"status": "SUCCESS", "message": f"Snapshot recorded for '{entity_id}'"}


@app.get("/api/telemetry/data/{entity_id}")
def get_telemetry_data(
    entity_id: str,
    hours: int | None = None,
    x_internal_secret: str = Header(...),
):
    _require_internal_secret(x_internal_secret)
    key = f"telemetry_data:{entity_id}"
    with Session(engine) as session:
        snapshot = session.exec(select(GlobalSetting).where(GlobalSetting.key == key)).first()
        if not snapshot:
            return {"entity_id": entity_id, "data_points": [], "data": []}
        data_points = json.loads(snapshot.value)
        if hours:
            cutoff = time.time() - hours * 3600
            data_points = [
                p for p in data_points if float(p.get("recorded_at") or 0) >= cutoff
            ]
        return {"entity_id": entity_id, "data_points": data_points, "data": data_points}


@app.get("/api/telemetry/summary/{entity_id}")
def get_telemetry_summary(entity_id: str, x_internal_secret: str = Header(...)):
    _require_internal_secret(x_internal_secret)
    key = f"telemetry_data:{entity_id}"
    with Session(engine) as session:
        snapshot = session.exec(select(GlobalSetting).where(GlobalSetting.key == key)).first()
        if not snapshot:
            return {"entity_id": entity_id, "summary": None}
        data_points = json.loads(snapshot.value)
        if not data_points:
            return {"entity_id": entity_id, "summary": None}
        power_values = [p["power_w"] for p in data_points if p.get("power_w") is not None]
        available_count = sum(1 for p in data_points if p.get("is_available", True))
        total = len(data_points)
        unavailable_points = [p for p in data_points if not p.get("is_available", True)]
        last_outage = unavailable_points[-1] if unavailable_points else None

        peak_power_w = max(power_values) if power_values else None
        peak_point = None
        peak_duration = 0.0
        if peak_power_w is not None:
            peak_indices = [i for i, p in enumerate(data_points) if p.get("power_w") == peak_power_w]
            if peak_indices:
                peak_idx = peak_indices[-1]
                peak_point = data_points[peak_idx]
                if peak_power_w > 0.0:
                    # Scan backwards and forwards around this peak point to measure consecutive high power draw (>=95% of peak)
                    threshold = 0.95 * peak_power_w
                    start_idx = peak_idx
                    while start_idx > 0:
                        prev_p = data_points[start_idx - 1].get("power_w")
                        if prev_p is not None and prev_p >= threshold:
                            start_idx -= 1
                        else:
                            break
                    end_idx = peak_idx
                    while end_idx < len(data_points) - 1:
                        next_p = data_points[end_idx + 1].get("power_w")
                        if next_p is not None and next_p >= threshold:
                            end_idx += 1
                        else:
                            break
                    start_ts = data_points[start_idx].get("recorded_at")
                    end_ts = data_points[end_idx].get("recorded_at")
                    if start_ts and end_ts:
                        peak_duration = end_ts - start_ts

        summary = {
            "entity_id": entity_id,
            "current_power_w": power_values[-1] if power_values else None,
            "peak_power_w": peak_power_w,
            "peak_at": peak_point.get("recorded_at") if peak_point else None,
            "peak_duration_seconds": peak_duration if peak_point else 0,
            "avg_power_w": sum(power_values) / len(power_values) if power_values else None,
            "availability_pct": (available_count / total * 100) if total > 0 else 100.0,
            "total_activations": total,
            "last_outage_at": last_outage.get("recorded_at") if last_outage else None,
            "data_points": data_points[-100:],
        }
        return {"entity_id": entity_id, "summary": summary}


@app.post("/api/telemetry/analyze")
async def trigger_telemetry_analysis(analysis_data: dict, x_internal_secret: str = Header(...)):
    """Run a real LLM analysis over a enrolled entity's telemetry history.

    Aggregates the stored power/availability data points, sends a stats-only
    prompt through the LLM gateway (rag_user identity resolution, same path
    OpenWebUI clients use), and persists the resulting insight as
    telemetry_insight:{entity_id}. Returns analysis_available=false rather
    than fabricating text when the gateway is unreachable.
    """
    _require_internal_secret(x_internal_secret)
    entity_id = analysis_data.get("entity_id")
    if not entity_id:
        raise HTTPException(status_code=400, detail="entity_id is required")
    hours = int(analysis_data.get("hours", 168) or 168)
    if hours < 1 or hours > 2160:
        raise HTTPException(status_code=422, detail="hours must be between 1 and 2160")

    import statistics
    import time as _time

    key = f"telemetry_data:{entity_id}"
    with Session(engine) as session:
        snapshot = session.exec(select(GlobalSetting).where(GlobalSetting.key == key)).first()
        if not snapshot:
            raise HTTPException(status_code=404, detail=f"No telemetry data for '{entity_id}'")
        data_points = json.loads(snapshot.value)

    cutoff = _time.time() - hours * 3600
    window = [p for p in data_points if (p.get("recorded_at") or 0) >= cutoff]
    if not window:
        window = data_points[-100:]
    if not window:
        raise HTTPException(status_code=404, detail=f"No telemetry data for '{entity_id}'")

    power_values = [p["power_w"] for p in window if p.get("power_w") is not None]
    available_count = sum(1 for p in window if p.get("is_available", True))
    total = len(window)

    stats: dict = {
        "entity_id": entity_id,
        "window_hours": hours,
        "data_points": total,
        "availability_pct": round(available_count / total * 100, 1) if total else 0.0,
    }
    if power_values:
        stats.update({
            "current_power_w": power_values[-1],
            "avg_power_w": round(statistics.mean(power_values), 1),
            "peak_power_w": max(power_values),
            "min_power_w": min(power_values),
            "estimated_energy_kwh": round(
                sum(power_values) / 3600 * (60 if total > 1 else 0) / 1000, 2
            ) if total > 1 else None,
        })

    prompt_lines = [
        f"Analyze the following power telemetry for Home Assistant entity '{entity_id}'",
        f"covering the last {hours} hours. Produce a concise insight (3-5 sentences):",
        "describe usage patterns, note anomalies or availability issues, and offer one",
        "practical energy-saving suggestion. Reference the actual numbers.",
        "",
        json.dumps(stats, indent=2),
    ]
    prompt = "\n".join(prompt_lines)

    analysis_text: str | None = None
    try:
        from services.config import GATEWAY_INTERNAL_URL
        from services.config import INTERNAL_SECRET as _INTERNAL_SECRET
        gateway_url = GATEWAY_INTERNAL_URL or "http://gateway:11435"
        enrollment_key = f"telemetry_enroll:{entity_id}"
        rag_user = None
        with Session(engine) as session:
            enrollment = session.exec(
                select(GlobalSetting).where(GlobalSetting.key == enrollment_key)
            ).first()
            if enrollment:
                rag_user = (json.loads(enrollment.value) or {}).get("rag_user")
        if not rag_user:
            admin = session.exec(select(User).where(User.is_admin == True)).first()  # noqa: E712
            if admin is None:
                admin = session.exec(select(User).where(User.username == "default")).first()
            rag_user = admin.username if admin else "default"

        import aiohttp
        body = {
            # Telemetry analysis runs on its own model so a scheduled/queued
            # report never competes with the voice assistant's model.
            "model": "telemetry",
            "messages": [{"role": "user", "content": prompt}],
            "rag_user": rag_user,
            # Reasoning models blend their <think> trace into `content` unless
            # the caller opts out, which put the model's thinking straight into
            # the stored insight text. We want the conclusion only.
            "think": False,
        }
        timeout = aiohttp.ClientTimeout(total=90.0)
        async with aiohttp.ClientSession() as client, client.post(
            f"{gateway_url.rstrip('/')}/v1/chat/completions",
            json=body,
            headers={"X-Internal-Secret": _INTERNAL_SECRET},
            timeout=timeout,
        ) as resp:
            if resp.status == 200:
                data = await resp.json()
                choices = data.get("choices") or []
                if choices:
                    analysis_text = (choices[0].get("message") or {}).get("content")
                else:
                    content = data.get("response") or data.get("answer") or data.get("message")
                    if isinstance(content, dict):
                        content = content.get("content")
                    if isinstance(content, str):
                        analysis_text = content
            else:
                log.warning(f"[telemetry] LLM gateway status {resp.status} for {entity_id}")
    except Exception as e:
        log.warning(f"[telemetry] LLM analysis failed for {entity_id}: {e}")

    insight = {
        "entity_id": entity_id,
        "generated_at": _time.time(),
        "window_hours": hours,
        "stats": stats,
        "analysis": analysis_text,
        "analysis_available": analysis_text is not None,
        "model": "assistant",
    }

    insight_key = f"telemetry_insight:{entity_id}"
    with Session(engine) as session:
        existing = session.exec(select(GlobalSetting).where(GlobalSetting.key == insight_key)).first()
        if existing:
            existing.value = json.dumps(insight)
        else:
            record = GlobalSetting(
                key=insight_key,
                value=json.dumps(insight),
                description=f"Telemetry LLM insight: {entity_id}",
            )
            session.add(record)
        session.commit()

    return {"status": "SUCCESS", "entity_id": entity_id, "hours": hours, **insight}


@app.get("/api/telemetry/insights")
def get_telemetry_insights(x_internal_secret: str = Header(...)):
    _require_internal_secret(x_internal_secret)
    with Session(engine) as session:
        insights = session.exec(select(GlobalSetting).where(text("globalsetting.key LIKE 'telemetry_insight:%'"))).all()
        result = []
        for i in insights:
            data = json.loads(i.value)
            data["key"] = i.key
            result.append(data)
        return {"insights": result}


@app.get("/api/telemetry/insights/{entity_id}")
def get_telemetry_insight_for_entity(entity_id: str, x_internal_secret: str = Header(...)):
    """Latest stored analysis for one entity (the UI asks for this per entity)."""
    _require_internal_secret(x_internal_secret)
    key = f"telemetry_insight:{entity_id}"
    with Session(engine) as session:
        insight = session.exec(select(GlobalSetting).where(GlobalSetting.key == key)).first()
        if not insight:
            return {"entity_id": entity_id, "insight": None, "insights": []}
        data = json.loads(insight.value)
        data["key"] = insight.key
        return {
            "entity_id": entity_id,
            "insight": data,
            "insights": [
                {
                    "type": (data.get("analysis_available") and "analysis") or "none",
                    "message": data.get("analysis") or "",
                    "severity": "info",
                    "timestamp": data.get("generated_at"),
                }
            ],
        }


# Household Intercom System (Section 3.16)

@app.get("/api/intercom/sessions")
def list_intercom_sessions(x_internal_secret: str = Header(...)):
    _require_internal_secret(x_internal_secret)
    with Session(engine) as session:
        sessions = session.exec(select(GlobalSetting).where(text("globalsetting.key LIKE 'intercom_session:%'"))).all()
        result = []
        for s in sessions:
            data = json.loads(s.value)
            data["key"] = s.key
            result.append(data)
        return result


@app.post("/api/intercom/sessions")
def start_intercom_session(session_data: dict, x_internal_secret: str = Header(...)):
    _require_internal_secret(x_internal_secret)
    import uuid
    from datetime import datetime
    session_id = str(uuid.uuid4())[:8]
    key = f"intercom_session:{session_id}"
    with Session(engine) as session:
        existing = session.exec(select(GlobalSetting).where(GlobalSetting.key == key)).first()
        if existing:
            raise HTTPException(status_code=409, detail=f"Session '{session_id}' already exists")
        intercom_session = GlobalSetting(
            key=key,
            value=json.dumps({
                "target_entity_ids": session_data.get("target_entity_ids", []),
                "session_type": session_data.get("session_type", "twoway"),
                "status": "active",
                "started_at": datetime.now(UTC).isoformat(),
                "ended_at": None,
                "room_name": session_data.get("target_room"),
            }),
            description=f"Intercom session: {session_id}",
        )
        session.add(intercom_session)
        session.commit()
        return {
            "session_id": session_id,
            "status": "active",
            "started_at": intercom_session.value,
            "message": f"Intercom session '{session_id}' started",
        }


@app.delete("/api/intercom/sessions/{session_id}")
def end_intercom_session(session_id: str, x_internal_secret: str = Header(...)):
    _require_internal_secret(x_internal_secret)
    from datetime import datetime
    key = f"intercom_session:{session_id}"
    with Session(engine) as session:
        intercom_session = session.exec(select(GlobalSetting).where(GlobalSetting.key == key)).first()
        if not intercom_session:
            raise HTTPException(status_code=404, detail=f"Intercom session '{session_id}' not found")
        data = json.loads(intercom_session.value)
        data["status"] = "ended"
        data["ended_at"] = datetime.now(tz=UTC).isoformat()
        intercom_session.value = json.dumps(data)
        session.commit()
        return {"status": "SUCCESS", "message": f"Intercom session '{session_id}' ended"}


@app.post("/api/intercom/broadcast")
def intercom_broadcast(broadcast_data: dict, x_internal_secret: str = Header(...)):
    _require_internal_secret(x_internal_secret)
    message = broadcast_data.get("message")
    if not message:
        raise HTTPException(status_code=400, detail="message is required")
    target_entity_ids = broadcast_data.get("target_entity_ids", [])
    target_rooms = broadcast_data.get("target_rooms", [])
    return {
        "status": "SUCCESS",
        "message": f"Broadcast queued for {len(target_entity_ids) + len(target_rooms)} targets",
        "targets_count": len(target_entity_ids) + len(target_rooms),
        "message_preview": message[:100],
    }


@app.post("/api/intercom/announce")
def intercom_announcement(announce_data: dict, x_internal_secret: str = Header(...)):
    _require_internal_secret(x_internal_secret)
    message = announce_data.get("message")
    if not message:
        raise HTTPException(status_code=400, detail="message is required")
    target_devices = announce_data.get("target_devices", [])
    return {
        "status": "SUCCESS",
        "message": f"Announcement queued for {len(target_devices)} devices",
        "targets_count": len(target_devices),
        "message_preview": message[:100],
    }


@app.get("/api/intercom/config")
def get_intercom_config(x_internal_secret: str = Header(...)):
    _require_internal_secret(x_internal_secret)
    with Session(engine) as session:
        config = session.exec(select(GlobalSetting).where(GlobalSetting.key == "intercom_config")).first()
        if config:
            return json.loads(config.value)
        return {
            "default_tts_engine": "kokoro",
            "default_voice": "af_heart",
            "default_volume": 0.8,
            "enable_espresense_routing": True,
        }


@app.get("/api/intercom/room-speakers")
def get_room_speakers(x_internal_secret: str = Header(...)):
    """Room name → media_player entity_ids map. No hardcoded defaults."""
    _require_internal_secret(x_internal_secret)
    with Session(engine) as session:
        setting = session.exec(select(GlobalSetting).where(GlobalSetting.key == "room_speakers")).first()
        if setting and setting.value:
            try:
                return {"room_speakers": json.loads(setting.value)}
            except json.JSONDecodeError:
                return {"room_speakers": {}}
        return {"room_speakers": {}}


@app.put("/api/intercom/room-speakers")
def put_room_speakers(body: dict, x_internal_secret: str = Header(...)):
    """
    Replace room→speakers mapping.
    Body: {"room_speakers": {"kitchen": ["media_player.x"], ...}}
    Values may be a string or list of entity IDs; empty map clears.
    """
    _require_internal_secret(x_internal_secret)
    raw = body.get("room_speakers", body)
    if not isinstance(raw, dict):
        raise HTTPException(status_code=400, detail="room_speakers must be an object")
    normalized: dict[str, list[str]] = {}
    for room, val in raw.items():
        if not isinstance(room, str) or not room.strip():
            raise HTTPException(status_code=400, detail="room keys must be non-empty strings")
        if val is None:
            continue
        if isinstance(val, str):
            ids = [val] if val else []
        elif isinstance(val, list):
            ids = [str(x) for x in val if x]
        else:
            raise HTTPException(status_code=400, detail=f"room '{room}' must be string or list of entity IDs")
        normalized[room.strip()] = ids
    with Session(engine) as session:
        setting = session.exec(select(GlobalSetting).where(GlobalSetting.key == "room_speakers")).first()
        value = json.dumps(normalized)
        if setting:
            setting.value = value
        else:
            session.add(GlobalSetting(
                key="room_speakers",
                value=value,
                description="Room name to media_player entity IDs for intercom/TTS routing",
            ))
        session.commit()
    return {"status": "SUCCESS", "room_speakers": normalized}


@app.patch("/api/intercom/config")
def update_intercom_config(config_data: dict, x_internal_secret: str = Header(...)):
    _require_internal_secret(x_internal_secret)
    with Session(engine) as session:
        existing = session.exec(select(GlobalSetting).where(GlobalSetting.key == "intercom_config")).first()
        current = {}
        if existing:
            current = json.loads(existing.value)
        current.update({k: v for k, v in config_data.items() if v is not None})
        if existing:
            existing.value = json.dumps(current)
        else:
            new_config = GlobalSetting(
                key="intercom_config",
                value=json.dumps(current),
                description="Intercom system configuration",
            )
            session.add(new_config)
        session.commit()
        return {"status": "SUCCESS", "message": "Intercom configuration updated", **current}


# Presence & Location

class LocationUpdate(BaseModel):
    """GPS location update from mobile app."""
    latitude: float
    longitude: float
    accuracy: float | None = None
    speed: float | None = None
    bearing: float | None = None
    battery: int | float | None = None
    #: Newer app builds say whether the phone is charging (low battery notice).
    charging: bool | None = None
    timestamp: float | None = None
    daily_steps: int | None = None


async def _forward_location_to_ha(user_id: str, location: LocationUpdate):
    """Forward location to Home Assistant via device_tracker.see REST API."""
    try:
        ha_url = None
        ha_token = None
        dev_ids: list[str] = []

        with Session(engine) as session:
            # 1. Look up user
            user = session.exec(select(User).where((User.id == user_id) | (User.username == user_id))).first()
            if user:
                ha_url = user.ha_url
                if user.ha_token_enc:
                    ha_token = decrypt(user.ha_token_enc)
                if user.username:
                    dev_ids.append(f"jarvis_{user.username.lower().replace('-', '_')}")
                    dev_ids.append(user.username.lower().replace('-', '_'))
                ha_ent = getattr(user, 'ha_entity_id', None)
                if ha_ent:
                    dev_ids.append(ha_ent.split(".")[-1])
                if hasattr(user, 'ha_device_trackers') and user.ha_device_trackers:
                    trackers = user.ha_device_trackers
                    if isinstance(trackers, str):
                        try:
                            trackers = json.loads(trackers)
                        except Exception:
                            trackers = [trackers]
                    if isinstance(trackers, list):
                        for t in trackers:
                            if isinstance(t, str):
                                dev_ids.append(t.split(".")[-1])

            # 2. Fallback to admin/default or global settings
            if not ha_url or not ha_token:
                admin = session.exec(select(User).where(User.is_admin == True)).first()  # noqa: E712
                default_user = session.exec(select(User).where(User.username == "default")).first()
                if admin and admin.ha_url and admin.ha_token_enc:
                    ha_url = ha_url or admin.ha_url
                    ha_token = ha_token or decrypt(admin.ha_token_enc)
                elif default_user and default_user.ha_url and default_user.ha_token_enc:
                    ha_url = ha_url or default_user.ha_url
                    ha_token = ha_token or decrypt(default_user.ha_token_enc)

            if not ha_url or not ha_token:
                url_setting = session.exec(select(GlobalSetting).where(GlobalSetting.key == "ha_url")).first()
                tok_setting = session.exec(select(GlobalSetting).where(GlobalSetting.key == "ha_token")).first()
                if url_setting and url_setting.value:
                    ha_url = ha_url or url_setting.value
                if tok_setting and tok_setting.value:
                    ha_token = ha_token or (decrypt(tok_setting.value) if tok_setting.value.startswith("gAAAAA") else tok_setting.value)

            if not ha_url:
                ha_url = os.getenv("HA_URL") or os.getenv("HOME_ASSISTANT_URL")
            if not ha_token:
                ha_token = os.getenv("HA_TOKEN") or os.getenv("HOME_ASSISTANT_TOKEN")

        if not ha_url or not ha_token:
            log.warning("[location] Cannot forward location to Home Assistant: HA_URL or HA_TOKEN not configured")
            return

        if not dev_ids:
            clean_name = re.sub(r'[^a-zA-Z0-9_]', '_', user_id.lower())
            dev_ids.append(f"jarvis_{clean_name}")

        # Unique dev_ids
        seen = set()
        unique_dev_ids = [d for d in dev_ids if not (d in seen or seen.add(d))]

        async with get_client_insecure() as client:
            for dev_id in unique_dev_ids:
                payload = {
                    "dev_id": dev_id,
                    "gps": [location.latitude, location.longitude],
                    "gps_accuracy": int(location.accuracy or 0),
                    "source_type": "gps",
                }
                if location.battery is not None:
                    payload["battery"] = int(location.battery)
                # device_tracker.see has no speed or course fields: sent at the
                # top level they made HA answer 400 to every update, so the app's
                # location never reached Home Assistant. Extra values go under
                # attributes, which the service accepts.
                extra = {}
                if location.speed is not None:
                    extra["speed"] = location.speed
                if location.bearing is not None:
                    extra["course"] = location.bearing
                if extra:
                    payload["attributes"] = extra

                try:
                    async with client.post(
                        f"{ha_url.rstrip('/')}/api/services/device_tracker/see",
                        headers={"Authorization": f"Bearer {ha_token}", "Content-Type": "application/json"},
                        json=payload,
                        timeout=aiohttp.ClientTimeout(total=5.0),
                    ) as resp:
                        if resp.status < 300:
                            log.info(f"[location] Forwarded location to Home Assistant for dev_id={dev_id}")
                        else:
                            resp_text = await resp.text()
                            log.warning(f"[location] HA device_tracker.see status {resp.status} for dev_id={dev_id}: {resp_text}")
                except Exception as post_err:
                    log.warning(f"[location] Failed to post device_tracker.see to HA for dev_id={dev_id}: {post_err}")
    except Exception as e:
        log.warning(f"[location] Error forwarding location to Home Assistant: {e}")


async def _forward_location_to_geo(user_id: str, location: LocationUpdate):
    """Forward location update to Geo service to record telemetry breadcrumbs."""
    try:
        from services.common.http import get_client_insecure
        from services.config import GEO_SVC_URL, INTERNAL_SECRET
        if not GEO_SVC_URL:
            return
        geo_url = GEO_SVC_URL.rstrip("/")
        clean_user = user_id.split(".")[-1].lower()
        payload = {
            "latitude": location.latitude,
            "longitude": location.longitude,
            "gps_accuracy": int(location.accuracy) if location.accuracy is not None else None,
            "speed": location.speed,
            "bearing": location.bearing,
            "battery": int(location.battery) if location.battery is not None else None,
            "timestamp": location.timestamp,
        }
        if location.daily_steps is not None:
            payload["daily_steps"] = location.daily_steps
        async with get_client_insecure() as client, client.post(
            f"{geo_url}/people/{clean_user}/record",
            headers={"X-Internal-Secret": INTERNAL_SECRET, "Content-Type": "application/json"},
            json=payload,
            timeout=aiohttp.ClientTimeout(total=3.0),
        ) as resp:
            if resp.status < 300:
                log.info(f"[location] Recorded breadcrumb in Geo service for {clean_user}")
            else:
                log.warning(f"[location] Geo record status {resp.status} for {clean_user}")
    except Exception as e:
        log.warning(f"[location] Failed to record breadcrumb in Geo service: {e}")


def _resolve_location_user_key(session: Session, user_id: str) -> str:
    """Normalize location storage key: accept numeric DB ids or usernames."""
    raw = (user_id or "").strip()
    if not raw:
        return raw
    if raw.isdigit():
        user = session.exec(select(User).where(User.id == int(raw))).first()
        if user and user.username:
            return user.username.lower()
    return raw.lower()


@app.post("/api/users/{user_id}/location")
async def update_user_location(
    user_id: str,
    location: LocationUpdate,
    background: BackgroundTasks,
    x_internal_secret: str | None = Header(None),
):
    """Store user GPS location from mobile app, forward to Home Assistant, and record in Geo service.

    Header is optional in the signature so a missing secret returns 403 (not FastAPI's 422),
    matching the rest of the identity internal endpoints. A low battery
    announcement runs after the response, so a slow telemetry never holds up
    or fails the upload.
    """
    _require_internal_secret(x_internal_secret)
    import time
    location_data = {
        "latitude": location.latitude,
        "longitude": location.longitude,
        "accuracy": location.accuracy,
        "speed": location.speed,
        "bearing": location.bearing,
        "battery": location.battery,
        "timestamp": location.timestamp or time.time(),
        "updated_at": time.time(),
    }
    low_battery = None
    with Session(engine) as session:
        person = _resolve_location_user_key(session, user_id)
        key = f"user_location:{person}"
        existing = session.exec(select(GlobalSetting).where(GlobalSetting.key == key)).first()
        try:
            previous = json.loads(existing.value) if existing else None
        except (TypeError, ValueError):
            previous = None
        announce, location_data[battery_alerts.SENT_FIELD] = battery_alerts.step(
            previous, location.battery, location.charging)
        if announce:
            owner = session.exec(select(User).where(User.username == person)).first()
            family = [u.username for u in session.exec(select(User)).all() if not u.is_system_default]
            low_battery = (person, (owner.display_name if owner else "") or person.title(), family)
        if existing:
            existing.value = json.dumps(location_data)
        else:
            new_location = GlobalSetting(
                key=key,
                value=json.dumps(location_data),
                description=f"GPS location for user {user_id}",
            )
            session.add(new_location)
        session.commit()
    # No coordinates in the log: it is kept and read far more widely than the map.
    log.info(f"[location] Updated location for {user_id} (±{int(location.accuracy or 0)} m)")
    if low_battery:
        name, display, family = low_battery
        background.add_task(battery_alerts.announce, name, display, location.battery, family)

    # Location forwarding to HA and Geo can be slow or subject to external timeouts;
    # run them in background tasks so location uploads never hold up the mobile client
    # or cause gateway middleware timeouts.
    background.add_task(_forward_location_to_ha, user_id, location)
    background.add_task(_forward_location_to_geo, user_id, location)

    return {"status": "SUCCESS", "message": "Location updated"}


@app.get("/api/users/{user_id}/location")
def get_user_location(
    user_id: str,
    x_internal_secret: str | None = Header(None),
):
    """Get stored GPS location for a user (username or numeric id)."""
    _require_internal_secret(x_internal_secret)
    with Session(engine) as session:
        key = f"user_location:{_resolve_location_user_key(session, user_id)}"
        location = session.exec(select(GlobalSetting).where(GlobalSetting.key == key)).first()
        if not location and user_id != user_id.lower():
            location = session.exec(
                select(GlobalSetting).where(GlobalSetting.key == f"user_location:{user_id.lower()}")
            ).first()
        if location:
            return json.loads(location.value)
    raise HTTPException(status_code=404, detail="Location not found")


@app.get("/api/users/location/all")
def get_all_user_locations(x_internal_secret: str = Header(...)):
    """Get GPS locations for all users."""
    _require_internal_secret(x_internal_secret)
    locations = {}
    with Session(engine) as session:
        settings = session.exec(select(GlobalSetting).where(text("globalsetting.key LIKE 'user_location:%'"))).all()
        for setting in settings:
            user_id = setting.key.replace("user_location:", "")
            locations[user_id] = json.loads(setting.value)
    return locations


# ---------------------------------------------------------------------------
# User Panel: device registry
#
# Phones self-register when they log in; assistants and lights have no account
# of their own, so an admin registers them. Either way the row is owned by a
# user, which is what separates this from execution.device_registry (a list of
# discovered network entities with no owner).
# ---------------------------------------------------------------------------


def _device_to_read(d: Device) -> DeviceRead:
    try:
        caps = json.loads(d.capabilities or "{}")
    except (TypeError, ValueError):
        caps = {}
    return DeviceRead(
        id=d.id,
        device_key=d.device_key,
        kind=d.kind,
        label=d.label or "",
        owner_username=d.owner_username,
        registered_by=d.registered_by,
        revoked=d.revoked,
        entity_id=d.entity_id,
        model=d.model,
        manufacturer=d.manufacturer,
        os_version=d.os_version,
        app_version=d.app_version,
        app_build=d.app_build,
        esphome_version=d.esphome_version,
        hardware=d.hardware,
        capabilities=caps if isinstance(caps, dict) else {},
        last_ip_address=d.last_ip_address,
        last_seen_at=d.last_seen_at,
        first_seen_at=d.first_seen_at,
    )


#: Battery reports kept per device. A watch reports every few minutes, so
#: without a limit the table grows by a few hundred rows a day per device.
BATTERY_RETENTION_DAYS = 30


def _battery_reading(event: DeviceEvent) -> BatteryReading | None:
    try:
        extra = json.loads(event.extra or "{}")
    except (TypeError, ValueError):
        return None
    if not isinstance(extra, dict):
        return None

    def num(key):
        value = extra.get(key)
        return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None

    usb = extra.get("usb")
    return BatteryReading(at=event.at, pct=num("pct"), usb=usb if isinstance(usb, bool) else None,
                          cell_v=num("cell_v"))


def _latest_batteries(session: Session, device_keys: list[str]) -> dict[str, BatteryReading]:
    """Each device's newest battery report (looking back a week)."""
    if not device_keys:
        return {}
    since = (datetime.now() - timedelta(days=7)).isoformat()
    rows = session.exec(
        select(DeviceEvent)
        .where(DeviceEvent.event == "battery", DeviceEvent.device_key.in_(device_keys), DeviceEvent.at >= since)
        .order_by(DeviceEvent.at.desc())
    ).all()
    latest: dict[str, BatteryReading] = {}
    for row in rows:
        if row.device_key not in latest:
            reading = _battery_reading(row)
            if reading:
                latest[row.device_key] = reading
    return latest


def _client_ip(request: Request) -> str | None:
    """The caller's address, from the request rather than the body.

    A client-supplied IP would let a caller write an arbitrary address into
    someone else's device row, which then shows up in the panel as fact.
    """
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else None


@app.post("/api/user-panel/devices/register", response_model=DeviceRead)
def register_device(
    body: DeviceSelfRegister,
    request: Request,
    session: Session = Depends(get_session),
    caller: User = Depends(require_api_key),
):
    """Register or refresh the caller's own phone or tablet, on login.

    Idempotent on ``device_key``: logging in repeatedly updates the existing
    row instead of creating a new device each time, which is why the key is
    unique in the schema rather than cleaned up here.

    The client may declare only its mobile form factor (``phone`` or ``tablet``),
    which the schema restricts; it can never name a non-mobile kind, so an app
    cannot move itself out of the advanced panel. An older client that sends no
    form factor is treated as a phone. A row already registered by an admin, or
    one whose kind is not mobile (an assistant, a light, a watch), is left
    alone: whoever holds its key must not silently reclassify it. A row that is
    self-registered mobile may be corrected between phone and tablet, so a
    device that first reported as a phone and now knows it is a tablet is
    believed.
    """
    key = body.device_key.strip()
    now = datetime.now().isoformat()
    device = session.exec(select(Device).where(Device.device_key == key)).first()
    if device is None:
        device = Device(
            device_key=key,
            kind=body.form_factor or "phone",
            registered_by="self",
            first_seen_at=now,
        )
        session.add(device)
    elif device.registered_by == "admin" or device.kind not in MOBILE_DEVICE_KINDS:
        # A device already registered as an assistant/light/watch must not be
        # silently reclassified by whoever holds its key.
        raise HTTPException(status_code=409, detail="This device key is already registered as another kind")
    elif body.form_factor and body.form_factor != device.kind:
        # The same install, corrected: it first reported as a phone and now
        # reports as a tablet (or the other way round).
        device.kind = body.form_factor

    device.owner_username = caller.username
    device.model = body.model or device.model
    device.manufacturer = body.manufacturer or device.manufacturer
    device.os_version = body.os_version or device.os_version
    device.os_build = body.os_build or device.os_build
    device.app_version = body.app_version or device.app_version
    device.app_build = body.app_build or device.app_build
    device.last_ip_address = _client_ip(request)
    device.last_seen_at = now
    session.add(device)
    session.commit()
    session.refresh(device)
    return _device_to_read(device)


@app.get("/api/user-panel/devices", response_model=list[DeviceRead])
def list_user_panel_devices(
    session: Session = Depends(get_session),
    caller: User = Depends(require_api_key),
):
    """Devices visible to the caller: their own, plus every device if admin.

    A non-admin sees only what they own, so the endpoint is safe to call from
    the app itself and not just from an admin screen.
    """
    stmt = select(Device)
    if not caller.is_admin:
        stmt = stmt.where(Device.owner_username == caller.username)
    devices = session.exec(stmt).all()
    batteries = _latest_batteries(session, [d.device_key for d in devices])
    out = []
    for d in devices:
        read = _device_to_read(d)
        read.battery = batteries.get(d.device_key)
        out.append(read)
    return out


@app.get("/api/user-panel/devices/{device_key}/battery", response_model=list[BatteryReading])
def device_battery_history(
    device_key: str,
    hours: int = Query(24, ge=1, le=BATTERY_RETENTION_DAYS * 24),
    session: Session = Depends(get_session),
    caller: User = Depends(require_api_key),
):
    """A device's battery reports over the last ``hours``, oldest first.
    Its owner's, or any device for an admin."""
    device = session.exec(select(Device).where(Device.device_key == device_key)).first()
    if device is None:
        raise HTTPException(status_code=404, detail="No such device")
    if device.owner_username != caller.username and not caller.is_admin:
        raise HTTPException(status_code=403, detail="Not your device")
    since = (datetime.now() - timedelta(hours=hours)).isoformat()
    rows = session.exec(
        select(DeviceEvent)
        .where(DeviceEvent.device_key == device_key, DeviceEvent.event == "battery", DeviceEvent.at >= since)
        .order_by(DeviceEvent.at)
    ).all()
    return [r for r in (_battery_reading(row) for row in rows) if r]


def _device_event_to_read(event: DeviceEvent) -> DeviceEventRead:
    try:
        extra = json.loads(event.extra or "{}")
    except (TypeError, ValueError):
        # A row whose extras will not parse is still an event that happened, so
        # the timeline keeps it and drops only the part it cannot read.
        extra = {}
    return DeviceEventRead(
        event=event.event,
        at=event.at,
        extra=extra if isinstance(extra, dict) else {},
    )


@app.get("/api/user-panel/devices/{device_key}/activity", response_model=DeviceActivity)
def device_activity(
    device_key: str,
    hours: int = Query(24, ge=1, le=BATTERY_RETENTION_DAYS * 24),
    limit: int = Query(50, ge=1, le=200),
    session: Session = Depends(get_session),
    caller: User = Depends(require_api_key),
):
    """What one device has reported lately: its record, event counts and its
    most recent events, newest first.

    This is what a device row opens into. Every event comes from the no-opt-in
    allowlist (see NO_OPT_IN_EVENTS), so the payload is activity and small
    scalars -- a battery percentage, a step count, an app version -- and never
    a transcript or a coordinate. Its owner's, or any device for an admin.
    """
    device = session.exec(select(Device).where(Device.device_key == device_key)).first()
    if device is None:
        raise HTTPException(status_code=404, detail="No such device")
    if device.owner_username != caller.username and not caller.is_admin:
        raise HTTPException(status_code=403, detail="Not your device")
    since = (datetime.now() - timedelta(hours=hours)).isoformat()
    rows = session.exec(
        select(DeviceEvent)
        .where(DeviceEvent.device_key == device_key, DeviceEvent.at >= since)
        .order_by(DeviceEvent.at.desc())
    ).all()
    counts: dict[str, int] = {}
    for row in rows:
        counts[row.event] = counts.get(row.event, 0) + 1
    read = _device_to_read(device)
    read.battery = _latest_batteries(session, [device_key]).get(device_key)
    return DeviceActivity(
        device=read,
        counts=counts,
        events=[_device_event_to_read(row) for row in rows[:limit]],
        first_seen_at=device.first_seen_at,
        last_seen_at=device.last_seen_at,
    )


@app.post("/api/user-panel/devices", response_model=DeviceRead)
def create_device(
    body: DeviceAdminCreate,
    request: Request,
    session: Session = Depends(get_session),
    admin: User = Depends(require_api_key),
):
    """Register an assistant or light. Admin only.

    These have no account to log in with, so an admin declares them and assigns
    the owner. A phone or tablet must not be created here: it registers itself.
    """
    if not admin.is_admin:
        raise HTTPException(status_code=403, detail="Admin only")
    kind = (body.kind or "").strip().lower()
    if kind not in DEVICE_KINDS:
        raise HTTPException(status_code=422, detail=f"kind must be one of {', '.join(DEVICE_KINDS)}")
    if kind in MOBILE_DEVICE_KINDS:
        raise HTTPException(
            status_code=422,
            detail="Phones and tablets register themselves; use /api/user-panel/devices/register",
        )
    if body.owner_username:
        owner = session.exec(select(User).where(User.username == body.owner_username.lower())).first()
        if owner is None:
            raise HTTPException(status_code=404, detail=f"No such user: {body.owner_username}")
    key = body.device_key.strip()
    if session.exec(select(Device).where(Device.device_key == key)).first():
        raise HTTPException(status_code=409, detail="device_key already registered")
    now = datetime.now().isoformat()
    device = Device(
        device_key=key,
        kind=kind,
        label=body.label or "",
        owner_username=body.owner_username.lower() if body.owner_username else None,
        registered_by="admin",
        entity_id=body.entity_id,
        esphome_version=body.esphome_version,
        hardware=body.hardware,
        capabilities=json.dumps(body.capabilities or {}),
        last_ip_address=_client_ip(request),
        last_seen_at=now,
        first_seen_at=now,
    )
    session.add(device)
    session.commit()
    session.refresh(device)
    return _device_to_read(device)


@app.post("/api/internal/devices/claim", response_model=DeviceRead)
def claim_device(
    body: DeviceClaim,
    session: Session = Depends(get_session),
    _: None = Depends(require_internal),
):
    """Link a companion device (watch, assistant, light) to the user who added it.

    Internal only: the execution service calls this after it has paired with
    the device (``verified``: the user typed the code the device showed) or
    adopted one that has no screen. A device already linked to someone else
    moves only on a verified pairing, which needs the device in hand; an
    unverified adoption of someone else's device is refused, so holding a
    device's address is not enough to take it.
    """
    kind = (body.kind or "").strip().lower()
    if kind not in DEVICE_KINDS or kind in MOBILE_DEVICE_KINDS:
        raise HTTPException(status_code=422, detail=f"kind must be one of {', '.join(k for k in DEVICE_KINDS if k not in MOBILE_DEVICE_KINDS)}")
    owner_name = body.owner_username.strip().lower()
    owner = session.exec(select(User).where(User.username == owner_name)).first()
    if owner is None:
        raise HTTPException(status_code=404, detail=f"No such user: {owner_name}")
    key = body.device_key.strip()
    now = datetime.now().isoformat()
    device = session.exec(select(Device).where(Device.device_key == key)).first()
    if device is not None:
        if device.kind in MOBILE_DEVICE_KINDS:
            raise HTTPException(status_code=409, detail="This device key belongs to a phone or tablet")
        if device.owner_username and device.owner_username != owner_name and not body.verified:
            raise HTTPException(
                status_code=409,
                detail=f"Already linked to {device.owner_username}. Pair it with the code it shows, or ask an admin.",
            )
    else:
        device = Device(device_key=key, kind=kind, first_seen_at=now)
    device.kind = kind
    device.label = body.label or device.label or ""
    device.owner_username = owner_name
    device.registered_by = "paired" if body.verified else "adopted"
    device.revoked = False
    device.esphome_version = body.esphome_version or device.esphome_version
    device.app_version = body.app_version or device.app_version
    device.hardware = body.hardware or device.hardware
    if body.capabilities:
        device.capabilities = json.dumps(body.capabilities)
    device.last_ip_address = body.ip_address or device.last_ip_address
    device.last_seen_at = now
    session.add(device)
    session.commit()
    session.refresh(device)
    return _device_to_read(device)


@app.patch("/api/user-panel/devices/{device_key}", response_model=DeviceRead)
def update_device(
    device_key: str,
    body: DeviceAssign,
    request: Request,
    session: Session = Depends(get_session),
    admin: User = Depends(require_api_key),
):
    """Assign or reassign a device to a user, or unassign it with an empty
    owner_username. Admin only.

    Assignment stays admin-side on purpose: a device being able to claim an
    owner would let anyone reassign the family's assistant by holding its key.
    """
    if not admin.is_admin:
        raise HTTPException(status_code=403, detail="Admin only")
    device = session.exec(select(Device).where(Device.device_key == device_key)).first()
    if device is None:
        raise HTTPException(status_code=404, detail="No such device")
    if body.owner_username == "":
        device.owner_username = None
    elif body.owner_username:
        owner = session.exec(select(User).where(User.username == body.owner_username.lower())).first()
        if owner is None:
            raise HTTPException(status_code=404, detail=f"No such user: {body.owner_username}")
        device.owner_username = body.owner_username.lower()
    session.add(device)
    session.commit()
    session.refresh(device)
    return _device_to_read(device)


@app.post("/api/user-panel/devices/telemetry")
def ingest_telemetry(
    body: TelemetryIngest,
    request: Request,
    background: BackgroundTasks,
    session: Session = Depends(get_session),
    caller: User = Depends(require_api_key),
):
    """Record usage events for one of the caller's own devices.

    ``event`` is checked against NO_OPT_IN_EVENTS, an allowlist, so content
    (a transcript, a coordinate, a vital) cannot be smuggled in under a name
    that happens to sound innocuous -- the check fails closed and reports which
    names were rejected.

    ``extra`` is stored as given; it is the client's responsibility to send
    scalars. The size is capped so a client cannot turn this into a blob store.
    """
    device = session.exec(select(Device).where(Device.device_key == body.device_key.strip())).first()
    if device is None:
        raise HTTPException(status_code=404, detail="Register this device before sending telemetry")
    if device.owner_username and device.owner_username != caller.username and not caller.is_admin:
        raise HTTPException(status_code=403, detail="Not your device")

    rejected: list[str] = []
    now = datetime.now().isoformat()
    written = 0
    battery_readings: list[tuple[float | None, bool | None]] = []
    for raw in body.events[:200]:
        event = str(raw.get("event", "")).strip()
        if event not in NO_OPT_IN_EVENTS:
            rejected.append(event or "<missing>")
            continue
        extra = raw.get("extra")
        if not isinstance(extra, dict):
            extra = {"value": extra} if extra is not None else {}
        encoded = json.dumps(extra)
        if len(encoded) > 2000:
            rejected.append(f"{event} (extra too large)")
            continue
        session.add(
            DeviceEvent(
                device_key=device.device_key,
                username=caller.username,
                event=event,
                extra=encoded,
                at=str(raw.get("at") or now),
            )
        )
        written += 1
        if event == "battery":
            pct = extra.get("pct")
            usb = extra.get("usb")
            battery_readings.append((
                float(pct) if isinstance(pct, (int, float)) and not isinstance(pct, bool) else None,
                usb if isinstance(usb, bool) else None,
            ))

    device.last_seen_at = now
    device.last_ip_address = _client_ip(request)
    if body.app_version:
        device.app_version = body.app_version.strip()
    session.add(device)

    watch_alert = None
    if device.kind == "watch" and battery_readings:
        state_key = f"{battery_alerts.WATCH_STATE_PREFIX}{device.device_key}"
        row = session.exec(select(GlobalSetting).where(GlobalSetting.key == state_key)).first()
        try:
            state = json.loads(row.value) if row else None
        except (TypeError, ValueError):
            state = None
        if not isinstance(state, dict):
            state = {}
        announced_at = None
        for pct, usb in battery_readings:
            announce, sent = battery_alerts.step(state, pct, usb)
            state = {**state, "battery": pct, battery_alerts.SENT_FIELD: sent}
            if announce and announced_at is None:
                announced_at = pct
        encoded_state = json.dumps(state)
        if row:
            row.value = encoded_state
        else:
            session.add(GlobalSetting(
                key=state_key,
                value=encoded_state,
                description=f"Low battery alert state for {device.device_key}",
            ))
        if announced_at is not None:
            owner_username = device.owner_username or caller.username
            owner = session.exec(select(User).where(User.username == owner_username)).first()
            family = [u.username for u in session.exec(select(User)).all() if not u.is_system_default]
            label = device.label or f"{(owner.display_name if owner else owner_username.title())}'s watch"
            watch_alert = (device.device_key, label, announced_at, family, owner_username)

    if any(str(raw.get("event", "")).strip() == "battery" for raw in body.events[:200]):
        cutoff = (datetime.now() - timedelta(days=BATTERY_RETENTION_DAYS)).isoformat()
        for old in session.exec(
            select(DeviceEvent).where(DeviceEvent.device_key == device.device_key,
                                      DeviceEvent.event == "battery", DeviceEvent.at < cutoff)
        ).all():
            session.delete(old)
    session.commit()
    if watch_alert:
        background.add_task(battery_alerts.announce_watch, *watch_alert)
    if rejected:
        log.warning(
            "[devices] rejected %d telemetry event(s) not in NO_OPT_IN_EVENTS: %s",
            len(rejected),
            ", ".join(sorted(set(rejected))[:10]),
        )
    return {"written": written, "rejected": sorted(set(rejected))}


@app.post("/api/user-panel/devices/{device_key}/capabilities", response_model=DeviceRead)
def report_capabilities(
    device_key: str,
    body: dict,
    session: Session = Depends(get_session),
    caller: User = Depends(require_api_key),
):
    """Record what a device can currently do, versioned over time.

    A capability gap only means something relative to what the device could do
    at the time, so the inventory is appended rather than overwritten.
    """
    device = session.exec(select(Device).where(Device.device_key == device_key)).first()
    if device is None:
        raise HTTPException(status_code=404, detail="No such device")
    if device.owner_username and device.owner_username != caller.username and not caller.is_admin:
        raise HTTPException(status_code=403, detail="Not your device")
    caps = body.get("capabilities")
    if not isinstance(caps, dict):
        raise HTTPException(status_code=422, detail="capabilities must be an object")
    encoded = json.dumps(caps)
    if len(encoded) > 8000:
        raise HTTPException(status_code=422, detail="capabilities too large")
    now = datetime.now().isoformat()
    session.add(
        CapabilityInventory(
            device_key=device.device_key,
            capabilities=encoded,
            esphome_version=body.get("esphome_version") or device.esphome_version,
            observed_at=now,
        )
    )
    device.capabilities = encoded
    device.esphome_version = body.get("esphome_version") or device.esphome_version
    session.add(device)
    session.commit()
    session.refresh(device)
    return _device_to_read(device)
