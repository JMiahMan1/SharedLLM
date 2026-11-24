# services/storage/providers_impl/nextcloud.py
from typing import Any

from services.storage.models import ContentSection, StorageEntry
from services.storage.nextcloud_client import NextCloudClient
from services.storage.pdf_index import default_max_chars, is_pdf_path, pdf_index_sections
from services.storage.providers import StorageProvider

#: Seconds to download one PDF for indexing; a book-sized PDF is many megabytes.
_PDF_DOWNLOAD_TIMEOUT_S = 120.0


class NextcloudStorageProvider(StorageProvider):
    def __init__(self, settings: dict):
        self.client = NextCloudClient(
            settings["url"],
            settings["username"],
            settings["password"],
        )
        self.pdf_max_chars = int(settings.get("pdf_max_chars") or default_max_chars())

    async def list_entries(self, path: str = "/", recursive: bool = False) -> list[StorageEntry]:
        return await self.client.list_entries(path=path, recursive=recursive)

    async def get_sections(self, path: str) -> list[ContentSection] | None:
        """A PDF's chapters (from its bookmarks) or pages; ``None`` for anything else.

        Reading a PDF as text (``get_file_content``) decodes its compressed
        streams into mojibake, so a PDF is downloaded as bytes and its text layer
        read instead. That is what puts Nextcloud PDFs into ``nextcloud_files``
        with their words, the same way a Calibre PDF book is indexed.
        """
        if not is_pdf_path(path):
            return None
        data = await self.client.get_file_bytes(path, timeout=_PDF_DOWNLOAD_TIMEOUT_S)
        if not data:
            return None
        return await pdf_index_sections(data, max_chars=self.pdf_max_chars, what=path)

    async def get_content(self, path: str) -> str | None:
        if is_pdf_path(path):
            sections = await self.get_sections(path)
            if not sections:
                return None
            return "\n\n".join(section.text for section in sections)
        return await self.client.get_file_content(path)

    async def get_bytes(self, path: str) -> bytes | None:
        return await self.client.get_file_bytes(path, timeout=60.0)

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
