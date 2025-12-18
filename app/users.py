"""
User management system for multi-user RAG support.
Provides user-specific credentials and data isolation using environment variables.
"""

import os
from typing import Dict, Optional

# Default user configuration - shared data access
DEFAULT_USER = {
    "user": "default",
    "display_name": "Shared/Default User",
    "nextcloud_url": os.getenv("NEXTCLOUD_URL"),
    "nextcloud_user": os.getenv("NEXTCLOUD_USER"),
    "nextcloud_pass": os.getenv("NEXTCLOUD_PASS"),
    "ha_url": os.getenv("HA_URL"),
    "ha_token": os.getenv("HA_TOKEN"),
    "audiobookshelf_url": os.getenv("AUDIOBOOKSHELF_URL"),
    "audiobookshelf_user": os.getenv("AUDIOBOOKSHELF_USER"),
    "audiobookshelf_pass": os.getenv("AUDIOBOOKSHELF_PASS"),
    "is_default": True,
    "can_access_shared": True,
}

def get_all_users() -> Dict[str, Dict]:
    """Get all configured users from environment variables."""
    users = {"default": DEFAULT_USER}

    # Parse user configurations from environment variables
    # Format: USER_{USERNAME}_{SETTING} or {USERNAME}_{SETTING}
    for key, value in os.environ.items():
        if key.startswith("USER_") or "_" in key:
            parts = key.split("_", 2)
            if len(parts) >= 3:
                username = parts[1].lower()
                setting = "_".join(parts[2:]).lower()

                if username not in users:
                    users[username] = {
                        "user": username,
                        "display_name": f"User: {username}",
                        "is_default": False,
                        "can_access_shared": True,
                    }

                # Map environment variable to user config
                if setting == "display_name" or setting == "name":
                    users[username]["display_name"] = value
                elif setting == "nextcloud_user":
                    users[username]["nextcloud_user"] = value
                elif setting == "nextcloud_pass" or setting == "nextcloud_password":
                    users[username]["nextcloud_pass"] = value
                elif setting == "ha_token" or setting == "home_assistant_token":
                    users[username]["ha_token"] = value
                elif setting == "audiobookshelf_user":
                    users[username]["audiobookshelf_user"] = value
                elif setting == "audiobookshelf_pass" or setting == "audiobookshelf_password":
                    users[username]["audiobookshelf_pass"] = value
                elif setting == "can_access_shared":
                    users[username]["can_access_shared"] = value.lower() in ("true", "1", "yes")

    return users

def get_user_config(username: str) -> Dict:
    """Get configuration for a specific user."""
    all_users = get_all_users()

    # Return user-specific config if it exists
    if username in all_users:
        return all_users[username]

    # For unknown users, create a new user config based on default
    # but with user-specific overrides from environment variables
    new_user = DEFAULT_USER.copy()
    new_user.update({
        "user": username,
        "display_name": f"User: {username}",
        "is_default": False,
        "can_access_shared": True,  # All users can access shared data
    })

    # Allow environment variable overrides for user-specific credentials
    # Format: {USERNAME}_NEXTCLOUD_USER, {USERNAME}_HA_TOKEN, etc.
    username_upper = username.upper()
    if f"{username_upper}_NEXTCLOUD_USER" in os.environ:
        new_user["nextcloud_user"] = os.getenv(f"{username_upper}_NEXTCLOUD_USER")
    if f"{username_upper}_NEXTCLOUD_PASS" in os.environ:
        new_user["nextcloud_pass"] = os.getenv(f"{username_upper}_NEXTCLOUD_PASS")
    if f"{username_upper}_HA_TOKEN" in os.environ:
        new_user["ha_token"] = os.getenv(f"{username_upper}_HA_TOKEN")
    if f"{username_upper}_AUDIOBOOKSHELF_USER" in os.environ:
        new_user["audiobookshelf_user"] = os.getenv(f"{username_upper}_AUDIOBOOKSHELF_USER")
    if f"{username_upper}_AUDIOBOOKSHELF_PASS" in os.environ:
        new_user["audiobookshelf_pass"] = os.getenv(f"{username_upper}_AUDIOBOOKSHELF_PASS")

    return new_user

def get_user_creds(username: str = "default") -> Dict[str, str]:
    """Get credentials for a specific user (backwards compatibility)."""
    user_config = get_user_config(username)
    return {
        "user": user_config["user"],
        "nextcloud_url": user_config.get("nextcloud_url"),
        "nextcloud_user": user_config.get("nextcloud_user"),
        "nextcloud_pass": user_config.get("nextcloud_pass"),
        "ha_url": user_config.get("ha_url"),
        "ha_token": user_config.get("ha_token"),
        "audiobookshelf_url": user_config.get("audiobookshelf_url"),
        "audiobookshelf_user": user_config.get("audiobookshelf_user"),
        "audiobookshelf_pass": user_config.get("audiobookshelf_pass"),
    }

def list_users() -> Dict[str, str]:
    """List all configured users with their display names."""
    all_users = get_all_users()
    return {username: user_config.get("display_name", username)
            for username, user_config in all_users.items()}

def create_user(username: str, display_name: str = None, **credentials) -> Dict:
    """Create a new user (Note: Runtime creation not supported - configure via environment variables)."""
    raise NotImplementedError("User creation must be done via environment variables. Set USER_{USERNAME}_* environment variables.")

def update_user_credentials(username: str, **credentials) -> Dict:
    """Update credentials for an existing user (Note: Runtime updates not supported - configure via environment variables)."""
    raise NotImplementedError("User credential updates must be done via environment variables.")

def delete_user(username: str) -> bool:
    """Delete a user (Note: Runtime deletion not supported - remove environment variables)."""
    raise NotImplementedError("User deletion must be done by removing environment variables.")

# Environment variable documentation
"""
User Configuration via Environment Variables:

For user-specific credentials, set environment variables with the format:
USER_{USERNAME}_{SETTING} or {USERNAME}_{SETTING}

Examples:
- USER_JOHN_DISPLAY_NAME=John Doe
- USER_JOHN_NEXTCLOUD_USER=john@example.com
- USER_JOHN_NEXTCLOUD_PASS=password123
- USER_JOHN_HA_TOKEN=eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9...
- USER_JOHN_AUDIOBOOKSHELF_USER=john
- USER_JOHN_AUDIOBOOKSHELF_PASS=pass123

Supported settings:
- DISPLAY_NAME or NAME: Display name for the user
- NEXTCLOUD_USER: NextCloud username
- NEXTCLOUD_PASS or NEXTCLOUD_PASSWORD: NextCloud password
- HA_TOKEN or HOME_ASSISTANT_TOKEN: Home Assistant token
- AUDIOBOOKSHELF_USER: AudioBookShelf username
- AUDIOBOOKSHELF_PASS or AUDIOBOOKSHELF_PASSWORD: AudioBookShelf password
- CAN_ACCESS_SHARED: Whether user can access shared data (default: true)

All users automatically have access to shared data from the default user.
"""
