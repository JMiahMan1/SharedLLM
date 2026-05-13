# services/identity/main.py
"""
Microservice 1: Identity & Profile Service
Manages user profiles, device assignments, and secure credential resolution.
"""
import os
import logging
from contextlib import asynccontextmanager
from typing import List, Optional, Any, Dict

from fastapi import FastAPI, Depends, HTTPException, Header, Request, status, File, UploadFile
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

DATABASE_URL = os.getenv("IDENTITY_DATABASE_URL", "sqlite:////data/identity.db")
INTERNAL_SECRET = os.getenv("INTERNAL_SECRET", "change-me-in-production")
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434")

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

@app.post("/api/users/{username}/password")
def admin_set_password(username: str, req: dict, x_internal_secret: Optional[str] = Header(default=None)):
    if x_internal_secret != INTERNAL_SECRET:
        raise HTTPException(status_code=403, detail="Forbidden: Admin secret required")
    
    new_password = req.get("new_password")
    if not new_password:
        raise HTTPException(status_code=400, detail="new_password is required")

    with Session(engine) as session:
        user = session.exec(select(User).where(User.username == username)).first()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        
        user.password_hash = pwd_context.hash(new_password)
        session.add(user)
        session.commit()
        return {"status": "SUCCESS", "message": f"Password for @{username} updated"}

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
            
    # Trust admin API keys
    if authorization and authorization.startswith("Bearer "):
        token = authorization.split(" ")[1]
        user = _find_user_for_api_key(session, token)
        if user and user.is_admin:
            return True
            
    raise HTTPException(status_code=401, detail="Unauthorized")

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
        git_token=decrypt(user.git_token_enc) if user.git_token_enc else None

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
    """Scan Home Assistant and Nextcloud for users to import."""
    if not admin.is_admin:
        raise HTTPException(status_code=403, detail="Admin only")
    
    discovered = []
    
    # Resolve credentials to use (prefer admin's, fallback to default)
    default_user = session.exec(select(User).where(User.username == "default")).first()
    
    ha_url = admin.ha_url or (default_user.ha_url if default_user else None)
    ha_token_enc = admin.ha_token_enc or (default_user.ha_token_enc if default_user else None)
    
    nc_url = admin.nextcloud_url or (default_user.nextcloud_url if default_user else None)
    nc_user = admin.nextcloud_user or (default_user.nextcloud_user if default_user else None)
    nc_pass_enc = admin.nextcloud_pass_enc or (default_user.nextcloud_pass_enc if default_user else None)

    log.info(f"[discovery] Starting scan. HA_URL: {ha_url}, NC_URL: {nc_url}")

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
                            existing = session.exec(select(User).where(User.username == username)).first()
                            if not existing:
                                discovered.append(DiscoverUser(
                                    username=username,
                                    source="Home Assistant",
                                    display_name=state.get('attributes', {}).get('friendly_name', username)
                                ))
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
                    users = data.get('ocs', {}).get('data', {}).get('users', [])
                    for username in users:
                        existing = session.exec(select(User).where(User.username == username.lower())).first()
                        if not existing:
                            discovered.append(DiscoverUser(
                                username=username.lower(),
                                source="Nextcloud",
                                display_name=username.capitalize()
                            ))
        except Exception as e:
            log.error(f"[discovery] Nextcloud Error: {str(e)}")
            
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

@app.get("/api/raven/missions", response_model=List[RavenMissionRead])
def get_missions(session: Session = Depends(get_session)):
    missions = session.exec(select(RavenMission).order_by(RavenMission.created_at.desc())).all()
    return missions

@app.post("/api/raven/missions", response_model=RavenMissionRead)
def create_mission(
    body: RavenMissionCreate,
    session: Session = Depends(get_session)
):
    mission = RavenMission(**body.model_dump())
    session.add(mission)
    session.commit()
    session.refresh(mission)
    return mission

@app.patch("/api/raven/missions/{mission_id}", response_model=RavenMissionRead)
def update_mission(
    mission_id: int,
    body: RavenMissionUpdate,
    session: Session = Depends(get_session)
):
    mission = session.exec(select(RavenMission).where(RavenMission.id == mission_id)).first()
    if not mission:
        raise HTTPException(status_code=404, detail="Mission not found")
    
    update_data = body.model_dump(exclude_unset=True)
    for k, v in update_data.items():
        setattr(mission, k, v)
        
    session.add(mission)
    session.commit()
    session.refresh(mission)
    return mission

@app.delete("/api/raven/missions/{mission_id}")
def delete_mission(mission_id: int, session: Session = Depends(get_session)):
    mission = session.exec(select(RavenMission).where(RavenMission.id == mission_id)).first()
    if not mission:
        raise HTTPException(status_code=404, detail="Mission not found")
    session.delete(mission)
    session.commit()
    return {"status": "SUCCESS"}


@app.post("/api/auth/import/nextcloud")
async def import_nextcloud_users(x_internal_secret: Optional[str] = Header(default=None)):
    if x_internal_secret != INTERNAL_SECRET:
        raise HTTPException(status_code=403, detail="Forbidden")
    
    # 1. Get Nextcloud config from the default admin user
    with Session(engine) as session:
        admin = session.exec(select(User).where(User.is_admin == True)).first()
        if not admin or not admin.nextcloud_url:
            # Try global settings
            nc_url = session.exec(select(GlobalSetting).where(GlobalSetting.key == "NEXTCLOUD_URL")).first()
            nc_user = session.exec(select(GlobalSetting).where(GlobalSetting.key == "NEXTCLOUD_USER")).first()
            nc_pass = session.exec(select(GlobalSetting).where(GlobalSetting.key == "NEXTCLOUD_PASS")).first()
            
            url = nc_url.value if nc_url else os.getenv("NEXTCLOUD_URL")
            user = nc_user.value if nc_user else os.getenv("NEXTCLOUD_USER")
            password = nc_pass.value if nc_pass else os.getenv("NEXTCLOUD_PASS")
        else:
            url = admin.nextcloud_url
            user = admin.nextcloud_user
            password = decrypt(admin.nextcloud_pass_enc) if admin.nextcloud_pass_enc else None

        if not url or not user or not password:
            raise HTTPException(status_code=400, detail="Nextcloud configuration missing")

        # 2. Fetch users from Nextcloud OCS API
        try:
            async with httpx.AsyncClient(verify=False) as client:
                resp = await client.get(
                    f"{url.rstrip('/')}/ocs/v1.php/cloud/users",
                    auth=(user, password),
                    headers={"OCS-APIRequest": "true", "Accept": "application/json"},
                    params={"format": "json"}
                )
                if resp.status_code != 200:
                    raise HTTPException(status_code=resp.status_code, detail=f"Nextcloud API error: {resp.text}")
                
                data = resp.json().get("ocs", {}).get("data", {}).get("users", [])
                
                count = 0
                for nc_username in data:
                    existing = session.exec(select(User).where(User.username == nc_username)).first()
                    if not existing:
                        new_user = User(
                            username=nc_username,
                            display_name=nc_username.capitalize(),
                            is_admin=False,
                            nextcloud_url=url,
                            nextcloud_user=nc_username,
                            # We don't have their password, so they must use another method or admin must set it
                        )
                        session.add(new_user)
                        count += 1
                
                session.commit()
                return {"status": "SUCCESS", "message": f"Imported {count} users from Nextcloud"}
        except Exception as e:
            log.error(f"Nextcloud import failed: {e}")
            raise HTTPException(status_code=500, detail=str(e))
