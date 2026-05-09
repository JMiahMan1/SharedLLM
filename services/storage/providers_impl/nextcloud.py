# services/storage/providers_impl/nextcloud.py
from typing import Any
try:
    from ..providers import StorageProvider
    from ..models import StorageEntry
    from ..nextcloud_client import NextCloudClient
except (ImportError, ValueError):
    import sys
    import os
    sys.path.append(os.path.join(os.path.dirname(__file__), ".."))
    from providers import StorageProvider
    from models import StorageEntry
    from nextcloud_client import NextCloudClient

class NextcloudStorageProvider(StorageProvider):
    def __init__(self, settings: dict):
        self.client = NextCloudClient(
            settings["url"],
            settings["username"],
            settings["password"],
        )

    def list_entries(self, path: str = "/", recursive: bool = False) -> list[StorageEntry]:
        return self.client.list_entries(path=path, recursive=recursive)

    def get_content(self, path: str) -> str | None:
        return self.client.get_file_content(path)

    def write_content(
        self,
        path: str,
        content: str | bytes,
        create_parents: bool = True,
        verify: bool = True,
        is_binary: bool = False,
    ) -> dict[str, Any]:
        return self.client.write_file_content(
            path, content, create_parents=create_parents, verify=verify, is_binary=is_binary
        )

    def upload_directory(self, remote_path: str, local_path: str) -> dict[str, Any]:
        return self.client.upload_directory(remote_path, local_path)
