# services/identity/main.py
"""
Microservice 1: Identity & Profile Service
Manages user profiles, device assignments, and secure credential resolution.
"""
import hmac
import json
import logging
import os
import re
import time
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from datetime import datetime as dt

import aiohttp
from fastapi import Depends, FastAPI, File, Header, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import inspect, text
from sqlmodel import Session, SQLModel, create_engine, select

from services.common.http import get_client, get_client_insecure
from services.config import IDENTITY_DATABASE_URL, INTERNAL_SECRET
from services.identity.crypto import decrypt, digest_secret, encrypt
from services.identity.models import (
    DEFAULT_GLOBAL_SETTINGS,
    ADVANCED_DEVICE_KINDS,
    DEVICE_KINDS,
    NO_OPT_IN_EVENTS,
    APIKey,
    CapabilityInventory,
    Device,
    DeviceAssignment,
    DeviceEvent,
    FeatureUsage,
    DnsRecord,
    EntityProtection,
    GlobalSetting,
    RavenMission,
    User,
    UserActivitySharing,
    UserCalendarSetting,
    UserCredentialShare,
    UserThemeSetting,
    UserWidget,
)
from services.identity.schemas import (
    ChangePasswordRequest,
    CredentialSharesRead,
    CredentialSharesUpdate,
    DeviceAdminCreate,
    DeviceAssign,
    DeviceAssignmentCreate,
    DeviceAssignmentRead,
    DeviceRead,
    DeviceSelfRegister,
    TelemetryIngest,
    DiscoverResponse,
    EntityProtectionRead,
    EntityProtectionUpdate,
    DiscoverUser,
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
    ShareRecipient,
    UserCreate,
    UserRead,
    UserUpdate,
    UserWidgetRead,
    UserWidgetUpdate,
    WidgetSettingsRead,
)
from services.identity.seed import hash_password, seed_from_env, verify_password
from services.shared.info_endpoint import info_router

# ─── Config ────────────────────────────────────────────────────────────────────

log = logging.getLogger("identity")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s")


def _require_internal_secret(x_internal_secret: str | None) -> None:
    expected = os.getenv("INTERNAL_SECRET", INTERNAL_SECRET)
    if not x_internal_secret or not (
        (expected and hmac.compare_digest(x_internal_secret, expected))
        or (INTERNAL_SECRET and hmac.compare_digest(x_internal_secret, INTERNAL_SECRET))
    ):
        raise HTTPException(status_code=403, detail="Forbidden")

DATABASE_URL = IDENTITY_DATABASE_URL or "sqlite:///default.db"

if "sqlite" in DATABASE_URL:
    engine = create_engine(
        DATABASE_URL,
        connect_args={"check_same_thread": False, "timeout": 30},
        pool_pre_ping=True
    )
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
        _add_column("user", "skylight_email", "ALTER TABLE user ADD COLUMN skylight_email VARCHAR")
        _add_column("user", "skylight_pass_enc", "ALTER TABLE user ADD COLUMN skylight_pass_enc VARCHAR")
        _add_column("user", "git_url", "ALTER TABLE user ADD COLUMN git_url VARCHAR")
        _add_column("user", "git_user", "ALTER TABLE user ADD COLUMN git_user VARCHAR")
        _add_column("user", "git_token_enc", "ALTER TABLE user ADD COLUMN git_token_enc VARCHAR")
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


# ─── Credential sharing policy ────────────────────────────────────────────────
# Every user resolves a service with their OWN credentials. The system default
# user ("User 1": is_system_default, or id 1, or username "default" — it may
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
    # Model settings default to "" (unconfigured) — they MUST be set explicitly
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
            log.info("[lifespan] FORCE_RESEED=true — re-seeding all data")
        seed_from_env(session, force=force_reseed)
        await _ensure_default_settings(session)
        _migrate_api_key_material(session)
    yield

app = FastAPI(title="Jarvis OS Identity Service", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(info_router)

# ─── Dependencies ──────────────────────────────────────────────────────────────

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
        (expected and hmac.compare_digest(val, expected))
        or (INTERNAL_SECRET and hmac.compare_digest(val, INTERNAL_SECRET))
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
def admin_set_password(username: str, req: dict, session: Session = Depends(get_session), admin: User = Depends(require_admin_or_internal)):
    new_password = req.get("new_password")
    if not new_password:
        raise HTTPException(status_code=400, detail="new_password is required")

    user = session.exec(select(User).where(User.username == username)).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    user.password_hash = hash_password(new_password)
    session.add(user)
    session.commit()
    return {"status": "SUCCESS", "message": f"Password for @{username} updated"}

def require_api_key(authorization: str = Header(None), session: Session = Depends(get_session)) -> User:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing API Key")
    key = authorization.split(" ")[1]

    user = _find_user_for_api_key(session, key)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid API Key")
    return user

# ─── Internal API ─────────────────────────────────────────────────────────────

@app.post("/api/resolve", response_model=ResolvedCredentials)
def resolve_identity(req: ResolveRequest, session: Session = Depends(get_session), _: None = Depends(require_internal)):
    """
    Downstream services call this to get decrypted credentials for a user.
    """
    user = None
    log.debug(f"[resolve] Input: user_id={req.user_id}, api_key={'***' + req.api_key[-4:] if req.api_key and len(req.api_key) > 4 else req.api_key}, rag_user={req.rag_user}, voice_id={req.voice_id}, device_id={req.device_id}")

    # Resolve by user ID (integer primary key)
    if req.user_id is not None:
        user = session.exec(select(User).where(User.id == req.user_id)).first()
        if user:
            log.info(f"[resolve] Resolved by user_id={req.user_id}: user={user.username}")
        else:
            log.warning(f"[resolve] No user found for user_id={req.user_id}")

    # Resolve by API Key first (for OpenWebUI & UI clients)
    if not user and req.api_key:
        user = _find_user_for_api_key(session, req.api_key)

    if not user and req.rag_user:
        user = session.exec(select(User).where(User.username == req.rag_user.lower())).first()
        if user:
            log.info(f"[resolve] Resolved by rag_user={req.rag_user}: user={user.username}")
        else:
            log.warning(f"[resolve] No user found for rag_user={req.rag_user}")

    if not user and req.voice_id:
        # Search for user by voice_id (username or biometric match)
        user = session.exec(select(User).where(User.username == req.voice_id.lower())).first()
        if user:
            log.info(f"[resolve] Resolved by voice_id={req.voice_id}: user={user.username}")
        else:
            log.warning(f"[resolve] No user found for voice_id={req.voice_id}")

    if not user and req.device_id:
        assignment = session.exec(select(DeviceAssignment).where(DeviceAssignment.device_id == req.device_id)).first()
        if assignment and not assignment.revoked:
            user = assignment.user
            log.info(f"[resolve] Resolved by device_id={req.device_id}: user={user.username}")
        elif assignment and assignment.revoked:
            log.warning(f"[resolve] Device {req.device_id} assignment revoked, skipping")
        else:
            log.warning(f"[resolve] No device assignment found for device_id={req.device_id}")

    if not user:
        log.warning("[resolve] No resolution path matched, falling back to system default")
        # Fallback to system account (ID 1)
        user = session.exec(select(User).where(User.id == 1)).first()
        if user:
            log.info(f"[resolve] Fallback resolved to system default (ID=1): user={user.username}")
        else:
            # Last resort fallback to "default" username if ID 1 somehow missing
            user = session.exec(select(User).where(User.username == "default")).first()
            if user:
                log.info(f"[resolve] Fallback resolved to system default (username='default'): user={user.username}")
            else:
                log.error("[resolve] No system default user found in database!")
                raise HTTPException(status_code=404, detail="No valid identity found")

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
    # configured system email — unless they configured an account of their own.
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

    log.info(
        f"[resolve] Returning credentials for user={user.username}, "
        f"credential_sources={credential_sources}"
    )

    return ResolvedCredentials(
        user=user.username,
        id=user.id,
        is_admin=user.is_admin,
        api_key=decrypt(user.api_key_enc) if user.api_key_enc else req.api_key,
        nextcloud_url=nextcloud["nextcloud_url"],
        nextcloud_user=nextcloud["nextcloud_user"],
        nextcloud_pass=nextcloud["nextcloud_pass"],
        ha_url=ha["ha_url"],
        ha_token=ha["ha_token"],
        github_url=user.github_url,
        github_user=user.github_user,
        github_token=decrypt(user.github_token_enc) if user.github_token_enc else None,
        gitlab_url=user.gitlab_url,
        gitlab_user=user.gitlab_user,
        gitlab_token=decrypt(user.gitlab_token_enc) if user.gitlab_token_enc else None,
        audiobookshelf_url=abs_["audiobookshelf_url"],
        audiobookshelf_user=abs_["audiobookshelf_user"],
        audiobookshelf_pass=abs_["audiobookshelf_pass"],
        audiobookshelf_api_key=abs_["audiobookshelf_api_key"],
        mailcow_url=user.mailcow_url,
        mailcow_api_key=decrypt(user.mailcow_api_key_enc) if user.mailcow_api_key_enc else None,
        mail_user=user.mail_user,
        mail_pass=decrypt(user.mail_pass_enc) if user.mail_pass_enc else None,
        mass_url=mass["mass_url"],
        mass_token=mass["mass_token"],
        git_url=user.git_url,
        git_user=user.git_user,
        git_token=decrypt(user.git_token_enc) if user.git_token_enc else None,
        huggingface_token=decrypt(user.huggingface_token_enc) if user.huggingface_token_enc else None,
        skylight_url=skylight_user.skylight_url if skylight_user else None,
        skylight_email=(skylight_user.skylight_email if skylight_user else None) or user.username,
        skylight_pass=(
            decrypt(skylight_user.skylight_pass_enc)
            if skylight_user and skylight_user.skylight_pass_enc
            else None
        ),
        skylight_enabled=user.skylight_enabled,
        preferred_tts_voice=user.preferred_tts_voice or "af_heart",
        calendar_settings=_load_calendar_settings(session, user.username),
        credential_sources=credential_sources,
        shared_credential_owner=shared_owner,
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


def _coerce_profile_value(key: str, value):
    """Normalize a profile update field without nulling valid falsy values.

    Booleans like False must stay False (NOT NULL columns). Only blank optional
    strings become None; display_name keeps "" so UserRead validation passes.
    """
    if isinstance(value, str):
        value = value.strip()
        if value == "" and key != "display_name":
            return None
        return value
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

# ─── Public/Admin API ──────────────────────────────────────────────────────────

@app.get("/api/users/me", response_model=UserRead)
def get_me(user: User = Depends(require_api_key)):
    return user

@app.post("/api/users/me/enroll")
async def enroll_voice(
    file: UploadFile = File(...),
    user: User = Depends(require_api_key),
    session: Session = Depends(get_session)
):
    """
    Receives an audio file and generates a voice fingerprint.
    For now, we simulate this by hashing the file content.
    """
    try:
        import hashlib
        content = await file.read()
        # Simulation: In a real system, we'd run a model here.
        # For now, we'll store a mock fingerprint based on the file content.
        fingerprint = hashlib.sha256(content).hexdigest()

        user.voice_fingerprint = f"v1:{fingerprint[:16]}"
        session.add(user)
        session.commit()

        log.info(f"User {user.username} enrolled with voice fingerprint {user.voice_fingerprint}")
        return {"status": "SUCCESS", "message": "Voice profile successfully enrolled."}
    except Exception as e:
        log.error(f"Enrollment failed: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from None

@app.patch("/api/users/me", response_model=UserRead)
def update_me(body: UserUpdate, session: Session = Depends(get_session), user: User = Depends(require_api_key)):
    log.info(f"[update_me] Received update for {user.username}: {body.model_dump(exclude_unset=True)}")
    update_data = body.model_dump(exclude_unset=True)

    # Privilege fields are never self-assignable here; admins use
    # PATCH /api/users/{username} which enforces is_admin.
    for privilege_field in ("is_admin", "is_system_default"):
        if privilege_field in update_data:
            log.warning(
                f"[update_me] {user.username} attempted self-update of "
                f"'{privilege_field}'; ignored"
            )
            del update_data[privilege_field]

    # Prevent non-default users from changing system skylight integration credentials
    if any(k in update_data for k in ["skylight_url", "skylight_email", "skylight_pass"]) and user.id != 1 and user.username != "default":
        raise HTTPException(status_code=403, detail="Only the default system user (User 1) can configure Skylight system integration.")

    # Handle encrypted fields
    crypto_map = {
        "nextcloud_pass": "nextcloud_pass_enc",
        "ha_token": "ha_token_enc",
        "github_token": "github_token_enc",
        "gitlab_token": "gitlab_token_enc",
        "audiobookshelf_pass": "audiobookshelf_pass_enc",
        "audiobookshelf_api_key": "audiobookshelf_api_key_enc",
        "mass_token": "mass_token_enc",
        "git_token": "git_token_enc",
        "huggingface_token": "huggingface_token_enc",
        "skylight_pass": "skylight_pass_enc"
    }

    for plain, enc in crypto_map.items():
        if plain in update_data:
            val = update_data.pop(plain)
            if isinstance(val, str):
                val = val.strip()
            val = val if val else None
            setattr(user, enc, encrypt(val) if val else None)

    for key, value in update_data.items():
        setattr(user, key, _coerce_profile_value(key, value))

    session.add(user)
    session.commit()
    session.refresh(user)
    return user

@app.patch("/api/users/{username}", response_model=UserRead)
def update_user(username: str, body: UserUpdate, session: Session = Depends(get_session), admin: User = Depends(require_api_key)):
    if not admin.is_admin:
        raise HTTPException(status_code=403, detail="Admin only")

    user = session.exec(select(User).where(User.username == username.lower())).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    update_data = body.model_dump(exclude_unset=True)

# Prevent non-default users from changing system skylight integration credentials
    if any(k in update_data for k in ["skylight_url", "skylight_email", "skylight_pass"]) and user.id != 1 and user.username != "default":
        raise HTTPException(status_code=403, detail="Only the default system user (User 1) can configure Skylight system integration.")

    # Handle encrypted fields
    crypto_map = {
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
        "skylight_pass": "skylight_pass_enc"
    }

    for plain, enc in crypto_map.items():
        if plain in update_data:
            val = update_data.pop(plain)
            if isinstance(val, str):
                val = val.strip()
            val = val if val else None
            setattr(user, enc, encrypt(val) if val else None)

    for key, value in update_data.items():
        setattr(user, key, _coerce_profile_value(key, value))

    session.add(user)
    session.commit()
    session.refresh(user)
    return user

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
def delete_user(username: str, session: Session = Depends(get_session), admin: User = Depends(require_api_key)):
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
    return {"status": "SUCCESS"}

@app.get("/api/users", response_model=list[UserRead])
def list_users(session: Session = Depends(get_session), _: bool = Depends(require_admin_or_internal)):
    return session.exec(select(User)).all()


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

@app.post("/api/users", response_model=UserRead)
def create_user(body: UserCreate, session: Session = Depends(get_session), admin: User = Depends(require_api_key)):
    if not admin.is_admin:
        raise HTTPException(status_code=403, detail="Admin only")

    def _coerce(val):
        if isinstance(val, str):
            val = val.strip()
        return val if val else None

    if any(_coerce(k) for k in [body.skylight_url, body.skylight_email, body.skylight_pass]) and admin.id != 1 and admin.username != "default":
        raise HTTPException(status_code=403, detail="Only the default system user (User 1) can configure Skylight system integration.")

    user = User(
        username=body.username.lower(),
        display_name=body.display_name,
        is_admin=body.is_admin,
        is_system_default=body.is_system_default,
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
        audiobookshelf_url=_coerce(body.audiobookshelf_url),
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
    return user

def _require_assignment_admin(user: User = Depends(require_api_key)) -> User:
    """Dependency: only an admin may grant or revoke control of a device.

    Writing an assignment IS granting control of a real device, so this is
    admin-only. Without it any authenticated user could assign an entity to
    themselves and walk straight past both DeviceAssignment and entity
    protection.
    """
    if not user.is_admin:
        raise HTTPException(
            status_code=403,
            detail="Only an admin can assign devices to users.",
        )
    return user


@app.get("/api/devices", response_model=list[DeviceAssignmentRead])
def list_devices(session: Session = Depends(get_session), _: User = Depends(require_api_key)):
    results = session.exec(select(DeviceAssignment)).all()
    return [
        DeviceAssignmentRead(
            id=d.id or 0,
            device_id=d.device_id,
            user_id=d.user_id or 0,
            username=d.user.username if d.user else "",
            revoked=d.revoked
        ) for d in results
    ]

@app.post("/api/devices", response_model=DeviceAssignmentRead)
def add_device(
    body: DeviceAssignmentCreate,
    session: Session = Depends(get_session),
    admin: User = Depends(_require_assignment_admin),
):
    user = session.exec(select(User).where(User.username == body.username.lower())).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    assignment = DeviceAssignment(device_id=body.device_id, user_id=user.id or 0, revoked=body.revoked)
    session.add(assignment)
    session.commit()
    session.refresh(assignment)
    return DeviceAssignmentRead(
        id=assignment.id or 0,
        device_id=assignment.device_id,
        user_id=assignment.user_id or 0,
        username=user.username,
        revoked=assignment.revoked
    )

@app.delete("/api/devices/{device_id}")
def remove_device(
    device_id: str,
    session: Session = Depends(get_session),
    admin: User = Depends(_require_assignment_admin),
):
    assignment = session.exec(select(DeviceAssignment).where(DeviceAssignment.device_id == device_id)).first()
    if not assignment:
        raise HTTPException(status_code=404, detail="Device assignment not found.")
    session.delete(assignment)
    session.commit()
    return {"status": "SUCCESS"}

@app.post("/api/devices/{device_id}/revoke")
def revoke_device(
    device_id: str,
    session: Session = Depends(get_session),
    admin: User = Depends(_require_assignment_admin),
):
    assignment = session.exec(select(DeviceAssignment).where(DeviceAssignment.device_id == device_id)).first()
    if not assignment:
        raise HTTPException(status_code=404, detail="Device assignment not found.")
    if assignment.revoked:
        return {"status": "SUCCESS", "message": "Device already revoked."}
    assignment.revoked = True
    session.commit()
    username = assignment.user.username if assignment.user else "unknown"
    return {"status": "SUCCESS", "message": f"Device '{device_id}' revoked (was assigned to '{username}')."}

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
    if user.is_admin or _is_system_default_user(user):
        return [row.entity_id for row in rows]
    username = (user.username or "").lower()
    return [
        row.entity_id
        for row in rows
        if username in _permitted_usernames(row)
    ]


def _require_protection_admin(user: User = Depends(require_api_key)) -> User:
    """Dependency: entity protection is admin-only, for reads and writes alike.

    Protection is the only thing standing between a normal user and the house,
    so a non-admin must never be able to lock, unlock, or widen a permit list.
    """
    if not user.is_admin:
        raise HTTPException(
            status_code=403,
            detail="Only an admin can manage entity protection.",
        )
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
@app.get("/api/internal/user-device-assignments")
def internal_user_device_assignments(
    username: str,
    session: Session = Depends(get_session),
    _: None = Depends(require_internal),
):
    """Internal: what this user may control.

    `device_ids` is the plain DeviceAssignment grant (unprotected entities).
    `protected_entity_ids` is every locked entity — a caller must not treat it
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
    }


@app.get("/api/internal/validate-api-key")
def internal_validate_api_key(
    api_key: str,
    session: Session = Depends(get_session),
    _: None = Depends(require_internal),
):
    """Internal: is this API key real, and whose is it?

    Deliberately has **no** system-default fallback, which is what makes it
    safe to use as an authentication gate. `POST /api/resolve` falls back to
    the default (admin) user whenever nothing matches, so a caller that
    validated a key through it would accept *any* string — including an
    anonymous caller's — as the default administrator. This endpoint answers
    only "did this exact key authenticate", and 401s otherwise.

    Do not add a fallback here; that is the whole point of the endpoint.
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
    if not user.is_admin:
        query = query.where(DeviceAssignment.user_id == (user.id or 0))
    results = session.exec(query).all()
    return [
        DeviceAssignmentRead(
            id=d.id or 0,
            device_id=d.device_id,
            user_id=d.user_id or 0,
            username=d.user.username if d.user else ""
        ) for d in results
    ]

@app.post("/api/users/devices", response_model=DeviceAssignmentRead)
def add_device_ui(
    body: DeviceAssignmentCreate,
    session: Session = Depends(get_session),
    authorization: str = Header(None),
    x_internal_secret: str = Header(None, alias="X-Internal-Secret")
):
    # Internal calls (Gateway's entity auto-discovery) send only
    # X-Internal-Secret; a user request must be an admin, because writing an
    # assignment grants control of a real device.
    is_internal = x_internal_secret == INTERNAL_SECRET
    if not is_internal:
        if not authorization:
            raise HTTPException(status_code=401, detail="Missing authorization")
        caller = require_api_key(authorization, session)
        if not caller.is_admin:
            raise HTTPException(
                status_code=403,
                detail="Only an admin can assign devices to users.",
            )

    target_user = session.exec(select(User).where(User.username == body.username.lower())).first()
    if not target_user:
        raise HTTPException(status_code=404, detail="User not found")

    # Upsert logic: if device already assigned, reassign it
    existing = session.exec(select(DeviceAssignment).where(DeviceAssignment.device_id == body.device_id)).first()
    if existing:
        existing.user_id = target_user.id or 0
        session.add(existing)
        session.commit()
        session.refresh(existing)
        session.refresh(existing)
        return DeviceAssignmentRead(id=existing.id or 0, device_id=existing.device_id, user_id=existing.user_id or 0, username=target_user.username, revoked=existing.revoked)

    assignment = DeviceAssignment(device_id=body.device_id, user_id=target_user.id or 0, revoked=body.revoked)
    session.add(assignment)
    session.commit()
    session.refresh(assignment)
    return DeviceAssignmentRead(
        id=assignment.id or 0,
        device_id=assignment.device_id,
        user_id=assignment.user_id or 0,
        username=target_user.username,
        revoked=assignment.revoked
    )

# ─── Auth & Discovery ──────────────────────────────────────────────────────────

@app.post("/api/auth/login", response_model=LoginResponse)
def login(req: LoginRequest, session: Session = Depends(get_session)):
    user = session.exec(select(User).where(User.username == req.username)).first()
    if not user or not user.password_hash:
        raise HTTPException(status_code=401, detail="Invalid username or password")

    if not verify_password(req.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid username or password")

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
    session.add(user)
    session.commit()
    return {"status": "SUCCESS", "message": "Password updated"}

@app.post("/api/auth/test-connection")
async def test_connection(req: dict, session: Session = Depends(get_session), admin: User = Depends(require_api_key)):
    """Test a connection before saving."""
    service = req.get("service")
    config = req.get("config", {})
    log.info(f"[test_connection] Testing {service} with config: { {k: '***' if 'token' in k.lower() or 'pass' in k.lower() else v for k, v in config.items()} }")

    try:
        async with get_client_insecure() as client:
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

                resp = await client.get(
                    f"{url.rstrip('/')}/ocs/v1.php/cloud/users?format=json",
                    headers={"OCS-APIRequest": "true"},
                    auth=aiohttp.BasicAuth(user, password)
                )
                if resp.status == 200:
                    return {"status": "SUCCESS", "message": "Connected to Nextcloud"}
                else:
                    return {"status": "ERROR", "message": f"Nextcloud returned {resp.status}"}

            elif service == "GitHub":
                url = config.get("github_url") or "https://api.github.com"
                token = config.get("github_token")
                if not token:
                    return {"status": "ERROR", "message": "Personal Token is required"}

                resp = await client.get(
                    f"{url.rstrip('/')}/user",
                    headers={"Authorization": f"token {token}"}
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
                    headers={"PRIVATE-TOKEN": token}
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

                resp = await client.post(
                    f"{url.rstrip('/')}/api/login",
                    json={"username": username, "password": password}
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

    except Exception as e:
        return {"status": "ERROR", "message": str(e)}

# ─── Credential Seeding ──────────────────────────────────────────────────────

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
}

@app.post("/api/admin/seed-credential")
def seed_credential(body: dict, session: Session = Depends(get_session), admin: User = Depends(require_admin_or_internal)):
    """Seed a single credential for the default user (User 1) without re-seeding the entire DB.

    Accepts a credential field name and its plain text value. The value is encrypted and stored.
    """
    if not admin.is_admin:
        raise HTTPException(status_code=403, detail="Admin only")

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

    # Encrypt and store
    if value:
        setattr(user, enc_field, encrypt(value))
        log.info(f"[seed-credential] Updated {field} for user {user.username}")
    else:
        setattr(user, enc_field, None)
        log.info(f"[seed-credential] Cleared {field} for user {user.username}")

    session.add(user)
    session.commit()
    session.refresh(user)

    return {
        "status": "SUCCESS",
        "message": f"Seeded {field} for user {user.username}",
        "field": field,
        "has_value": bool(value)
    }

# ─── API Key Management ────────────────────────────────────────────────────────

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
def generate_key(body: dict, session: Session = Depends(get_session), user: User = Depends(require_api_key)):
    """Generate a new API key for the current user."""
    import secrets
    new_key_value = "sk-" + secrets.token_hex(24)
    new_key = APIKey(label=body.get("label", "New Key"), user_id=user.id or 0)
    _store_generated_api_key(new_key, new_key_value)
    session.add(new_key)
    session.commit()
    session.refresh(new_key)
    return {"id": new_key.id, "label": new_key.label, "key": new_key_value} # Only show full key once!

@app.delete("/api/users/me/keys/{key_id}")
def revoke_key(key_id: int, session: Session = Depends(get_session), user: User = Depends(require_api_key)):
    """Revoke an API key."""
    key = session.exec(select(APIKey).where(APIKey.id == key_id, APIKey.user_id == user.id)).first()
    if not key:
        raise HTTPException(status_code=404, detail="Key not found")
    session.delete(key)
    session.commit()
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

    # 3. Scan Audiobookshelf (library users) — basic auth, or the API key header
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
        warnings.append("Mailcow: not configured — set mailcow_url to include mailboxes in onboarding")

    # 5. Merge users — one entry per username, combining every source.
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
            # Never alias onto something that is itself a discovered login —
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

# ─── Onboarding: one-time password → per-user token ────────────────────────────

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

    No password from the user is needed at all here — this is the one service
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

    return {
        "success": True,
        "username": target.username,
        "service": service,
        "message": f"{service} token stored for {target.username}; the password was not saved",
    }


# ─── Admin ─────────────────────────────────────────────────────────────────────

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
    return settings

# Keys that must never be written with a blank/empty value.
# The UI dropdowns are always populated with real values, so a blank write
# here means a UI bug or a bad direct API call — both must fail loudly.
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

# ─── DNS Management ─────────────────────────────────────────────────────────────

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
    known_keys = [
        'energy_insights', 'ambient_timer', 'quick_notes', 'active_media',
        'chores_progress', 'upcoming_events', 'quick_assistant', 'device_control',
        'workspaces', 'health_activity', 'climate', 'presence'
    ]
    default_sizes = {
        'energy_insights': 'medium',
        'ambient_timer': 'small',
        'quick_notes': 'medium',
        'active_media': 'wide',
        'chores_progress': 'tall',
        'upcoming_events': 'wide',
        'quick_assistant': 'medium',
        'device_control': 'tall',
        'workspaces': 'medium',
        'health_activity': 'medium',
        'climate': 'medium',
        'presence': 'medium',
    }
    result = []
    for key in known_keys:
        if key in settings_map:
            result.append(settings_map[key])
        else:
            result.append(UserWidgetRead(
                widget_key=key,
                visibility='visible' if key != 'quick_assistant' else 'hidden',
                order_index=known_keys.index(key),
                size=default_sizes[key],
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
                order_index=6,
                size='medium',
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

    update_data = body.model_dump(exclude_unset=True)

    if not update_data:
        return {"status": "SUCCESS"}

    # Serialize JSON fields
    if 'pinned_devices' in update_data:
        update_data['pinned_devices'] = json.dumps(update_data['pinned_devices'])
    if 'config' in update_data:
        update_data['config'] = json.dumps(update_data['config'])

    # Creating a quick_assistant row without an explicit visibility must not
    # reveal the widget (model default is 'visible').
    if widget_key == 'quick_assistant' and 'visibility' not in update_data:
        update_data['visibility'] = 'hidden'

    if existing:
        for key, value in update_data.items():
            setattr(existing, key, value)
        existing.updated_at = int(datetime.now().timestamp() * 1000)
        session.add(existing)
    else:
        new_widget = UserWidget(
            username=user.username,
            widget_key=widget_key,
            **update_data,
        )
        session.add(new_widget)

    session.commit()
    return {"status": "SUCCESS"}

# ─── Raven Missions (Autonomous Ops & User Tasks) ───────────────────────────────

# ─── Calendar Integration Settings (per-user) ───────────────────────────────

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
    calendar-owner person records: {id, name, color, accounts}).
    Unknown keys are ignored; missing keys are preserved.
    """
    row = session.exec(
        select(UserCalendarSetting).where(UserCalendarSetting.username == user.username)
    ).first()
    if row is None:
        row = UserCalendarSetting(username=user.username, data={})
        session.add(row)

    data = json.loads(row.data) if (row and row.data) else {}
    allowed = {"default", "disabled", "priority", "ical_urls", "people"}
    for key, value in (body or {}).items():
        if key in allowed:
            data[key] = value
    row.data = json.dumps(data)
    session.add(row)
    session.commit()
    return {"status": "SUCCESS", "settings": data}


# ─── Per-user website/widget theme (same pack schema as the web client) ──────

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


# ─── Per-user opt-in activity sharing (private by default) ───────────────────

ACTIVITY_SHARE_SCOPES = {"totals", "workouts", "achievements"}
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
    """Current user's activity-sharing preference (defaults to off)."""
    row = session.exec(
        select(UserActivitySharing).where(UserActivitySharing.username == user.username)
    ).first()
    return {"status": "SUCCESS", **_activity_sharing_data(row)}


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
    for key, value in (body or {}).items():
        if key == "enabled":
            if not isinstance(value, bool):
                raise HTTPException(status_code=422, detail="enabled must be a boolean")
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
    row.data = json.dumps(data)
    session.add(row)
    session.commit()
    return {"status": "SUCCESS", **data}


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
def get_missions(limit: int = 200, workspace_id: str | None = None, session: Session = Depends(get_session)):
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
def get_mission(mission_id_or_slug: str, session: Session = Depends(get_session)):
    return _resolve_mission(mission_id_or_slug, session)

@app.post("/api/raven/missions", response_model=RavenMissionRead)
def create_mission(
    body: RavenMissionCreate,
    session: Session = Depends(get_session)
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
    session: Session = Depends(get_session)
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

@app.delete("/api/raven/missions/{mission_id_or_slug}")
def delete_mission(mission_id_or_slug: str, session: Session = Depends(get_session)):
    mission = _resolve_mission(mission_id_or_slug, session)
    if not mission:
        raise HTTPException(status_code=404, detail="Mission not found")
    session.delete(mission)
    session.commit()
    return {"status": "SUCCESS"}

@app.delete("/api/raven/missions/{mission_id}")
def delete_mission_by_id(mission_id: int, session: Session = Depends(get_session)):
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
        return {
            "status": "SUCCESS",
            "message": f"Imported {len(imported)} users",
            "imported_users": imported,
        }


# ─── Device & Light Grouping (Section 3.14) ───────────────────────────────────

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


# ─── Device Telemetry Monitoring (Section 3.15) ───────────────────────────────

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

    import time as _time
    import statistics

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
        from services.config import GATEWAY_INTERNAL_URL, INTERNAL_SECRET as _INTERNAL_SECRET
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
        async with aiohttp.ClientSession() as client:
            async with client.post(
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
                    "type": data.get("analysis_available") and "analysis" or "none",
                    "message": data.get("analysis") or "",
                    "severity": "info",
                    "timestamp": data.get("generated_at"),
                }
            ],
        }


# ─── Household Intercom System (Section 3.16) ─────────────────────────────────

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


# ─── Presence & Location ───────────────────────────────────────────────────────

class LocationUpdate(BaseModel):
    """GPS location update from mobile app."""
    latitude: float
    longitude: float
    accuracy: float | None = None
    speed: float | None = None
    bearing: float | None = None
    battery: int | float | None = None
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
                if location.speed is not None:
                    payload["speed"] = location.speed
                if location.bearing is not None:
                    payload["course"] = location.bearing

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
        async with get_client_insecure() as client:
            async with client.post(
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
    x_internal_secret: str | None = Header(None),
):
    """Store user GPS location from mobile app, forward to Home Assistant, and record in Geo service.

    Header is optional in the signature so a missing secret returns 403 (not FastAPI's 422),
    matching the rest of the identity internal endpoints.
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
    with Session(engine) as session:
        key = f"user_location:{_resolve_location_user_key(session, user_id)}"
        existing = session.exec(select(GlobalSetting).where(GlobalSetting.key == key)).first()
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
    log.info(f"[location] Updated location for {user_id}: ({location.latitude}, {location.longitude})")

    try:
        await _forward_location_to_ha(user_id, location)
        await _forward_location_to_geo(user_id, location)
    except Exception as exc:
        log.warning(f"[location] Forward failed for {user_id}: {exc}")

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
    """Register or refresh the caller's own phone, on login.

    Idempotent on ``device_key``: logging in repeatedly updates the existing
    row instead of creating a new device each time, which is why the key is
    unique in the schema rather than cleaned up here.

    ``kind`` is deliberately not accepted from the client. A phone is a phone;
    letting the app claim ``kind="light"`` would move itself out of the
    advanced panel. Assistants and lights go through the admin endpoint.
    """
    key = body.device_key.strip()
    now = datetime.now().isoformat()
    device = session.exec(select(Device).where(Device.device_key == key)).first()
    if device is None:
        device = Device(device_key=key, kind="phone", registered_by="self", first_seen_at=now)
        session.add(device)
    elif device.kind != "phone" or device.registered_by == "admin":
        # A device already registered as an assistant/light must not be
        # silently reclassified by whoever holds its key.
        raise HTTPException(status_code=409, detail="This device key is already registered as another kind")

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
def list_devices(
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
    return [_device_to_read(d) for d in session.exec(stmt).all()]


@app.post("/api/user-panel/devices", response_model=DeviceRead)
def create_device(
    body: DeviceAdminCreate,
    request: Request,
    session: Session = Depends(get_session),
    admin: User = Depends(require_api_key),
):
    """Register an assistant or light. Admin only.

    These have no account to log in with, so an admin declares them and assigns
    the owner. A phone must not be created here: it registers itself.
    """
    if not admin.is_admin:
        raise HTTPException(status_code=403, detail="Admin only")
    kind = (body.kind or "").strip().lower()
    if kind not in DEVICE_KINDS:
        raise HTTPException(status_code=422, detail=f"kind must be one of {', '.join(DEVICE_KINDS)}")
    if kind == "phone":
        raise HTTPException(status_code=422, detail="Phones register themselves; use /api/user-panel/devices/register")
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


@app.patch("/api/user-panel/devices/{device_key}", response_model=DeviceRead)
def update_device(
    device_key: str,
    body: DeviceAssign,
    request: Request,
    session: Session = Depends(get_session),
    admin: User = Depends(require_api_key),
):
    """Assign or reassign a device to a user. Admin only.

    Assignment stays admin-side on purpose: a device being able to claim an
    owner would let anyone reassign the family's assistant by holding its key.
    """
    if not admin.is_admin:
        raise HTTPException(status_code=403, detail="Admin only")
    device = session.exec(select(Device).where(Device.device_key == device_key)).first()
    if device is None:
        raise HTTPException(status_code=404, detail="No such device")
    if body.owner_username:
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

    device.last_seen_at = now
    device.last_ip_address = _client_ip(request)
    session.add(device)
    session.commit()
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
