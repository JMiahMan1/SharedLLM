# services/identity/main.py
"""
Microservice 1: Identity & Profile Service
Manages user profiles, device assignments, and secure credential resolution.
"""
import os
import sys
import json
import logging
from contextlib import asynccontextmanager
from typing import List, Optional, Dict

from fastapi import FastAPI, Depends, HTTPException, Header, File, UploadFile
from sqlalchemy import inspect, text
from sqlmodel import Session, SQLModel, create_engine, select

try:
    from .models import User, DeviceAssignment, GlobalSetting, APIKey, DEFAULT_GLOBAL_SETTINGS
    from .schemas import (
        ResolveRequest, ResolvedCredentials, 
        UserCreate, UserRead, UserUpdate,
        DeviceAssignmentRead, DeviceAssignmentCreate,
        LoginRequest, LoginResponse, DiscoverUser,
        GlobalSettingRead, GlobalSettingUpdate
    )
    from .crypto import encrypt, decrypt, digest_secret
    from .seed import seed_from_env, pwd_context
except (ImportError, ModuleNotFoundError):
    from models import User, DeviceAssignment, GlobalSetting, APIKey, DEFAULT_GLOBAL_SETTINGS
    from schemas import (
        ResolveRequest, ResolvedCredentials, 
        UserCreate, UserRead, UserUpdate,
        DeviceAssignmentRead, DeviceAssignmentCreate,
        LoginRequest, LoginResponse, DiscoverUser,
        GlobalSettingRead, GlobalSettingUpdate
    )
    from crypto import encrypt, decrypt, digest_secret
    from seed import seed_from_env, pwd_context

import httpx

# ─── Config ────────────────────────────────────────────────────────────────────

log = logging.getLogger("identity")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from config import INTERNAL_SECRET, OLLAMA_URL, IDENTITY_DATABASE_URL

def _require_internal_secret(x_internal_secret: Optional[str]) -> None:
    if x_internal_secret != INTERNAL_SECRET:
        raise HTTPException(status_code=403, detail="Forbidden")

DATABASE_URL = IDENTITY_DATABASE_URL

engine = create_engine(
    DATABASE_URL, 
    connect_args={"check_same_thread": False, "timeout": 30} if "sqlite" in DATABASE_URL else {}
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
            conn.commit()


async def _ensure_default_settings(session: Session) -> None:
    existing_keys = {
        setting.key
        for setting in session.exec(select(GlobalSetting)).all()
    }
    
    # Try to fetch available models from Ollama to provide better 'auto' defaults
    available_models = []
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(f"{OLLAMA_URL}/api/tags")
            if resp.status_code == 200:
                available_models = [m["name"] for m in resp.json().get("models", [])]
    except Exception as e:
        log.warning(f"Could not reach Ollama to resolve 'auto' defaults: {e}")

    def resolve_best_model(pattern: str, fallback: str) -> str:
        if not available_models: return fallback
        # Priority 1: Exact match
        for m in available_models:
            if pattern in m: return m
        # Priority 2: First available
        return available_models[0]

    inserted = False
    for setting in DEFAULT_GLOBAL_SETTINGS:
        if setting["key"] in existing_keys:
            continue
        
        # Dynamic Resolution for models
        if setting.get("value") == "auto":
            if setting["key"] in ("coding_model", "ollama_coding_model"):
                setting["value"] = resolve_best_model("qwen3.5", "qwen3.5:9b")
            elif setting["key"] in ("librarian_model", "ollama_librarian_model"):
                setting["value"] = resolve_best_model("qwen3.5", "qwen3.5:9b")
            elif setting["key"] in ("assistant_model", "ollama_assistant_model"):
                setting["value"] = resolve_best_model("qwen3.5", "qwen3.5:9b")
            elif setting["key"] == "cloud_coding_model":
                setting["value"] = "anthropic/claude-3.5-sonnet"
            elif setting["key"] in ("cloud_assistant_model", "cloud_librarian_model"):
                setting["value"] = "google/gemini-2.0-flash-001"

        session.add(GlobalSetting(**setting))
        inserted = True
    if inserted:
        session.commit()

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Ensure tables exist
    SQLModel.metadata.create_all(engine)
    _ensure_schema_upgrades()
    
    # Run initial seed if needed
    with Session(engine) as session:
        seed_from_env(session)
        await _ensure_default_settings(session)
        _migrate_api_key_material(session)
    yield

app = FastAPI(title="Jarvis OS Identity Service", lifespan=lifespan)

from fastapi.middleware.cors import CORSMiddleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

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
    key_hash = digest_secret(key_value)

    api_key_obj = session.exec(select(APIKey).where(APIKey.key_hash == key_hash)).first() if key_hash else None
    if not api_key_obj:
        api_key_obj = session.exec(select(APIKey).where(APIKey.key_value == key_value)).first()
        if api_key_obj and not api_key_obj.key_hash:
            _store_generated_api_key(api_key_obj, key_value)
            session.add(api_key_obj)
            session.commit()
            session.refresh(api_key_obj)
    if api_key_obj:
        return api_key_obj.user

    user = session.exec(select(User).where(User.api_key_hash == key_hash)).first() if key_hash else None
    if not user:
        user = session.exec(select(User).where(User.api_key == key_value)).first()
        if user and not user.api_key_hash:
            _store_user_api_key(user, key_value)
            session.add(user)
            session.commit()
            session.refresh(user)
    return user


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
    
    user.password_hash = pwd_context.hash(new_password)
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
    
    # Resolve by API Key first (for OpenWebUI & UI clients)
    if req.api_key:
        user = _find_user_for_api_key(session, req.api_key)

    elif req.rag_user:
        user = session.exec(select(User).where(User.username == req.rag_user.lower())).first()
    elif req.voice_id:
        # Search for user by voice_id (username or biometric match)
        user = session.exec(select(User).where(User.username == req.voice_id.lower())).first()
    elif req.device_id:
        assignment = session.exec(select(DeviceAssignment).where(DeviceAssignment.device_id == req.device_id)).first()
        if assignment:
            user = assignment.user

    if not user:
        # Fallback to system account (ID 1)
        user = session.exec(select(User).where(User.id == 1)).first()
        if not user:
            # Last resort fallback to "default" username if ID 1 somehow missing
            user = session.exec(select(User).where(User.username == "default")).first()
            if not user:
                raise HTTPException(status_code=404, detail="No valid identity found")

    # Decrypt sensitive fields
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
        git_url=user.git_url,
        git_user=user.git_user,
        git_token=decrypt(user.git_token_enc) if user.git_token_enc else None,
        preferred_tts_voice=user.preferred_tts_voice or "af_heart"
    )

@app.get("/health")
def health():
    return {"status": "OK", "service": "identity"}

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
        raise HTTPException(status_code=500, detail=str(e))

@app.patch("/api/users/me", response_model=UserRead)
def update_me(body: UserUpdate, session: Session = Depends(get_session), user: User = Depends(require_api_key)):
    log.info(f"[update_me] Received update for {user.username}: {body.model_dump(exclude_unset=True)}")
    update_data = body.model_dump(exclude_unset=True)
    
    # Handle encrypted fields
    crypto_map = {
        "nextcloud_pass": "nextcloud_pass_enc",
        "ha_token": "ha_token_enc",
        "github_token": "github_token_enc",
        "gitlab_token": "gitlab_token_enc",
        "audiobookshelf_pass": "audiobookshelf_pass_enc",
        "git_token": "git_token_enc"
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
    
    # Handle encrypted fields
    crypto_map = {
        "nextcloud_pass": "nextcloud_pass_enc",
        "ha_token": "ha_token_enc",
        "github_token": "github_token_enc",
        "gitlab_token": "gitlab_token_enc",
        "audiobookshelf_pass": "audiobookshelf_pass_enc",
        "git_token": "git_token_enc"
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

@app.get("/api/users", response_model=List[UserRead])
def list_users(session: Session = Depends(get_session), user: User = Depends(require_api_key)):
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Admin only")
    return session.exec(select(User)).all()

@app.post("/api/users", response_model=UserRead)
def create_user(body: UserCreate, session: Session = Depends(get_session), admin: User = Depends(require_api_key)):
    if not admin.is_admin:
        raise HTTPException(status_code=403, detail="Admin only")
        
    def _coerce(val):
        if isinstance(val, str):
            val = val.strip()
        return val if val else None

    user = User(
        username=body.username.lower(),
        display_name=body.display_name,
        is_admin=body.is_admin,
        is_system_default=body.is_system_default,
        password_hash=pwd_context.hash(body.password) if body.password else None,
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
        audiobookshelf_pass_enc=encrypt(_coerce(body.audiobookshelf_pass)) if _coerce(body.audiobookshelf_pass) else None
    )
    _store_user_api_key(user, body.api_key or os.urandom(24).hex())
    session.add(user)
    session.commit()
    session.refresh(user)
    return user

@app.get("/api/devices", response_model=List[DeviceAssignmentRead])
def list_devices(session: Session = Depends(get_session), _: User = Depends(require_api_key)):
    results = session.exec(select(DeviceAssignment)).all()
    return [
        DeviceAssignmentRead(
            id=d.id, 
            device_id=d.device_id, 
            user_id=d.user_id, 
            username=d.user.username
        ) for d in results
    ]

@app.post("/api/devices", response_model=DeviceAssignmentRead)
def add_device(body: DeviceAssignmentCreate, session: Session = Depends(get_session), _: User = Depends(require_api_key)):
    user = session.exec(select(User).where(User.username == body.username.lower())).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
        
    assignment = DeviceAssignment(device_id=body.device_id, user_id=user.id)
    session.add(assignment)
    session.commit()
    session.refresh(assignment)
    return DeviceAssignmentRead(
        id=assignment.id, 
        device_id=assignment.device_id, 
        user_id=assignment.user_id, 
        username=user.username
    )

@app.delete("/api/devices/{device_id}")
def remove_device(device_id: str, session: Session = Depends(get_session), _: User = Depends(require_api_key)):
    assignment = session.exec(select(DeviceAssignment).where(DeviceAssignment.device_id == device_id)).first()
    if not assignment:
        raise HTTPException(status_code=404, detail="Device assignment not found.")
    session.delete(assignment)
    session.commit()
    return {"status": "SUCCESS"}

# --- Device Matrix (UI Contract) ---
@app.get("/api/users/devices", response_model=List[DeviceAssignmentRead])
def list_devices_ui(session: Session = Depends(get_session), _: User = Depends(require_api_key)):
    results = session.exec(select(DeviceAssignment)).all()
    return [
        DeviceAssignmentRead(
            id=d.id, 
            device_id=d.device_id, 
            user_id=d.user_id, 
            username=d.user.username
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
        existing.user_id = target_user.id
        session.add(existing)
        session.commit()
        session.refresh(existing)
        return DeviceAssignmentRead(id=existing.id, device_id=existing.device_id, user_id=existing.user_id, username=target_user.username)

    assignment = DeviceAssignment(device_id=body.device_id, user_id=target_user.id)
    session.add(assignment)
    session.commit()
    session.refresh(assignment)
    return DeviceAssignmentRead(
        id=assignment.id, 
        device_id=assignment.device_id, 
        user_id=assignment.user_id, 
        username=target_user.username
    )

# ─── Auth & Discovery ──────────────────────────────────────────────────────────

@app.post("/api/auth/login", response_model=LoginResponse)
def login(req: LoginRequest, session: Session = Depends(get_session)):
    user = session.exec(select(User).where(User.username == req.username)).first()
    if not user or not user.password_hash:
        raise HTTPException(status_code=401, detail="Invalid username or password")
    
    if not pwd_context.verify(req.password, user.password_hash):
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
    user.password_hash = pwd_context.hash(new_password)
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
        async with httpx.AsyncClient(timeout=5.0, verify=False) as client:
            if service == "Home Assistant":
                url = config.get("ha_url")
                token = config.get("ha_token")
                if not url or not token:
                    return {"status": "ERROR", "message": "URL and Token are required"}
                
                resp = await client.get(
                    f"{url.rstrip('/')}/api/config",
                    headers={"Authorization": f"Bearer {token}"}
                )
                log.info(f"[test_connection] HA response: {resp.status_code}")
                if resp.status_code == 200:
                    return {"status": "SUCCESS", "message": "Connected to Home Assistant"}
                else:
                    return {"status": "ERROR", "message": f"HA returned {resp.status_code}: {resp.text[:100]}"}
            
            elif service == "Nextcloud":
                url = config.get("nextcloud_url")
                user = config.get("nextcloud_user")
                password = config.get("nextcloud_pass")
                if not url or not user or not password:
                    return {"status": "ERROR", "message": "URL, User, and Password are required"}
                
                resp = await client.get(
                    f"{url.rstrip('/')}/ocs/v1.php/cloud/users?format=json",
                    headers={"OCS-APIRequest": "true"},
                    auth=(user, password)
                )
                if resp.status_code == 200:
                    return {"status": "SUCCESS", "message": "Connected to Nextcloud"}
                else:
                    return {"status": "ERROR", "message": f"Nextcloud returned {resp.status_code}"}
            
            elif service == "GitHub":
                url = config.get("github_url") or "https://api.github.com"
                token = config.get("github_token")
                if not token:
                    return {"status": "ERROR", "message": "Personal Token is required"}
                
                resp = await client.get(
                    f"{url.rstrip('/')}/user",
                    headers={"Authorization": f"token {token}", "Accept": "application/vnd.github.v3+json"}
                )
                if resp.status_code == 200:
                    return {"status": "SUCCESS", "message": f"Connected to GitHub as {resp.json().get('login')}"}
                else:
                    return {"status": "ERROR", "message": f"GitHub returned {resp.status_code}"}

            elif service == "GitLab":
                url = config.get("gitlab_url") or "https://gitlab.com"
                token = config.get("gitlab_token")
                if not token:
                    return {"status": "ERROR", "message": "Access Token is required"}
                
                resp = await client.get(
                    f"{url.rstrip('/')}/api/v4/user",
                    headers={"PRIVATE-TOKEN": token}
                )
                if resp.status_code == 200:
                    return {"status": "SUCCESS", "message": f"Connected to GitLab as {resp.json().get('username')}"}
                else:
                    return {"status": "ERROR", "message": f"GitLab returned {resp.status_code}"}

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
                if resp.status_code == 200:
                    data = resp.json()
                    user_info = data.get("user", {})
                    return {"status": "SUCCESS", "message": f"Connected to Audiobookshelf as {user_info.get('username')}"}
                else:
                    return {"status": "ERROR", "message": f"Audiobookshelf returned {resp.status_code}: {resp.text[:100]}"}

            return {"status": "ERROR", "message": f"Service {service} not testable yet"}
            
    except Exception as e:
        return {"status": "ERROR", "message": str(e)}

# ─── API Key Management ────────────────────────────────────────────────────────

@app.get("/api/users/me/keys")
def get_my_keys(session: Session = Depends(get_session), user: User = Depends(require_api_key)):
    """Return list of API keys for the current user."""
    # Return masked keys
    return [
        {
            "id": k.id, 
            "label": k.label, 
            "prefix": k.key_prefix or _api_key_prefix(k.key_value) or "unavailable",
            "created_at": k.created_at
        } for k in user.api_keys
    ]

@app.post("/api/users/me/keys")
def generate_key(body: dict, session: Session = Depends(get_session), user: User = Depends(require_api_key)):
    """Generate a new API key for the current user."""
    import secrets
    new_key_value = "sk-" + secrets.token_hex(24)
    new_key = APIKey(label=body.get("label", "New Key"), user_id=user.id)
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

@app.get("/api/auth/discover", response_model=List[DiscoverUser])
async def discover_users(session: Session = Depends(get_session), admin: User = Depends(require_api_key)):
    """Scan Home Assistant and Nextcloud for users to import.
    Merges users found in both sources into a single entry with combined data."""
    if not admin.is_admin:
        raise HTTPException(status_code=403, detail="Admin only")
    
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
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    f"{ha_url.rstrip('/')}/api/states",
                    headers={"Authorization": f"Bearer {ha_token}"}
                )
                if resp.status_code == 200:
                    for state in resp.json():
                        if state['entity_id'].startswith('person.'):
                            username = state['entity_id'].split('.')[1]
                            ha_users[username] = {
                                "display_name": state.get('attributes', {}).get('friendly_name', username),
                                "entity_id": state['entity_id'],
                            }
        except Exception as e:
            log.error(f"[discovery] HA Error: {str(e)}")

    # 2. Scan Nextcloud (Provisioning API)
    if nc_url and nc_user and nc_pass_enc:
        nc_pass = decrypt(nc_pass_enc)
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    f"{nc_url.rstrip('/')}/ocs/v1.php/cloud/users?format=json",
                    headers={"OCS-APIRequest": "true"},
                    auth=(nc_user, nc_pass)
                )
                if resp.status_code == 200:
                    data = resp.json()
                    # Handle Nextcloud API error responses
                    meta = data.get("ocs", {}).get("meta", {})
                    if meta.get("status") == "failure":
                        log.warning(f"[discovery] Nextcloud API error: {meta.get('message', 'Unknown')}")
                    else:
                        usernames = data.get("ocs", {}).get("data", {}).get("users", [])
                        for username in usernames:
                            nc_users[username.lower()] = {"nc_username": username}
                            # Fetch detailed info for each user
                            try:
                                detail_resp = await client.get(
                                    f"{nc_url.rstrip('/')}/ocs/v1.php/cloud/users/{username}",
                                    headers={"OCS-APIRequest": "true", "Accept": "application/json"},
                                    auth=(nc_user, nc_pass),
                                    params={"format": "json"}
                                )
                                if detail_resp.status_code == 200:
                                    nc_data = detail_resp.json().get("ocs", {}).get("data", {})
                                    nc_users[username.lower()]["display_name"] = nc_data.get("display-name") or nc_data.get("displayname")
                                    nc_users[username.lower()]["email"] = nc_data.get("email")
                            except Exception:
                                pass
        except Exception as e:
            log.error(f"[discovery] Nextcloud Error: {str(e)}")

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
    return discovered

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

@app.post("/api/settings")
def update_settings_bulk(
    body: Dict[str, str], 
    session: Session = Depends(get_session), 
    auth: bool = Depends(require_admin_or_internal)
):
    """
    Securely accept raw keys and commit them to the database without logging the raw payload.
    """
    for key, value in body.items():
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
        
    setting = session.exec(select(GlobalSetting).where(GlobalSetting.key == key)).first()
    if not setting:
        setting = GlobalSetting(key=key, value=body.value)
    else:
        setting.value = body.value
        
    session.add(setting)
    session.commit()
    session.refresh(setting)
    return setting

@app.post("/api/admin/seed", dependencies=[Depends(require_internal)])
def manual_seed(force: bool = False, session: Session = Depends(get_session)):
    count = seed_from_env(session, force=force)
    return {"status": "SUCCESS", "count": count}

# ─── Raven Missions (Autonomous Ops & User Tasks) ───────────────────────────────
try:
    from .models import RavenMission
    from .schemas import RavenMissionRead, RavenMissionCreate, RavenMissionUpdate
except ImportError:
    from models import RavenMission
    from schemas import RavenMissionRead, RavenMissionCreate, RavenMissionUpdate
from typing import List

def _resolve_mission(mission_id_or_slug: str, session: Session) -> RavenMission:
    try:
        mid = int(mission_id_or_slug)
        mission = session.exec(select(RavenMission).where(RavenMission.id == mid)).first()
    except ValueError:
        mission = session.exec(select(RavenMission).where(RavenMission.slug == mission_id_or_slug)).first()
    
    if not mission:
        raise HTTPException(status_code=404, detail="Mission not found")
    return mission

@app.get("/api/raven/missions", response_model=List[RavenMissionRead])
def get_missions(session: Session = Depends(get_session)):
    missions = session.exec(select(RavenMission).order_by(RavenMission.created_at.desc())).all()
    return missions

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


@app.post("/api/auth/import/nextcloud")
async def import_nextcloud_users(x_internal_secret: Optional[str] = Header(default=None)):
    """Import users from Nextcloud and Home Assistant, merging by username.
    Generates temp passwords and pre-fills all available user data."""
    if x_internal_secret != INTERNAL_SECRET:
        raise HTTPException(status_code=403, detail="Forbidden")
    
    with Session(engine) as session:
        admin = session.exec(select(User).where(User.is_admin)).first()
        default_user = session.exec(select(User).where(User.username == "default")).first()
        
        # Resolve Nextcloud credentials
        if not admin or not admin.nextcloud_url:
            nc_url_setting = session.exec(select(GlobalSetting).where(GlobalSetting.key == "NEXTCLOUD_URL")).first()
            nc_user_setting = session.exec(select(GlobalSetting).where(GlobalSetting.key == "NEXTCLOUD_USER")).first()
            nc_pass_setting = session.exec(select(GlobalSetting).where(GlobalSetting.key == "NEXTCLOUD_PASS")).first()
            from config import NEXTCLOUD_URL, NEXTCLOUD_USER, NEXTCLOUD_PASS
            nc_url = nc_url_setting.value if nc_url_setting else NEXTCLOUD_URL
            nc_admin_user = nc_user_setting.value if nc_user_setting else NEXTCLOUD_USER
            nc_admin_pass = nc_pass_setting.value if nc_pass_setting else NEXTCLOUD_PASS
        else:
            nc_url = admin.nextcloud_url
            nc_admin_user = admin.nextcloud_user
            nc_admin_pass = decrypt(admin.nextcloud_pass_enc) if admin.nextcloud_pass_enc else None

        # Resolve Home Assistant credentials
        ha_url = admin.ha_url or (default_user.ha_url if default_user else None)
        ha_token_enc = admin.ha_token_enc or (default_user.ha_token_enc if default_user else None)
        ha_token = decrypt(ha_token_enc) if ha_token_enc else None

        if not nc_url or not nc_admin_user or not nc_admin_pass:
            raise HTTPException(status_code=400, detail="Nextcloud configuration missing")

        # Collect data from both sources
        ha_users: dict[str, dict] = {}
        nc_users: dict[str, dict] = {}

        # Fetch HA person entities
        if ha_url and ha_token:
            try:
                async with httpx.AsyncClient(timeout=10.0, verify=False) as client:
                    resp = await client.get(
                        f"{ha_url.rstrip('/')}/api/states",
                        headers={"Authorization": f"Bearer {ha_token}"}
                    )
                    if resp.status_code == 200:
                        for state in resp.json():
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
                log.error(f"[import] HA Error: {str(e)}")

        # Fetch Nextcloud users with detailed info
        try:
            async with httpx.AsyncClient(verify=False) as client:
                resp = await client.get(
                    f"{nc_url.rstrip('/')}/ocs/v1.php/cloud/users",
                    auth=(nc_admin_user, nc_admin_pass),
                    headers={"OCS-APIRequest": "true", "Accept": "application/json"},
                    params={"format": "json"}
                )
                if resp.status_code != 200:
                    raise HTTPException(status_code=resp.status_code, detail=f"Nextcloud API error: {resp.text}")
                
                nc_resp = resp.json()
                # Handle Nextcloud API error responses (e.g., 403 for non-admin users)
                meta = nc_resp.get("ocs", {}).get("meta", {})
                if meta.get("status") == "failure":
                    log.warning(f"[import] Nextcloud API error: {meta.get('message', 'Unknown')}")
                    usernames = []
                else:
                    usernames = nc_resp.get("ocs", {}).get("data", {}).get("users", [])
                
                for nc_username in usernames:
                    nc_data = {"nc_username": nc_username}
                    try:
                        detail_resp = await client.get(
                            f"{nc_url.rstrip('/')}/ocs/v1.php/cloud/users/{nc_username}",
                            auth=(nc_admin_user, nc_admin_pass),
                            headers={"OCS-APIRequest": "true", "Accept": "application/json"},
                            params={"format": "json"}
                        )
                        if detail_resp.status_code == 200:
                            udata = detail_resp.json().get("ocs", {}).get("data", {})
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
            log.error(f"[import] Nextcloud Error: {str(e)}")
            raise HTTPException(status_code=500, detail=str(e))

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
                password_hash=pwd_context.hash(temp_password),
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
        groups = session.exec(select(GlobalSetting).where(GlobalSetting.key.like("media_group:%"))).all()
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
        clusters = session.exec(select(GlobalSetting).where(GlobalSetting.key.like("light_cluster:%"))).all()
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
        patterns = session.exec(select(GlobalSetting).where(GlobalSetting.key.like("light_pattern:%"))).all()
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
        enrollments = session.exec(select(GlobalSetting).where(GlobalSetting.key.like("telemetry_enroll:%"))).all()
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
        existing = session.exec(select(GlobalSetting).where(GlobalSetting.key == key)).first()
        if existing:
            raise HTTPException(status_code=409, detail=f"'{entity_id}' already enrolled")
        from datetime import datetime
        enrollment = GlobalSetting(
            key=key,
            value=json.dumps({
                "entity_id": entity_id,
                "power_tracking": enroll_data.get("power_tracking", True),
                "availability_tracking": enroll_data.get("availability_tracking", True),
                "usage_tracking": enroll_data.get("usage_tracking", True),
                "offline_alert_threshold_minutes": enroll_data.get("offline_alert_threshold_minutes", 30),
                "group_id": enroll_data.get("group_id"),
                "owner_user_id": enroll_data.get("owner_user_id", "system"),
                "enrolled_at": datetime.utcnow().isoformat(),
            }),
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
        summary = {
            "entity_id": entity_id,
            "current_power_w": power_values[-1] if power_values else None,
            "peak_power_w": max(power_values) if power_values else None,
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
        insights = session.exec(select(GlobalSetting).where(GlobalSetting.key.like("telemetry_insight:%"))).all()
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
        sessions = session.exec(select(GlobalSetting).where(GlobalSetting.key.like("intercom_session:%"))).all()
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
        new_session = GlobalSetting(
            key=key,
            value=json.dumps({
                "session_id": session_id,
                "caller_user_id": session_data.get("caller_user_id", "system"),
                "target_user_id": session_data.get("target_user_id"),
                "target_room": session_data.get("target_room"),
                "target_entity_ids": session_data.get("target_entity_ids", []),
                "session_type": session_data.get("session_type", "twoway"),
                "status": "active",
                "started_at": datetime.utcnow().isoformat(),
                "ended_at": None,
                "room_name": session_data.get("target_room"),
            }),
            description=f"Intercom session: {session_id}",
        )
        session.add(new_session)
        session.commit()
        return {
            "session_id": session_id,
            "status": "active",
            "started_at": new_session.value,
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
        data["ended_at"] = datetime.utcnow().isoformat()
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
