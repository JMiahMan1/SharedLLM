# services/storage/nextcloud_client.py
import easywebdav
import logging
import requests
from urllib.parse import urlparse

try:
    from .models import StorageEntry
except ImportError:
    from models import StorageEntry

log = logging.getLogger("storage.nextcloud")

class NextCloudClient:
    def __init__(self, url, username, password):
        parsed = urlparse(url)
        self.host = parsed.netloc
        self.protocol = parsed.scheme
        self.path = parsed.path.rstrip('/') + '/remote.php/dav/files/' + username + '/'
        
        self.username = username
        self.password = password
        
        self.client = easywebdav.connect(
            self.host,
            protocol=self.protocol,
            username=username,
            password=password,
            path=self.path
        )

    def list_files(self, remote_path='/'):
        """List files in a directory."""
        try:
            return self.client.ls(remote_path)
        except Exception as e:
            log.error(f"Failed to list files in {remote_path}: {e}")
            return []

    def list_entries(self, path: str = "/", recursive: bool = False) -> list[StorageEntry]:
        entries: list[StorageEntry] = []

        def _walk(current_path: str):
            for item in self.list_files(current_path):
                normalized_path = self._normalize_remote_path(getattr(item, "name", current_path))
                is_dir = self._is_directory(item)
                entries.append(
                    StorageEntry(
                        path=normalized_path,
                        name=self._basename(normalized_path),
                        is_dir=is_dir,
                        size=getattr(item, "size", None),
                        mtime=str(getattr(item, "mtime", "")) or None,
                        content_type=getattr(item, "contenttype", None),
                    )
                )
                if recursive and is_dir and normalized_path != current_path:
                    _walk(normalized_path)

        _walk(self._normalize_remote_path(path))
        return entries

    def download_file(self, remote_path, local_path):
        """Download a file from NextCloud."""
        try:
            self.client.download(remote_path, local_path)
            return True
        except Exception as e:
            log.error(f"Failed to download {remote_path}: {e}")
            return False

    def get_file_content(self, remote_path: str) -> str | None:
        """Fetch content of a text file directly via HTTP."""
        full_url = f"{self.protocol}://{self.host}{self.path}{remote_path.lstrip('/')}"
        try:
            resp = requests.get(
                full_url, 
                auth=(self.username, self.password),
                timeout=10.0
            )
            resp.raise_for_status()
            return resp.text
        except Exception as e:
            log.error(f"Failed to fetch content for {remote_path}: {e}")
            return None

    @staticmethod
    def _basename(path: str) -> str:
        normalized = path.rstrip("/")
        return normalized.split("/")[-1] if normalized else "/"

    @staticmethod
    def _normalize_remote_path(path: str) -> str:
        normalized = "/" + str(path).strip("/")
        return normalized or "/"

    @staticmethod
    def _is_directory(item) -> bool:
        content_type = str(getattr(item, "contenttype", "") or "")
        return content_type == "httpd/unix-directory" or content_type.endswith("directory")
