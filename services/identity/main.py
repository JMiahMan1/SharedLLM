# services/identity/main.py
"""
Microservice 1: Identity & Profile Service
Manages user profiles, device assignments, and secure credential resolution.
"""
import json
import logging
import os
import re
import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from datetime import datetime as dt

import aiohttp
from fastapi import Depends, FastAPI, File, Header, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import inspect, text
from sqlmodel import Session, SQLModel, create_engine, select

from services.common.http import get_client, get_client_insecure
from services.config import IDENTITY_DATABASE_URL, INTERNAL_SECRET
from services.identity.crypto import decrypt, digest_secret, encrypt
from services.identity.models import (
    DEFAULT_GLOBAL_SETTINGS,
    APIKey,
    DeviceAssignment,
    DnsRecord,
    GlobalSetting,
    RavenMission,
    User,
    UserCalendarSetting,
    UserWidget,
)
from services.identity.schemas import (
    DeviceAssignmentCreate,
    DeviceAssignmentRead,
    DiscoverResponse,
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
    if x_internal_secret != INTERNAL_SECRET:
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
    inspector = inspect(engine)
    if "user" in inspector.get_table_names():
        columns = {column["name"] for column in inspector.get_columns("user")}
        with engine.connect() as conn:
            if "is_admin" not in columns:
                conn.execute(text("ALTER TABLE user ADD COLUMN is_admin BOOLEAN NOT NULL DEFAULT 0"))
            if "password_hash" not in columns:
                conn.execute(text("ALTER TABLE user ADD COLUMN password_hash VARCHAR"))
            if "api_key" not in columns:
                conn.execute(text("ALTER TABLE user ADD COLUMN api_key VARCHAR"))
            if "api_key_enc" not in columns:
                conn.execute(text("ALTER TABLE user ADD COLUMN api_key_enc VARCHAR"))
            if "api_key_hash" not in columns:
                conn.execute(text("ALTER TABLE user ADD COLUMN api_key_hash VARCHAR"))
            if "github_url" not in columns:
                conn.execute(text("ALTER TABLE user ADD COLUMN github_url VARCHAR"))
            if "github_user" not in columns:
                conn.execute(text("ALTER TABLE user ADD COLUMN github_user VARCHAR"))
            if "github_token_enc" not in columns:
                conn.execute(text("ALTER TABLE user ADD COLUMN github_token_enc VARCHAR"))
            if "huggingface_token_enc" not in columns:
                conn.execute(text("ALTER TABLE user ADD COLUMN huggingface_token_enc VARCHAR"))
            if "gitlab_url" not in columns:
                conn.execute(text("ALTER TABLE user ADD COLUMN gitlab_url VARCHAR"))
            if "gitlab_user" not in columns:
                conn.execute(text("ALTER TABLE user ADD COLUMN gitlab_user VARCHAR"))
            if "gitlab_token_enc" not in columns:
                conn.execute(text("ALTER TABLE user ADD COLUMN gitlab_token_enc VARCHAR"))
            if "audiobookshelf_url" not in columns:
                conn.execute(text("ALTER TABLE user ADD COLUMN audiobookshelf_url VARCHAR"))
            if "audiobookshelf_user" not in columns:
                conn.execute(text("ALTER TABLE user ADD COLUMN audiobookshelf_user VARCHAR"))
            if "audiobookshelf_pass_enc" not in columns:
                conn.execute(text("ALTER TABLE user ADD COLUMN audiobookshelf_pass_enc VARCHAR"))
            if "audiobookshelf_api_key_enc" not in columns:
                conn.execute(text("ALTER TABLE user ADD COLUMN audiobookshelf_api_key_enc VARCHAR"))
            if "mass_url" not in columns:
                conn.execute(text("ALTER TABLE user ADD COLUMN mass_url VARCHAR"))
            if "mass_token_enc" not in columns:
                conn.execute(text("ALTER TABLE user ADD COLUMN mass_token_enc VARCHAR"))
            if "skylight_enabled" not in columns:
                conn.execute(text("ALTER TABLE user ADD COLUMN skylight_enabled BOOLEAN NOT NULL DEFAULT 1"))
            if "skylight_url" not in columns:
                conn.execute(text("ALTER TABLE user ADD COLUMN skylight_url VARCHAR"))
            if "skylight_email" not in columns:
                conn.execute(text("ALTER TABLE user ADD COLUMN skylight_email VARCHAR"))
            if "skylight_pass_enc" not in columns:
                conn.execute(text("ALTER TABLE user ADD COLUMN skylight_pass_enc VARCHAR"))
            if "git_url" not in columns:
                conn.execute(text("ALTER TABLE user ADD COLUMN git_url VARCHAR"))
            if "git_user" not in columns:
                conn.execute(text("ALTER TABLE user ADD COLUMN git_user VARCHAR"))
            if "git_token_enc" not in columns:
                conn.execute(text("ALTER TABLE user ADD COLUMN git_token_enc VARCHAR"))
            if "voice_fingerprint" not in columns:
                conn.execute(text("ALTER TABLE user ADD COLUMN voice_fingerprint VARCHAR"))
            if "preferred_tts_voice" not in columns:
                conn.execute(text("ALTER TABLE user ADD COLUMN preferred_tts_voice VARCHAR DEFAULT 'af_heart'"))
            conn.commit()
    if "apikey" in inspector.get_table_names():
        key_columns = {column["name"] for column in inspector.get_columns("apikey")}
        with engine.connect() as conn:
            if "key_hash" not in key_columns:
                conn.execute(text("ALTER TABLE apikey ADD COLUMN key_hash VARCHAR"))
            if "key_prefix" not in key_columns:
                conn.execute(text("ALTER TABLE apikey ADD COLUMN key_prefix VARCHAR"))
            conn.commit()

    if "ravenmission" in inspector.get_table_names():
        raven_columns = {column["name"] for column in inspector.get_columns("ravenmission")}
        with engine.connect() as conn:
            if "slug" not in raven_columns:
                conn.execute(text("ALTER TABLE ravenmission ADD COLUMN slug VARCHAR"))
            if "queued_at" not in raven_columns:
                conn.execute(text("ALTER TABLE ravenmission ADD COLUMN queued_at VARCHAR"))
            if "started_at" not in raven_columns:
                conn.execute(text("ALTER TABLE ravenmission ADD COLUMN started_at VARCHAR"))
            if "completed_at" not in raven_columns:
                conn.execute(text("ALTER TABLE ravenmission ADD COLUMN completed_at VARCHAR"))
            if "duration" not in raven_columns:
                conn.execute(text("ALTER TABLE ravenmission ADD COLUMN duration INTEGER"))
            if "workspace_id" not in raven_columns:
                conn.execute(text("ALTER TABLE ravenmission ADD COLUMN workspace_id VARCHAR"))
            if "last_llm_reply" not in raven_columns:
                conn.execute(text("ALTER TABLE ravenmission ADD COLUMN last_llm_reply TEXT"))
            conn.commit()

    if "deviceassignment" in inspector.get_table_names():
        da_columns = {column["name"] for column in inspector.get_columns("deviceassignment")}
        with engine.connect() as conn:
            if "revoked" not in da_columns:
                conn.execute(text("ALTER TABLE deviceassignment ADD COLUMN revoked BOOLEAN NOT NULL DEFAULT 0"))
            conn.commit()

    if "user_widgets" not in inspector.get_table_names():
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

def require_internal(authorization: str = Header(None), x_internal_secret: str = Header(None, alias="X-Internal-Secret")):
    if x_internal_secret == INTERNAL_SECRET:
        return
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing internal token")
    token = authorization.split(" ")[1]
    if token != INTERNAL_SECRET:
        raise HTTPException(status_code=403, detail="Invalid internal token")

def require_admin_or_internal(
    authorization: str = Header(None),
    x_internal_secret: str = Header(None, alias="X-Internal-Secret"),
    session: Session = Depends(get_session)
):
    # Trust internal services
    if x_internal_secret == INTERNAL_SECRET:
        return True

    # Trust bearer tokens matching internal secret
    if authorization and authorization.startswith("Bearer "):
        token = authorization.split(" ")[1]
        if token == INTERNAL_SECRET:
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

    # Fetch system user for shared skylight credentials and MA fallback
    sys_user = session.exec(select(User).where(User.id == 1)).first()
    if not sys_user:
        sys_user = session.exec(select(User).where(User.username == "default")).first()

    # Fallback MA credentials to admin if user doesn't have them
    use_admin_mass_url = sys_user.mass_url if (not user.mass_url and sys_user and sys_user.mass_url) else user.mass_url
    use_admin_mass_token = (
        decrypt(sys_user.mass_token_enc) if (sys_user and sys_user.mass_token_enc) else None
    ) if (not user.mass_token_enc) else (decrypt(user.mass_token_enc) if user.mass_token_enc else None)

    log.info(f"[resolve] Returning credentials for user={user.username}, mass_token={'set' if (user.mass_token_enc or use_admin_mass_token) else 'NOT SET'}, using_admin_mass={'YES' if use_admin_mass_url != user.mass_url else 'NO'}")

    return ResolvedCredentials(
        user=user.username,
        is_admin=user.is_admin,
        api_key=decrypt(user.api_key_enc) if user.api_key_enc else req.api_key,
        nextcloud_url=user.nextcloud_url,
        nextcloud_user=user.nextcloud_user,
        nextcloud_pass=decrypt(user.nextcloud_pass_enc) if user.nextcloud_pass_enc else None,
        ha_url=user.ha_url,
        ha_token=decrypt(user.ha_token_enc) if user.ha_token_enc else None,
        github_url=user.github_url,
        github_user=user.github_user,
        github_token=decrypt(user.github_token_enc) if user.github_token_enc else None,
        gitlab_url=user.gitlab_url,
        gitlab_user=user.gitlab_user,
        gitlab_token=decrypt(user.gitlab_token_enc) if user.gitlab_token_enc else None,
        audiobookshelf_url=user.audiobookshelf_url,
        audiobookshelf_user=user.audiobookshelf_user,
        audiobookshelf_pass=decrypt(user.audiobookshelf_pass_enc) if user.audiobookshelf_pass_enc else None,
        audiobookshelf_api_key=decrypt(user.audiobookshelf_api_key_enc) if user.audiobookshelf_api_key_enc else None,
        mass_url=use_admin_mass_url,
        mass_token=use_admin_mass_token,
        git_url=user.git_url,
        git_user=user.git_user,
        git_token=decrypt(user.git_token_enc) if user.git_token_enc else None,
        huggingface_token=decrypt(user.huggingface_token_enc) if user.huggingface_token_enc else None,
        skylight_url=sys_user.skylight_url if sys_user else None,
        skylight_email=sys_user.skylight_email if sys_user else user.username,
        skylight_pass=decrypt(sys_user.skylight_pass_enc) if (sys_user and sys_user.skylight_pass_enc) else None,
        skylight_enabled=user.skylight_enabled,
        preferred_tts_voice=user.preferred_tts_voice or "af_heart",
        calendar_settings=_load_calendar_settings(session, user.username),
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
        if isinstance(value, str):
            value = value.strip()
        value = value if value else None
        setattr(user, key, value)

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
        if isinstance(value, str):
            value = value.strip()
        value = value if value else None
        setattr(user, key, value)

    session.add(user)
    session.commit()
    session.refresh(user)
    return user

@app.delete("/api/users/{username}")
def delete_user(username: str, session: Session = Depends(get_session), admin: User = Depends(require_api_key)):
    if not admin.is_admin:
        raise HTTPException(status_code=403, detail="Admin only")

    user = session.exec(select(User).where(User.username == username.lower())).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if user.is_system_default:
        raise HTTPException(status_code=400, detail="Cannot delete system default user")

    session.delete(user)
    session.commit()
    return {"status": "SUCCESS"}

@app.get("/api/users", response_model=list[UserRead])
def list_users(session: Session = Depends(get_session), _: bool = Depends(require_admin_or_internal)):
    return session.exec(select(User)).all()

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
def add_device(body: DeviceAssignmentCreate, session: Session = Depends(get_session), _: User = Depends(require_api_key)):
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
def remove_device(device_id: str, session: Session = Depends(get_session), _: User = Depends(require_api_key)):
    assignment = session.exec(select(DeviceAssignment).where(DeviceAssignment.device_id == device_id)).first()
    if not assignment:
        raise HTTPException(status_code=404, detail="Device assignment not found.")
    session.delete(assignment)
    session.commit()
    return {"status": "SUCCESS"}

@app.post("/api/devices/{device_id}/revoke")
def revoke_device(device_id: str, session: Session = Depends(get_session), _: User = Depends(require_api_key)):
    assignment = session.exec(select(DeviceAssignment).where(DeviceAssignment.device_id == device_id)).first()
    if not assignment:
        raise HTTPException(status_code=404, detail="Device assignment not found.")
    if assignment.revoked:
        return {"status": "SUCCESS", "message": "Device already revoked."}
    assignment.revoked = True
    session.commit()
    username = assignment.user.username if assignment.user else "unknown"
    return {"status": "SUCCESS", "message": f"Device '{device_id}' revoked (was assigned to '{username}')."}

# --- Device Matrix (UI Contract) ---
@app.get("/api/users/devices", response_model=list[DeviceAssignmentRead])
def list_devices_ui(session: Session = Depends(get_session), _: User = Depends(require_api_key)):
    results = session.exec(select(DeviceAssignment)).all()
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
    # Determine if internal or user-authed
    is_internal = x_internal_secret == INTERNAL_SECRET
    user = None
    if not is_internal:
        # User-facing API calls require a valid API key
        user = require_api_key(authorization, session)
        if not user:
            raise HTTPException(status_code=401, detail="Unauthorized")

    # Internal calls (like auto-discovery from Gateway) bypass user auth
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
def change_password(new_password: str, session: Session = Depends(get_session), user: User = Depends(require_api_key)):
    user.password_hash = hash_password(new_password)
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
                    auth=aiohttp.BasicAuth(user, password)
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
                username = config.get("audiobookshelf_user") or config.get("abs_user")
                password = config.get("audiobookshelf_pass") or config.get("abs_pass")
                if not url:
                    return {"status": "ERROR", "message": "URL is required"}
                if not username or not password:
                    return {"status": "ERROR", "message": "Username and Password are required"}

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
    """Scan Home Assistant and Nextcloud for users to import.
    Merges users found in both sources into a single entry with combined data."""
    if not admin.is_admin:
        raise HTTPException(status_code=403, detail="Admin only")

    warnings: list[str] = []
    errors: list[str] = []

    # Resolve credentials to use (prefer admin's, fallback to default)
    default_user = session.exec(select(User).where(User.username == "default")).first()

    ha_url = admin.ha_url or (default_user.ha_url if default_user else None)
    ha_token_enc = admin.ha_token_enc or (default_user.ha_token_enc if default_user else None)

    nc_url = admin.nextcloud_url or (default_user.nextcloud_url if default_user else None)
    nc_user = admin.nextcloud_user or (default_user.nextcloud_user if default_user else None)
    nc_pass_enc = admin.nextcloud_pass_enc or (default_user.nextcloud_pass_enc if default_user else None)

    log.info(f"[discovery] Starting scan. HA_URL: {ha_url}, NC_URL: {nc_url}")

    # Collect users from each source into dicts keyed by username
    ha_users: dict[str, dict] = {}
    nc_users: dict[str, dict] = {}

    # 1. Scan Home Assistant (Person entities)
    if ha_url and ha_token_enc:
        ha_token = decrypt(ha_token_enc)
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

    # 2. Scan Nextcloud (Provisioning API)
    if nc_url and nc_user and nc_pass_enc:
        nc_pass = decrypt(nc_pass_enc)
        assert nc_pass is not None
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

    # 3. Merge users — combine HA and NC data when usernames match
    all_usernames = set(ha_users.keys()) | set(nc_users.keys())
    discovered = []
    for username in sorted(all_usernames):
        existing = session.exec(select(User).where(User.username == username)).first()
        if existing:
            continue

        in_ha = username in ha_users
        in_nc = username in nc_users
        ha_data = ha_users.get(username, {})
        nc_data = nc_users.get(username, {})

        # Determine source label
        if in_ha and in_nc:
            source = "Home Assistant + Nextcloud"
        elif in_ha:
            source = "Home Assistant"
        else:
            source = "Nextcloud"

        # Prefer NC display_name (usually more accurate), fall back to HA
        display_name = nc_data.get("display_name") or ha_data.get("display_name") or username.capitalize()
        email = nc_data.get("email")

        discovered.append(DiscoverUser(
            username=username,
            source=source,
            display_name=display_name,
            email=email,
            ha_person_id=ha_data.get("entity_id"),
            nc_username=nc_data.get("nc_username"),
        ))

    log.info(f"[discovery] Discovery complete. Found {len(discovered)} users.")
    return DiscoverResponse(users=discovered, warnings=warnings, errors=errors)

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
        'chores_progress', 'upcoming_events', 'quick_assistant', 'device_control'
    ]
    result = []
    for key in known_keys:
        if key in settings_map:
            result.append(settings_map[key])
        else:
            result.append(UserWidgetRead(
                widget_key=key,
                visibility='visible' if key != 'quick_assistant' else 'hidden',
                order_index=known_keys.index(key),
                size='medium',
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
    stmt = stmt.options(defer(cast(Any, RavenMission.output_log)), defer(cast(Any, RavenMission.result)))
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
            raise HTTPException(status_code=409, detail=f"'{entity_id}' already enrolled")
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
def get_telemetry_data(entity_id: str, x_internal_secret: str = Header(...)):
    _require_internal_secret(x_internal_secret)
    key = f"telemetry_data:{entity_id}"
    with Session(engine) as session:
        snapshot = session.exec(select(GlobalSetting).where(GlobalSetting.key == key)).first()
        if not snapshot:
            return {"entity_id": entity_id, "data_points": []}
        data_points = json.loads(snapshot.value)
        return {"entity_id": entity_id, "data_points": data_points}


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
def trigger_telemetry_analysis(analysis_data: dict, x_internal_secret: str = Header(...)):
    _require_internal_secret(x_internal_secret)
    entity_id = analysis_data.get("entity_id")
    hours = analysis_data.get("hours", 168)
    return {
        "status": "SUCCESS",
        "message": f"Telemetry analysis queued for '{entity_id or 'all enrolled'}' over {hours}h",
        "entity_id": entity_id,
        "hours": hours,
    }


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
    timestamp: float | None = None


@app.post("/api/users/{user_id}/location")
def update_user_location(
    user_id: str,
    location: LocationUpdate,
    x_internal_secret: str = Header(...),
):
    """Store user GPS location from mobile app."""
    _require_internal_secret(x_internal_secret)
    import time
    location_data = {
        "latitude": location.latitude,
        "longitude": location.longitude,
        "accuracy": location.accuracy,
        "speed": location.speed,
        "bearing": location.bearing,
        "timestamp": location.timestamp or time.time(),
        "updated_at": time.time(),
    }
    with Session(engine) as session:
        key = f"user_location:{user_id}"
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
    return {"status": "SUCCESS", "message": "Location updated"}


@app.get("/api/users/{user_id}/location")
def get_user_location(
    user_id: str,
    x_internal_secret: str = Header(...),
):
    """Get stored GPS location for a user."""
    _require_internal_secret(x_internal_secret)
    with Session(engine) as session:
        key = f"user_location:{user_id}"
        location = session.exec(select(GlobalSetting).where(GlobalSetting.key == key)).first()
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
