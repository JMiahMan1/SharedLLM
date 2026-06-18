# services/identity/seed.py
"""
Migration / seed script for the Identity Service.

Reads the legacy USER_{USERNAME}_{SETTING} environment variables from the
monolith's .env and seeds the SQL database if it is empty. Run once on
first startup or call via the /api/admin/seed endpoint.
"""
import hashlib
import os
import logging
from sqlmodel import Session, select, text
from dotenv import load_dotenv

from services.identity.models import User, GlobalSetting, DEFAULT_GLOBAL_SETTINGS
from services.identity.crypto import encrypt

def hash_password(password: str) -> str:
    salt = os.urandom(16)
    pwd_hash = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 260000)
    return f"pbkdf2_sha256${salt.hex()}${pwd_hash.hex()}"

def verify_password(password: str, stored: str) -> bool:
    if not stored or not stored.startswith("pbkdf2_sha256$"):
        return False
    parts = stored.split("$")
    salt = bytes.fromhex(parts[1])
    pwd_hash = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 260000)
    stored_hash = bytes.fromhex(parts[2])
    return pwd_hash == stored_hash

# Load legacy .env if available
import sys
if "PYTEST_CURRENT_TEST" not in os.environ and "pytest" not in sys.modules:
    from services.config import LEGACY_ENV_PATH as _LEGACY_ENV_PATH

    _legacy_env = _LEGACY_ENV_PATH
    if os.path.exists(_legacy_env):
        load_dotenv(_legacy_env)
    elif os.path.exists(".env"):
        load_dotenv(".env")
    else:
        # Try loading .env from current directory as last resort
        load_dotenv(".env", override=True)

log = logging.getLogger("identity.seed")


def _parse_env_users() -> dict:
    """
    Replicate the legacy get_all_users() logic from app/users.py.
    Returns a dict keyed by lowercase username.
    """
    users: dict[str, dict] = {}

    # ── Always include the DEFAULT system user from bare env vars ──────────────
    default = {
        "username": "default",
        "display_name": "Shared/Default User",
        "is_system_default": True,
        "nextcloud_url": os.getenv("NEXTCLOUD_URL"),
        "nextcloud_user": os.getenv("NEXTCLOUD_USER"),
        "nextcloud_pass": os.getenv("NEXTCLOUD_PASS"),
        "ha_url": os.getenv("HA_URL") or os.getenv("HOME_ASSISTANT_URL"),
        "ha_token": os.getenv("HA_TOKEN") or os.getenv("HOME_ASSISTANT_TOKEN"),
        "github_url": os.getenv("GITHUB_URL") or os.getenv("GIT_URL"),
        "github_user": os.getenv("GITHUB_USER") or os.getenv("GIT_USER"),
        "github_token": os.getenv("GITHUB_TOKEN") or os.getenv("GIT_TOKEN"),
        "gitlab_url": os.getenv("GITLAB_URL"),
        "gitlab_user": os.getenv("GITLAB_USER"),
        "gitlab_token": os.getenv("GITLAB_TOKEN"),
        "audiobookshelf_url": os.getenv("AUDIOBOOKSHELF_URL") or os.getenv("ABS_URL"),
        "audiobookshelf_user": os.getenv("AUDIOBOOKSHELF_USER") or os.getenv("ABS_USER"),
        "audiobookshelf_pass": os.getenv("AUDIOBOOKSHELF_PASS") or os.getenv("ABS_PASS"),
        "mass_url": os.getenv("MA_URL") or os.getenv("MUSIC_ASSISTANT_URL"),
        "mass_token": os.getenv("MA_TOKEN") or os.getenv("MUSIC_ASSISTANT_TOKEN"),
        "skylight_url": os.getenv("SKYLIGHT_URL"),
        "skylight_email": os.getenv("SKYLIGHT_EMAIL"),
        "skylight_pass": os.getenv("SKYLIGHT_PASS"),
    }
    users["default"] = default

    # ── Parse USER_{USERNAME}_{SETTING} vars ──────────────────────────────────
    for key, value in os.environ.items():
        if not key.startswith("USER_"):
            continue
        parts = key.split("_", 2)
        if len(parts) < 3:
            continue
        username = parts[1].lower()
        setting = parts[2].lower()

        if username not in users:
            users[username] = {
                "username": username,
                "display_name": f"User: {username}",
                "is_admin": False,
                "is_system_default": False,
            }

        mapping = {
            "is_admin": "is_admin",
            "admin": "is_admin",
            "display_name": "display_name",
            "name": "display_name",
            "nextcloud_user": "nextcloud_user",
            "nextcloud_pass": "nextcloud_pass",
            "nextcloud_password": "nextcloud_pass",
            "ha_url": "ha_url",
            "ha_token": "ha_token",
            "home_assistant_token": "ha_token",
            "github_url": "github_url",
            "github_user": "github_user",
            "github_token": "github_token",
            "git_url": "github_url",
            "git_user": "github_user",
            "git_token": "github_token",
            "gitlab_url": "gitlab_url",
            "gitlab_user": "gitlab_user",
            "gitlab_token": "gitlab_token",
            "audiobookshelf_url": "audiobookshelf_url",
            "audiobookshelf_user": "audiobookshelf_user",
            "audiobookshelf_pass": "audiobookshelf_pass",
            "audiobookshelf_password": "audiobookshelf_pass",
            "mass_url": "mass_url",
            "mass_token": "mass_token",
            "music_assistant_url": "mass_url",
            "music_assistant_token": "mass_token",
            "skylight_url": "skylight_url",
            "skylight_email": "skylight_email",
            "skylight_pass": "skylight_pass",
            "api_key": "api_key",
        }
        if setting in mapping:
            mapped = mapping[setting]
            if mapped == "is_admin":
                users[username][mapped] = str(value).strip().lower() in {"1", "true", "yes", "on"}
            else:
                users[username][mapped] = value

    return users


def seed_from_env(session: Session, force: bool = False) -> int:
    """
    Seed the database from environment variables.
    If force is True, clears existing users/assignments first.
    """
    if force:
        log.info("[seed] Forced re-seed: Clearing existing users/assignments.")
        # Clear using SQLModel to avoid table name mismatches
        for table in ["deviceassignment", "user", "apikey", "globalsetting"]:
            try:
                session.execute(text(f"DELETE FROM {table}"))  # type: ignore[deprecated]
            except Exception:
                pass 
        session.commit()

    count = 0
    has_users = session.exec(select(User)).first() is not None
    if not has_users or force:
        env_users = _parse_env_users()
        for udata in env_users.values():
            # Set default password for 'default' user if not already set
            password_hash = None
            is_admin = udata.get("is_admin", False)
            if udata["username"] == "default":
                raw_pwd = os.getenv("DEFAULT_ADMIN_PASSWORD", "changeme")
                if len(raw_pwd) > 72:
                    raw_pwd = raw_pwd[:72]
                password_hash = hash_password(raw_pwd)
                is_admin = True # Default user should be admin for first setup

            user = User(
                username=udata["username"],
                display_name=udata.get("display_name", ""),
                is_admin=is_admin,
                is_system_default=udata.get("is_system_default", False),
                password_hash=password_hash,
                api_key=udata.get("api_key") or os.urandom(24).hex(), # Ensure API key exists
                nextcloud_url=udata.get("nextcloud_url"),
                nextcloud_user=udata.get("nextcloud_user"),
                ha_url=udata.get("ha_url"),
                github_url=udata.get("github_url"),
                github_user=udata.get("github_user"),
                gitlab_url=udata.get("gitlab_url"),
                gitlab_user=udata.get("gitlab_user"),
                audiobookshelf_url=udata.get("audiobookshelf_url"),
                audiobookshelf_user=udata.get("audiobookshelf_user"),
                mass_url=udata.get("mass_url"),
                skylight_url=udata.get("skylight_url"),
                skylight_email=udata.get("skylight_email"),
                # Encrypt sensitive fields
                nextcloud_pass_enc=encrypt(udata.get("nextcloud_pass")),
                ha_token_enc=encrypt(udata.get("ha_token")),
                github_token_enc=encrypt(udata.get("github_token")),
                gitlab_token_enc=encrypt(udata.get("gitlab_token")),
                audiobookshelf_pass_enc=encrypt(udata.get("audiobookshelf_pass")),
                mass_token_enc=encrypt(udata.get("mass_token")),
                skylight_pass_enc=encrypt(udata.get("skylight_pass")),
            )
            session.add(user)
            count += 1
        session.commit()
    else:
        log.info("[seed] Users already exist — skipping user seeding.")

    # ── Seed Global Settings ──────────────────────────────────────────────────
    for ds in DEFAULT_GLOBAL_SETTINGS:
        existing = session.exec(select(GlobalSetting).where(GlobalSetting.key == ds["key"])).first()
        if not existing:
            setting = GlobalSetting(key=ds["key"], value=ds["value"])
            if "description" in ds:
                setting.description = ds["description"]
            session.add(setting)
        elif force:
            existing.value = ds["value"]
            existing.description = ds.get("description")
            session.add(existing)

    # ── Seed OLLAMA_URL from .env (seed-only, not in DEFAULT_GLOBAL_SETTINGS) ─
    # Read directly from .env file to avoid import-time caching issues
    env_ollama_url = os.getenv("OLLAMA_URL", "")
    if not env_ollama_url and os.path.exists(".env"):
        from dotenv import dotenv_values
        env_vals = dotenv_values(".env")
        env_ollama_url = env_vals.get("OLLAMA_URL", "")
    if env_ollama_url:
        existing = session.exec(select(GlobalSetting).where(GlobalSetting.key == "llm_local_url")).first()
        if not existing:
            session.add(GlobalSetting(key="llm_local_url", value=env_ollama_url, description="Base URL for local LLM inference (Ollama, llama.cpp server, or compatible API). Seeded from .env OLLAMA_URL on first startup."))
            log.info(f"[seed] Seeded OLLAMA_URL from .env: {env_ollama_url}")
        elif not existing.value or force:
            existing.value = env_ollama_url
            session.add(existing)
            log.info(f"[seed] Seeded OLLAMA_URL from .env: {env_ollama_url}")

    # ── Seed additional .env vars into GlobalSettings ─────────────────────────
    env_to_global = {
        "SEARXNG_URL": "searxng_url",
        "RAG_HOSTNAME": "rag_hostname",
        "RAG_ADDRESS": "rag_address",
        "HA_DEFAULT_USER": "ha_default_user",
        "SKYLIGHT_URL": "skylight_url",
        "SKYLIGHT_EMAIL": "skylight_email",
    }
    for env_key, global_key in env_to_global.items():
        env_val = os.getenv(env_key)
        if env_val:
            existing = session.exec(select(GlobalSetting).where(GlobalSetting.key == global_key)).first()
            if not existing:
                session.add(GlobalSetting(key=global_key, value=env_val))
                log.info(f"[seed] Seeded {env_key} -> {global_key}: {env_val}")
            elif not existing.value or force:
                existing.value = env_val
                session.add(existing)
                log.info(f"[seed] Seeded {env_key} -> {global_key}: {env_val}")

    # ── Seed models from .env (seed-only, overrides DEFAULT_GLOBAL_SETTINGS) ──
    env_models = {
        "ASSISTANT_MODEL": "assistant_model",
        "CODING_MODEL": "coding_model",
        "LIBRARIAN_MODEL": "librarian_model",
    }
    for env_key, global_key in env_models.items():
        env_val = os.getenv(env_key)
        if env_val:
            existing = session.exec(select(GlobalSetting).where(GlobalSetting.key == global_key)).first()
            if not existing:
                session.add(GlobalSetting(key=global_key, value=env_val, description=f"Global {global_key} model. Seeded from .env {env_key} on first startup."))
                log.info(f"[seed] Seeded {env_key} -> {global_key}: {env_val}")
            elif not existing.value or existing.value == "qwen3:8b" or force:
                existing.value = env_val
                session.add(existing)
                log.info(f"[seed] Seeded {env_key} -> {global_key}: {env_val}")

    # ── Seed SKYLIGHT_PASS (encrypted) ────────────────────────────────────────
    skylight_pass = os.getenv("SKYLIGHT_PASS")
    if skylight_pass:
        existing = session.exec(select(GlobalSetting).where(GlobalSetting.key == "skylight_pass_enc")).first()
        if not existing:
            session.add(GlobalSetting(key="skylight_pass_enc", value=encrypt(skylight_pass) or ""))
            log.info("[seed] Seeded SKYLIGHT_PASS (encrypted)")
        elif not existing.value or force:
            existing.value = encrypt(skylight_pass) or ""
            session.add(existing)
            log.info("[seed] Re-seeded SKYLIGHT_PASS (encrypted)")

    session.commit()
    log.info(f"[seed] Seeded {count} user(s) and default settings.")
    return count
