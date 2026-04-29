# services/identity/seed.py
"""
Migration / seed script for the Identity Service.

Reads the legacy USER_{USERNAME}_{SETTING} environment variables from the
monolith's .env and seeds the SQL database if it is empty. Run once on
first startup or call via the /api/admin/seed endpoint.
"""
import os
import logging
from sqlmodel import Session, select

from .models import User
from .crypto import encrypt

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
        "ha_url": os.getenv("HA_URL"),
        "ha_token": os.getenv("HA_TOKEN"),
        "audiobookshelf_url": os.getenv("AUDIOBOOKSHELF_URL"),
        "audiobookshelf_user": os.getenv("AUDIOBOOKSHELF_USER"),
        "audiobookshelf_pass": os.getenv("AUDIOBOOKSHELF_PASS"),
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
                "is_system_default": False,
            }

        mapping = {
            "display_name": "display_name",
            "name": "display_name",
            "nextcloud_user": "nextcloud_user",
            "nextcloud_pass": "nextcloud_pass",
            "nextcloud_password": "nextcloud_pass",
            "ha_url": "ha_url",
            "ha_token": "ha_token",
            "home_assistant_token": "ha_token",
            "audiobookshelf_url": "audiobookshelf_url",
            "audiobookshelf_user": "audiobookshelf_user",
            "audiobookshelf_pass": "audiobookshelf_pass",
            "audiobookshelf_password": "audiobookshelf_pass",
            "api_key": "api_key",
        }
        if setting in mapping:
            users[username][mapping[setting]] = value

    return users


def seed_from_env(session: Session) -> int:
    """
    Seed the database from environment variables.
    Only runs when the users table is empty. Returns the count of users seeded.
    """
    existing = session.exec(select(User)).first()
    if existing:
        log.info("[seed] Database already seeded — skipping.")
        return 0

    env_users = _parse_env_users()
    count = 0

    for udata in env_users.values():
        user = User(
            username=udata["username"],
            display_name=udata.get("display_name", ""),
            is_system_default=udata.get("is_system_default", False),
            api_key=udata.get("api_key"),
            nextcloud_url=udata.get("nextcloud_url"),
            nextcloud_user=udata.get("nextcloud_user"),
            ha_url=udata.get("ha_url"),
            audiobookshelf_url=udata.get("audiobookshelf_url"),
            audiobookshelf_user=udata.get("audiobookshelf_user"),
            # Encrypt sensitive fields
            nextcloud_pass_enc=encrypt(udata.get("nextcloud_pass")),
            ha_token_enc=encrypt(udata.get("ha_token")),
            audiobookshelf_pass_enc=encrypt(udata.get("audiobookshelf_pass")),
        )
        session.add(user)
        count += 1

    session.commit()
    log.info(f"[seed] Seeded {count} user(s) from environment variables.")
    return count
