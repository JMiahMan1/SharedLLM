# services/storage/providers.py
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

try:
    from .models import ContentSection, ProviderConfig, StorageEntry
except ImportError:
    from models import ContentSection, ProviderConfig, StorageEntry

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

    async def get_sections(self, path: str) -> list[ContentSection] | None:
        """Return the document's internal structure, or ``None`` if it has none.

        ``get_content`` flattens a document to one string, and a flat string can
        only be cut into anonymous character windows. A provider that can see
        the real structure -- an EPUB spine, a Calibre book divided into days or
        chapters -- overrides this so the indexer can emit chunks that carry a
        citable label instead of ``chunk 412``.

        Returning ``None`` is a legitimate answer, not a failure: a plain
        ``.txt`` file genuinely has no sections, and the indexer falls back to
        ``get_content``.
        """
        return None

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

    if config.kind == "calibre":
        # The library is a folder inside Nextcloud, so the same credentials
        # and the same WebDAV client apply. The only genuinely new setting is
        # which folder holds the library.
        settings = _resolve_nextcloud_settings(config.settings)
        library_path = settings.get("library_path")
        if not library_path:
            raise ValueError(
                "Calibre provider requires a 'library_path' setting: the Nextcloud "
                "folder containing metadata.db (for example '/Books/Text'). There is "
                "no default, because guessing it would index the wrong shelf."
            )
        try:
            from .providers_impl.calibre import CalibreStorageProvider
        except ImportError:
            from providers_impl.calibre import CalibreStorageProvider
        return CalibreStorageProvider(settings)

    # Example for future local provider:
    # if config.kind == "local":
    #     from .providers_impl.local import LocalStorageProvider
    #     return LocalStorageProvider(config.settings)

    raise ValueError(f"Unsupported storage provider: {config.kind}")
