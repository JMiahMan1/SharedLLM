# services/identity/models.py
"""
SQLModel database models for the Identity & Profile Service.
"""
from datetime import datetime
from typing import Optional
from sqlmodel import SQLModel, Field, Relationship


class User(SQLModel, table=True):
    """A user account with service credentials."""
    id: Optional[int] = Field(default=None, primary_key=True)
    username: str = Field(index=True, unique=True)
    display_name: str = Field(default="")
    is_admin: bool = Field(default=False)
    is_system_default: bool = Field(default=False)
    password_hash: Optional[str] = Field(default=None)
    api_key: Optional[str] = Field(default=None, index=True)
    api_key_enc: Optional[str] = Field(default=None)
    api_key_hash: Optional[str] = Field(default=None, index=True)

    # Plain-text fields
    nextcloud_url: Optional[str] = None
    nextcloud_user: Optional[str] = None
    ha_url: Optional[str] = None
    github_url: Optional[str] = None
    github_user: Optional[str] = None
    gitlab_url: Optional[str] = None
    gitlab_user: Optional[str] = None
    audiobookshelf_url: Optional[str] = None
    audiobookshelf_user: Optional[str] = None
    git_url: Optional[str] = None
    git_user: Optional[str] = None

    # Encrypted at rest — stored as Fernet ciphertext (base64 string)
    nextcloud_pass_enc: Optional[str] = None
    ha_token_enc: Optional[str] = None
    github_token_enc: Optional[str] = None
    gitlab_token_enc: Optional[str] = None
    audiobookshelf_pass_enc: Optional[str] = None
    git_token_enc: Optional[str] = None
    
    # Biometric voice profile (stored as a JSON string of embeddings)
    voice_fingerprint: Optional[str] = None

    # Relationships
    devices: list["DeviceAssignment"] = Relationship(back_populates="user")
    api_keys: list["APIKey"] = Relationship(back_populates="user")


class DeviceAssignment(SQLModel, table=True):
    """Maps an HA entity_id to a User for device-based identity resolution."""
    id: Optional[int] = Field(default=None, primary_key=True)
    device_id: str = Field(index=True, unique=True)  # e.g. "media_player.kitchen_speaker"
    user_id: int = Field(foreign_key="user.id")
    user: Optional[User] = Relationship(back_populates="devices")

class APIKey(SQLModel, table=True):
    """Secure access tokens for users and external clients."""
    id: Optional[int] = Field(default=None, primary_key=True)
    key_value: Optional[str] = Field(default=None, index=True, unique=True)
    key_hash: Optional[str] = Field(default=None, index=True, unique=True)
    key_prefix: Optional[str] = Field(default=None)
    label: str = Field(default="External Client")
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    user_id: int = Field(foreign_key="user.id")
    user: Optional[User] = Relationship(back_populates="api_keys")

class GlobalSetting(SQLModel, table=True):
    """System-wide configuration settings."""
    id: Optional[int] = Field(default=None, primary_key=True)
    key: str = Field(index=True, unique=True)
    value: str
    description: Optional[str] = None
