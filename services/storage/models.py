from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


ProviderKind = Literal["nextcloud"]


class ProviderConfig(BaseModel):
    kind: ProviderKind
    settings: dict[str, Any] = Field(default_factory=dict)


class StorageEntry(BaseModel):
    path: str
    name: str
    is_dir: bool
    size: int | None = None
    mtime: str | None = None
    content_type: str | None = None


class ContentIndexItem(BaseModel):
    path: str
    name: str
    is_dir: bool
    item_type: str
    subtype: str
    role: str
    mime_type: str | None = None
    extension: str | None = None
    size: int | None = None
    mtime: str | None = None
    signals: list[str] = Field(default_factory=list)
    extractable_capabilities: list[str] = Field(default_factory=list)
    recommended_tools: list[str] = Field(default_factory=list)
    restrictions: list[str] = Field(default_factory=list)
    related_items: list[str] = Field(default_factory=list)
    usage_hints: str = ""


class ProviderListRequest(BaseModel):
    provider: ProviderConfig
    path: str = "/"
    recursive: bool = False


class IndexScanRequest(BaseModel):
    provider: ProviderConfig
    path: str = "/"
    recursive: bool = True


class ProviderWriteRequest(BaseModel):
    provider: ProviderConfig
    path: str
    content: Optional[str] = None
    content_b64: Optional[str] = None
    create_parents: bool = True
    verify: bool = True


class ProviderMirrorRequest(BaseModel):
    provider: ProviderConfig
    remote_path: str
    local_path: str  # Only works if storage svc has access to local fs
    excludes: list[str] = Field(default_factory=list)
