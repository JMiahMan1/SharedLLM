# services/storage/providers.py
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

try:
    from .models import ProviderConfig, StorageEntry
except ImportError:
    from models import ProviderConfig, StorageEntry

class StorageProvider(ABC):
    @abstractmethod
    async def list_entries(self, path: str = "/", recursive: bool = False) -> list[StorageEntry]:
        raise NotImplementedError

    @abstractmethod
    async def get_content(self, path: str) -> str | None:
        raise NotImplementedError

    @abstractmethod
    async def write_content(
        self, path: str, content: str | bytes, create_parents: bool = True, verify: bool = True, is_binary: bool = False
    ) -> dict[str, Any]:
        raise NotImplementedError

def _resolve_nextcloud_settings(settings: dict[str, Any]) -> dict[str, Any]:
    """Merge request settings with defaults from config.py."""
    merged = dict(settings)
    from services.config import NEXTCLOUD_PASS, NEXTCLOUD_URL, NEXTCLOUD_USER
    if "url" not in merged:
        merged["url"] = NEXTCLOUD_URL
    if "username" not in merged:
        merged["username"] = NEXTCLOUD_USER
    if "password" not in merged:
        merged["password"] = NEXTCLOUD_PASS
    return merged

def build_provider(config: ProviderConfig) -> StorageProvider:
    """
    Factory function to build storage providers.
    Moving specifics to plugins ensures backend agnosticism.
    """
    if config.kind == "nextcloud":
        settings = _resolve_nextcloud_settings(config.settings)
        try:
            from .providers_impl.nextcloud import NextcloudStorageProvider
        except ImportError:
            from providers_impl.nextcloud import NextcloudStorageProvider
        return NextcloudStorageProvider(settings)

    # Example for future local provider:
    # if config.kind == "local":
    #     from .providers_impl.local import LocalStorageProvider
    #     return LocalStorageProvider(config.settings)

    raise ValueError(f"Unsupported storage provider: {config.kind}")
