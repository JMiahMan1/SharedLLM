# services/identity/models.py
"""
SQLModel database models for the Identity & Profile Service.
"""
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

    # Encrypted at rest — stored as Fernet ciphertext (base64 string)
    nextcloud_pass_enc: Optional[str] = None
    ha_token_enc: Optional[str] = None
    github_token_enc: Optional[str] = None
    gitlab_token_enc: Optional[str] = None
    audiobookshelf_pass_enc: Optional[str] = None

    # Relationships
    devices: list["DeviceAssignment"] = Relationship(back_populates="user")


class DeviceAssignment(SQLModel, table=True):
    """Maps an HA entity_id to a User for device-based identity resolution."""
    id: Optional[int] = Field(default=None, primary_key=True)
    device_id: str = Field(index=True, unique=True)  # e.g. "media_player.kitchen_speaker"
    user_id: int = Field(foreign_key="user.id")
    user: Optional[User] = Relationship(back_populates="devices")
