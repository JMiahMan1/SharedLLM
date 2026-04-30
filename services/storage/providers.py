from __future__ import annotations

from abc import ABC, abstractmethod

try:
    from .models import ProviderConfig, StorageEntry
    from .nextcloud_client import NextCloudClient
except ImportError:
    from models import ProviderConfig, StorageEntry
    from nextcloud_client import NextCloudClient


class StorageProvider(ABC):
    @abstractmethod
    def list_entries(self, path: str = "/", recursive: bool = False) -> list[StorageEntry]:
        raise NotImplementedError


class NextcloudStorageProvider(StorageProvider):
    def __init__(self, settings: dict):
        self.client = NextCloudClient(
            settings["url"],
            settings["username"],
            settings["password"],
        )

    def list_entries(self, path: str = "/", recursive: bool = False) -> list[StorageEntry]:
        return self.client.list_entries(path=path, recursive=recursive)


def build_provider(config: ProviderConfig) -> StorageProvider:
    if config.kind == "nextcloud":
        return NextcloudStorageProvider(config.settings)
    raise ValueError(f"Unsupported storage provider: {config.kind}")
