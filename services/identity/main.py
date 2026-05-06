# services/identity/main.py
"""
Microservice 1: Identity & Profile Service
Manages user profiles, device assignments, and secure credential resolution.
"""
import os
import logging
from contextlib import asynccontextmanager
from typing import List

from fastapi import FastAPI, Depends, HTTPException, Header, Request, status, File, UploadFile
from sqlalchemy import inspect, text
from sqlmodel import Session, SQLModel, create_engine, select

try:
    from .models import User, DeviceAssignment, GlobalSetting, APIKey
    from .schemas import (
        ResolveRequest, ResolvedCredentials, 
        UserCreate, UserRead, UserUpdate,
        DeviceAssignmentRead, DeviceAssignmentCreate,
        LoginRequest, LoginResponse, DiscoverUser,
        GlobalSettingRead, GlobalSettingUpdate
    )
    from .crypto import encrypt, decrypt
    from .seed import seed_from_env, pwd_context
except (ImportError, ModuleNotFoundError):
    from models import User, DeviceAssignment, GlobalSetting, APIKey
    from schemas import (
        ResolveRequest, ResolvedCredentials, 
        UserCreate, UserRead, UserUpdate,
        DeviceAssignmentRead, DeviceAssignmentCreate,
        LoginRequest, LoginResponse, DiscoverUser,
        GlobalSettingRead, GlobalSettingUpdate
    )
    from crypto import encrypt, decrypt
    from seed import seed_from_env, pwd_context

import httpx

# ─── Config ────────────────────────────────────────────────────────────────────

log = logging.getLogger("identity")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s")

DATABASE_URL = os.getenv("IDENTITY_DATABASE_URL", "sqlite:////data/identity.db")
INTERNAL_SECRET = os.getenv("INTERNAL_SECRET", "change-me-in-production")

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})


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

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Ensure tables exist
    SQLModel.metadata.create_all(engine)
    _ensure_schema_upgrades()
    
    # Run initial seed if needed
    with Session(engine) as session:
        seed_from_env(session)
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

def require_internal(authorization: str = Header(None), x_internal_secret: str = Header(None, alias="X-Internal-Secret")):
    if x_internal_secret == INTERNAL_SECRET:
        return
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing internal token")
    token = authorization.split(" ")[1]
    if token != INTERNAL_SECRET:
        raise HTTPException(status_code=403, detail="Invalid internal token")

def require_api_key(authorization: str = Header(None), session: Session = Depends(get_session)) -> User:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing API Key")
    key = authorization.split(" ")[1]
    
    # Check new APIKey table
    api_key_obj = session.exec(select(APIKey).where(APIKey.key_value == key)).first()
    if api_key_obj:
        return api_key_obj.user
        
    # Fallback to legacy User.api_key (for session tokens)
    user = session.exec(select(User).where(User.api_key == key)).first()
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
        # Check new APIKey table
        api_key_obj = session.exec(select(APIKey).where(APIKey.key_value == req.api_key)).first()
        if api_key_obj:
            user = api_key_obj.user
        else:
            # Fallback to legacy User.api_key
            user = session.exec(select(User).where(User.api_key == req.api_key)).first()

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
        # Fallback to system default if user not found or not specified
        user = session.exec(select(User).where(User.username == "default")).first()
        if not user:
            raise HTTPException(status_code=404, detail="No valid identity found")

    # Decrypt sensitive fields
    return ResolvedCredentials(
        user=user.username,
        is_admin=user.is_admin,
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
        api_key=body.api_key or os.urandom(24).hex(),
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
    
    if not user.api_key:
        user.api_key = os.urandom(24).hex()
        session.add(user)
        session.commit()
        session.refresh(user)

    return LoginResponse(
        api_key=user.api_key,
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
            "prefix": k.key_value[:8] + "...",
            "created_at": k.created_at
        } for k in user.api_keys
    ]

@app.post("/api/users/me/keys")
def generate_key(body: dict, session: Session = Depends(get_session), user: User = Depends(require_api_key)):
    """Generate a new API key for the current user."""
    import secrets
    new_key_value = "sk-" + secrets.token_hex(24)
    new_key = APIKey(key_value=new_key_value, label=body.get("label", "New Key"), user_id=user.id)
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
def get_settings(session: Session = Depends(get_session), user: User = Depends(require_api_key)):
    return session.exec(select(GlobalSetting)).all()

@app.get("/api/settings/{key}", response_model=GlobalSettingRead)
def get_setting(key: str, session: Session = Depends(get_session)):
    setting = session.exec(select(GlobalSetting).where(GlobalSetting.key == key)).first()
    if not setting:
        raise HTTPException(status_code=404, detail="Setting not found")
    return setting

@app.patch("/api/settings/{key}", response_model=GlobalSettingRead)
def update_setting(key: str, body: GlobalSettingUpdate, session: Session = Depends(get_session), admin: User = Depends(require_api_key)):
    if not admin.is_admin:
        raise HTTPException(status_code=403, detail="Forbidden")
        
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
