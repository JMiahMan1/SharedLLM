# services/identity/seed.py
"""
Migration / seed script for the Identity Service.

Reads the legacy USER_{USERNAME}_{SETTING} environment variables from the
monolith's .env and seeds the SQL database if it is empty. Run once on
first startup or call via the /api/admin/seed endpoint.
"""
import os
import logging
from sqlmodel import Session, select, text
from dotenv import load_dotenv

try:
    from .models import User, GlobalSetting, DeviceAssignment, APIKey, DEFAULT_GLOBAL_SETTINGS
    from .crypto import encrypt
except ImportError:
    from models import User, GlobalSetting, DeviceAssignment, APIKey, DEFAULT_GLOBAL_SETTINGS
    from crypto import encrypt

from passlib.context import CryptContext
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# Load legacy .env if available
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from config import LEGACY_ENV_PATH
if os.path.exists(LEGACY_ENV_PATH):
    load_dotenv(LEGACY_ENV_PATH)

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
    if not force:
        existing = session.exec(select(User)).first()
        if existing:
            log.info("[seed] Database already seeded — skipping.")
            return 0
    else:
        log.info("[seed] Forced re-seed: Clearing existing users/assignments.")
        # Clear using SQLModel to avoid table name mismatches
        for table in ["deviceassignment", "user", "apikey", "globalsetting"]:
            try:
                session.exec(text(f"DELETE FROM {table}"))
            except Exception:
                pass 
        session.commit()

    env_users = _parse_env_users()
    count = 0

    for udata in env_users.values():
        # Set default password for 'default' user if not already set
        password_hash = None
        is_admin = udata.get("is_admin", False)
        if udata["username"] == "default":
            password_hash = pwd_context.hash("admin")
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
            # Encrypt sensitive fields
            nextcloud_pass_enc=encrypt(udata.get("nextcloud_pass")),
            ha_token_enc=encrypt(udata.get("ha_token")),
            github_token_enc=encrypt(udata.get("github_token")),
            gitlab_token_enc=encrypt(udata.get("gitlab_token")),
            audiobookshelf_pass_enc=encrypt(udata.get("audiobookshelf_pass")),
        )
        session.add(user)
        count += 1

    # ── Seed Global Settings ──────────────────────────────────────────────────
    for ds in DEFAULT_GLOBAL_SETTINGS:
        existing = session.exec(select(GlobalSetting).where(GlobalSetting.key == ds["key"])).first()
        if not existing:
            session.add(GlobalSetting(**ds))
        elif force:
            existing.value = ds["value"]
            existing.description = ds.get("description")
            session.add(existing)

    session.commit()
    log.info(f"[seed] Seeded {count} user(s) and default settings.")
    return count
