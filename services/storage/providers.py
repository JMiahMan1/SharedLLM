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

def build_provider(config: ProviderConfig) -> StorageProvider:
    """
    Factory function to build storage providers.
    Moving specifics to plugins ensures backend agnosticism.
    """
    if config.kind == "nextcloud":
        try:
            from .providers_impl.nextcloud import NextcloudStorageProvider
        except ImportError:
            from providers_impl.nextcloud import NextcloudStorageProvider
        return NextcloudStorageProvider(config.settings)
    
    # Example for future local provider:
    # if config.kind == "local":
    #     from .providers_impl.local import LocalStorageProvider
    #     return LocalStorageProvider(config.settings)
        
    raise ValueError(f"Unsupported storage provider: {config.kind}")
