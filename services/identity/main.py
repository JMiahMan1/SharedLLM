# services/identity/main.py
"""
Microservice 1: Identity & Profile Service
Manages user profiles, device assignments, and secure credential resolution.
"""
import os
import logging
from contextlib import asynccontextmanager
from typing import List

from fastapi import FastAPI, Depends, HTTPException, Header, Request, status
from sqlalchemy import inspect, text
from sqlmodel import Session, SQLModel, create_engine, select

try:
    from .models import User, DeviceAssignment
    from .schemas import (
        ResolveRequest, ResolvedCredentials,
        UserCreate, UserRead,
        DeviceAssignmentCreate, DeviceAssignmentRead,
    )
    from .crypto import encrypt, decrypt
    from .seed import seed_from_env, pwd_context
except (ImportError, ValueError):
    try:
        from identity.models import User, DeviceAssignment
        from identity.schemas import (
            ResolveRequest, ResolvedCredentials,
            UserCreate, UserRead,
            DeviceAssignmentCreate, DeviceAssignmentRead,
            LoginRequest, LoginResponse, DiscoverUser
        )
        from identity.crypto import encrypt, decrypt
        from identity.seed import seed_from_env, pwd_context
    except ImportError:
        from models import User, DeviceAssignment
        from schemas import (
            ResolveRequest, ResolvedCredentials,
            UserCreate, UserRead,
            DeviceAssignmentCreate, DeviceAssignmentRead,
            LoginRequest, LoginResponse, DiscoverUser
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
    columns = {column["name"] for column in inspector.get_columns("user")}
    upgrades = [
        ("is_admin", "ALTER TABLE user ADD COLUMN is_admin BOOLEAN NOT NULL DEFAULT 0"),
        ("password_hash", "ALTER TABLE user ADD COLUMN password_hash VARCHAR"),
        ("github_url", "ALTER TABLE user ADD COLUMN github_url VARCHAR"),
        ("github_user", "ALTER TABLE user ADD COLUMN github_user VARCHAR"),
        ("github_token_enc", "ALTER TABLE user ADD COLUMN github_token_enc VARCHAR"),
        ("gitlab_url", "ALTER TABLE user ADD COLUMN gitlab_url VARCHAR"),
        ("gitlab_user", "ALTER TABLE user ADD COLUMN gitlab_user VARCHAR"),
        ("gitlab_token_enc", "ALTER TABLE user ADD COLUMN gitlab_token_enc VARCHAR"),
    ]
    for column_name, ddl in upgrades:
        if column_name not in columns:
            with engine.begin() as conn:
                conn.execute(text(ddl))
            log.info("Applied identity schema upgrade: added user.%s column", column_name)

# ─── Lifespan ──────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    SQLModel.metadata.create_all(engine)
    _ensure_schema_upgrades()
    with Session(engine) as session:
        seeded = seed_from_env(session)
        if seeded:
            log.info(f"First-run seed complete: {seeded} user(s) added.")
    yield

app = FastAPI(title="Jarvis OS Identity", version="1.0.0", lifespan=lifespan)

from fastapi.responses import JSONResponse
import traceback

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    err_msg = f"Identity Error: {type(exc).__name__}: {str(exc)}"
    log.error(f"{err_msg}\n{traceback.format_exc()}")
    return JSONResponse(
        status_code=500,
        content={"status": "ERROR", "message": "Internal Identity Error", "detail": str(exc)}
    )

# ─── Dependencies ──────────────────────────────────────────────────────────────

def get_session():
    with Session(engine) as session:
        yield session


def require_internal(x_internal_secret: str = Header(...)):
    if x_internal_secret != INTERNAL_SECRET:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")


def require_api_key(authorization: str = Header(...), session: Session = Depends(get_session)):
    """Validate Bearer <api_key> against the users table."""
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid auth format")
    token = authorization.removeprefix("Bearer ").strip()
    user = session.exec(select(User).where(User.api_key == token)).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key")
    return user

# ─── Helpers ───────────────────────────────────────────────────────────────────

def _build_credentials(user: User, default_user: User | None) -> ResolvedCredentials:
    """
    Build a ResolvedCredentials payload, merging missing fields from the
    system default user (credential inheritance).
    """
    def _merge(field, default_field=None):
        """Return user's field, falling back to default_user's field."""
        val = getattr(user, field, None)
        if val is not None:
            return val
        if default_user:
            return getattr(default_user, default_field or field, None)
        return None

    def _merge_enc(enc_field):
        """Decrypt user's encrypted field, fall back to default_user."""
        val = decrypt(getattr(user, enc_field, None))
        if val is not None:
            return val
        if default_user:
            return decrypt(getattr(default_user, enc_field, None))
        return None

    return ResolvedCredentials(
        user=user.username,
        is_admin=bool(user.is_admin),
        nextcloud_url=_merge("nextcloud_url"),
        nextcloud_user=_merge("nextcloud_user"),
        nextcloud_pass=_merge_enc("nextcloud_pass_enc"),
        ha_url=_merge("ha_url"),
        ha_token=_merge_enc("ha_token_enc"),
        github_url=_merge("github_url"),
        github_user=_merge("github_user"),
        github_token=_merge_enc("github_token_enc"),
        gitlab_url=_merge("gitlab_url"),
        gitlab_user=_merge("gitlab_user"),
        gitlab_token=_merge_enc("gitlab_token_enc"),
        audiobookshelf_url=_merge("audiobookshelf_url"),
        audiobookshelf_user=_merge("audiobookshelf_user"),
        audiobookshelf_pass=_merge_enc("audiobookshelf_pass_enc"),
    )


def _get_default_user(session: Session) -> User | None:
    return session.exec(select(User).where(User.is_system_default == True)).first()  # noqa: E712


# ─── Core Resolution Endpoint ──────────────────────────────────────────────────

@app.post("/api/resolve", response_model=ResolvedCredentials, dependencies=[Depends(require_internal)])
def resolve_user(req: ResolveRequest, session: Session = Depends(get_session)):
    """
    Context Resolution Engine — priority order:
      1. voice_id  →  2. rag_user  →  3. device_id (DeviceAssignment)  →  4. system default
    """
    default_user = _get_default_user(session)
    user: User | None = None

    # Priority 1: voice_id
    if req.voice_id:
        user = session.exec(select(User).where(User.username == req.voice_id)).first()
        if user:
            log.info(f"[resolve] Resolved via voice_id='{req.voice_id}'")

    # Priority 2: rag_user
    if not user and req.rag_user:
        user = session.exec(select(User).where(User.username == req.rag_user)).first()
        if user:
            log.info(f"[resolve] Resolved via rag_user='{req.rag_user}'")

    # Priority 3: device_id → DeviceAssignment
    if not user and req.device_id:
        assignment = session.exec(
            select(DeviceAssignment).where(DeviceAssignment.device_id == req.device_id)
        ).first()
        if assignment:
            user = session.get(User, assignment.user_id)
            if user:
                log.info(f"[resolve] Resolved via device_id='{req.device_id}' → user='{user.username}'")

    # Priority 4: system default
    if not user:
        user = default_user
        if user:
            log.info("[resolve] Falling back to system default user.")

    if not user:
        raise HTTPException(status_code=404, detail="No user could be resolved and no default user exists.")

    return _build_credentials(user, default_user if user != default_user else None)


# ─── User CRUD ─────────────────────────────────────────────────────────────────

@app.get("/api/users", response_model=List[UserRead])
def list_users(session: Session = Depends(get_session), _: User = Depends(require_api_key)):
    users = session.exec(select(User)).all()
    return [
        UserRead(
            id=u.id, username=u.username, display_name=u.display_name,
            is_admin=u.is_admin,
            is_system_default=u.is_system_default,
            nextcloud_url=u.nextcloud_url, nextcloud_user=u.nextcloud_user,
            ha_url=u.ha_url, audiobookshelf_url=u.audiobookshelf_url,
            github_url=u.github_url, github_user=u.github_user,
            gitlab_url=u.gitlab_url, gitlab_user=u.gitlab_user,
            audiobookshelf_user=u.audiobookshelf_user,
        )
        for u in users
    ]


@app.post("/api/users", response_model=UserRead, status_code=201)
def create_user(body: UserCreate, session: Session = Depends(get_session), _: User = Depends(require_api_key)):
    existing = session.exec(select(User).where(User.username == body.username)).first()
    if existing:
        raise HTTPException(status_code=409, detail=f"User '{body.username}' already exists.")

    user = User(
        username=body.username,
        display_name=body.display_name,
        is_admin=body.is_admin,
        is_system_default=body.is_system_default,
        api_key=body.api_key,
        nextcloud_url=body.nextcloud_url,
        nextcloud_user=body.nextcloud_user,
        ha_url=body.ha_url,
        github_url=body.github_url,
        github_user=body.github_user,
        gitlab_url=body.gitlab_url,
        gitlab_user=body.gitlab_user,
        audiobookshelf_url=body.audiobookshelf_url,
        audiobookshelf_user=body.audiobookshelf_user,
        nextcloud_pass_enc=encrypt(body.nextcloud_pass),
        ha_token_enc=encrypt(body.ha_token),
        github_token_enc=encrypt(body.github_token),
        gitlab_token_enc=encrypt(body.gitlab_token),
        audiobookshelf_pass_enc=encrypt(body.audiobookshelf_pass),
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    return UserRead(
        id=user.id, username=user.username, display_name=user.display_name,
        is_admin=user.is_admin,
        is_system_default=user.is_system_default,
        nextcloud_url=user.nextcloud_url, nextcloud_user=user.nextcloud_user,
        ha_url=user.ha_url, audiobookshelf_url=user.audiobookshelf_url,
        github_url=user.github_url, github_user=user.github_user,
        gitlab_url=user.gitlab_url, gitlab_user=user.gitlab_user,
        audiobookshelf_user=user.audiobookshelf_user,
    )


@app.patch("/api/users/me", response_model=UserRead)
def update_me(body: UserUpdate, session: Session = Depends(get_session), user: User = Depends(require_api_key)):
    return _do_update(user, body, session)


@app.patch("/api/users/{username}", response_model=UserRead)
def update_user(username: str, body: UserUpdate, session: Session = Depends(get_session), admin: User = Depends(require_api_key)):
    if not admin.is_admin and admin.username != username:
        raise HTTPException(status_code=403, detail="Forbidden")
    
    user = session.exec(select(User).where(User.username == username)).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found.")
    
    return _do_update(user, body, session)


def _do_update(user: User, body: UserUpdate, session: Session):
    update_data = body.model_dump(exclude_unset=True)
    
    # Handle sensitive fields (encryption)
    enc_map = {
        "nextcloud_pass": "nextcloud_pass_enc",
        "ha_token": "ha_token_enc",
        "github_token": "github_token_enc",
        "gitlab_token": "gitlab_token_enc",
        "audiobookshelf_pass": "audiobookshelf_pass_enc",
    }
    
    for plain, enc in enc_map.items():
        if plain in update_data:
            val = update_data.pop(plain)
            setattr(user, enc, encrypt(val))
            
    for key, value in update_data.items():
        setattr(user, key, value)
        
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


@app.post("/api/admin/users/{username}/admin", dependencies=[Depends(require_internal)])
def set_user_admin(username: str, is_admin: bool = True, session: Session = Depends(get_session)):
    user = session.exec(select(User).where(User.username == username)).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found.")

    user.is_admin = is_admin
    session.add(user)
    session.commit()
    session.refresh(user)
    return {
        "status": "SUCCESS",
        "username": user.username,
        "is_admin": user.is_admin,
    }


@app.delete("/api/users/{username}", status_code=204)
def delete_user(username: str, session: Session = Depends(get_session), _: User = Depends(require_api_key)):
    user = session.exec(select(User).where(User.username == username)).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found.")
    session.delete(user)
    session.commit()


# ─── Device Assignment CRUD ────────────────────────────────────────────────────

@app.get("/api/devices", response_model=List[DeviceAssignmentRead])
def list_devices(session: Session = Depends(get_session), _: User = Depends(require_api_key)):
    assignments = session.exec(select(DeviceAssignment)).all()
    result = []
    for a in assignments:
        u = session.get(User, a.user_id)
        result.append(DeviceAssignmentRead(
            id=a.id, device_id=a.device_id, user_id=a.user_id,
            username=u.username if u else "unknown",
        ))
    return result


@app.post("/api/devices", response_model=DeviceAssignmentRead, status_code=201)
def assign_device(body: DeviceAssignmentCreate, session: Session = Depends(get_session), _: User = Depends(require_api_key)):
    user = session.exec(select(User).where(User.username == body.username)).first()
    if not user:
        raise HTTPException(status_code=404, detail=f"User '{body.username}' not found.")

    existing = session.exec(select(DeviceAssignment).where(DeviceAssignment.device_id == body.device_id)).first()
    if existing:
        existing.user_id = user.id
        session.add(existing)
        session.commit()
        session.refresh(existing)
        return DeviceAssignmentRead(id=existing.id, device_id=existing.device_id, user_id=existing.user_id, username=user.username)

    assignment = DeviceAssignment(device_id=body.device_id, user_id=user.id)
    session.add(assignment)
    session.commit()
    session.refresh(assignment)
    return DeviceAssignmentRead(id=assignment.id, device_id=assignment.device_id, user_id=assignment.user_id, username=user.username)


@app.delete("/api/devices/{device_id}", status_code=204)
def remove_device(device_id: str, session: Session = Depends(get_session), _: User = Depends(require_api_key)):
    assignment = session.exec(select(DeviceAssignment).where(DeviceAssignment.device_id == device_id)).first()
    if not assignment:
        raise HTTPException(status_code=404, detail="Device assignment not found.")
    session.delete(assignment)
    session.commit()


# ─── Auth & Discovery ──────────────────────────────────────────────────────────

@app.post("/api/auth/login", response_model=LoginResponse)
def login(req: LoginRequest, session: Session = Depends(get_session)):
    user = session.exec(select(User).where(User.username == req.username)).first()
    if not user or not user.password_hash:
        raise HTTPException(status_code=401, detail="Invalid username or password")
    
    if not pwd_context.verify(req.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid username or password")
    
    # Ensure user has an API key to return as token
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

@app.get("/api/auth/discover", response_model=List[DiscoverUser])
async def discover_users(session: Session = Depends(get_session), admin: User = Depends(require_api_key)):
    """Scan Home Assistant and Nextcloud for users to import."""
    if not admin.is_admin:
        raise HTTPException(status_code=403, detail="Admin only")
    
    discovered = []
    
    # 1. Scan Home Assistant (Person entities)
    log.info(f"[discovery] Starting scan for user: {admin.username} (HA_URL: {admin.ha_url}, NC_URL: {admin.nextcloud_url})")
    if admin.ha_url and admin.ha_token_enc:
        ha_token = decrypt(admin.ha_token_enc)
        log.info(f"[discovery] Decrypted HA token: {bool(ha_token)}")
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(
                    f"{admin.ha_url.rstrip('/')}/api/config",
                    headers={"Authorization": f"Bearer {ha_token}"}
                )
                log.info(f"[discovery] HA /api/config status: {resp.status_code}")
                if resp.status_code == 200:
                    # Note: HA /api/config doesn't return full user list easily without admin auth
                    # We'll use person entities as a proxy for users
                    resp_persons = await client.get(
                        f"{admin.ha_url.rstrip('/')}/api/states",
                        headers={"Authorization": f"Bearer {ha_token}"}
                    )
                    log.info(f"[discovery] HA /api/states status: {resp_persons.status_code}")
                    if resp_persons.status_code == 200:
                        for state in resp_persons.json():
                            if state['entity_id'].startswith('person.'):
                                username = state['entity_id'].split('.')[1]
                                discovered.append(DiscoverUser(
                                    username=username,
                                    source="Home Assistant",
                                    display_name=state.get('attributes', {}).get('friendly_name')
                                ))
        except Exception as e:
            log.error(f"[discovery] Failed to scan HA for users: {e}")

    # 2. Scan Nextcloud (OCS API)
    if admin.nextcloud_url and admin.nextcloud_user and admin.nextcloud_pass_enc:
        nc_pass = decrypt(admin.nextcloud_pass_enc)
        log.info(f"[discovery] Decrypted NC pass: {bool(nc_pass)}")
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                # Nextcloud OCS User provisioning API
                url = f"{admin.nextcloud_url.rstrip('/')}/ocs/v1.php/cloud/users"
                log.info(f"[discovery] Querying Nextcloud OCS: {url} as {admin.nextcloud_user}")
                resp = await client.get(
                    url,
                    auth=(admin.nextcloud_user, nc_pass),
                    headers={"OCS-APIRequest": "true"}
                )
                if resp.status_code == 200:
                    import xml.etree.ElementTree as ET
                    root = ET.fromstring(resp.text)
                    for user_el in root.findall(".//element"):
                        username = user_el.text
                        if username:
                            discovered.append(DiscoverUser(
                                username=username,
                                source="Nextcloud"
                            ))
        except Exception as e:
            log.warning(f"Failed to scan Nextcloud for users: {e}")

    log.info(f"[discovery] Discovery complete. Found {len(discovered)} users.")
    return discovered


# ─── Admin ─────────────────────────────────────────────────────────────────────

@app.post("/api/admin/seed", dependencies=[Depends(require_internal)])
def manual_seed(force: bool = False, session: Session = Depends(get_session)):
    count = seed_from_env(session, force=force)
    return {"seeded": count, "forced": force}


@app.get("/health")
def health():
    return {"status": "ok", "service": "identity"}
