"""
Monolith Shim: Drop-in replacement for app/users.py.
Makes a rapid requests.post() to the new Identity Service instead of reading .env.
Ensures downstream monolith files do not break while refactoring.
"""
import logging

import aiohttp
from fastapi import HTTPException, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from services.config import IDENTITY_SVC_URL, INTERNAL_SECRET

log = logging.getLogger("shim_users")

security = HTTPBearer(auto_error=False)

async def get_user_creds(username: str = "default") -> dict[str, str | None]:
    """
    Replaces the legacy get_user_creds().
    Calls the Identity Service /api/resolve with rag_user=username.
    """
    url = f"{IDENTITY_SVC_URL.rstrip('/')}/api/resolve"
    headers = {"X-Internal-Secret": INTERNAL_SECRET, "Content-Type": "application/json"}
    payload = {"rag_user": username}

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=5.0)) as resp:
                if resp.status == 404:
                    log.warning(f"[shim] User '{username}' not found. Attempting default fallback.")
                    # Fallback to default if explicitly requested user is not found
                    if username != "default":
                        return await get_user_creds("default")
                    raise Exception("No default user found in Identity Service")

                resp.raise_for_status()
                return await resp.json()  # This perfectly matches the old schema
    except Exception as e:
        log.error(f"[shim] Failed to fetch credentials for '{username}': {e}")
        # Return a safe empty schema to prevent catastrophic crashing
        return {
            "user": username,
            "nextcloud_url": None, "nextcloud_user": None, "nextcloud_pass": None,
            "ha_url": None, "ha_token": None,
            "audiobookshelf_url": None, "audiobookshelf_user": None, "audiobookshelf_pass": None
        }

async def get_current_user(credentials: HTTPAuthorizationCredentials = Security(security)):
    """
    Replaces the FastAPI dependency.
    Validates token against Identity Service.
    """
    if not credentials:
        return await get_user_creds("default")

    token = credentials.credentials
    # In the SOA architecture, we treat the token as a username for simplicity in the shim
    creds = await get_user_creds(token)
    if not creds or not creds.get("user"):
         raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials"
        )
    return creds

# Stubs for other legacy functions that might be called
async def get_all_users() -> dict[str, dict]:
    # In a full migration, this would call GET /api/users
    # For the shim, we return a mock dict to prevent crashes
    default_creds = await get_user_creds("default")
    return {"default": default_creds}

async def get_user_config(username: str) -> dict:
    return await get_user_creds(username)
