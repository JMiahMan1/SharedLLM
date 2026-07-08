# services/storage/providers_impl/nextcloud.py
from typing import Any

from services.storage.models import StorageEntry
from services.storage.nextcloud_client import NextCloudClient
from services.storage.providers import StorageProvider


class NextcloudStorageProvider(StorageProvider):
    def __init__(self, settings: dict):
        self.client = NextCloudClient(
            settings["url"],
            settings["username"],
            settings["password"],
        )

    async def list_entries(self, path: str = "/", recursive: bool = False) -> list[StorageEntry]:
        return await self.client.list_entries(path=path, recursive=recursive)

    async def get_content(self, path: str) -> str | None:
        return await self.client.get_file_content(path)

    async def write_content(
        self,
        path: str,
        content: str | bytes,
        create_parents: bool = True,
        verify: bool = True,
        is_binary: bool = False,
    ) -> dict[str, Any]:
        return await self.client.write_file_content(
            path, content, create_parents=create_parents, verify=verify, is_binary=is_binary
        )

    async def upload_directory(self, remote_path: str, local_path: str, excludes: list[str] | None = None) -> dict[str, Any]:
        return await self.client.upload_directory(remote_path, local_path, excludes=excludes)
