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
        # The base DAV path for this user
        self.dav_path = f"/remote.php/dav/files/{username}/"
        self.path = parsed.path.rstrip('/') + self.dav_path
        
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
            items = self.client.ls(remote_path)
            log.info(f"DAV ls({remote_path}) returned {len(items)} items")
            return items
        except Exception as e:
            log.error(f"Failed to list files in {remote_path}: {e}")
            return []

    def list_entries(self, path: str = "/", recursive: bool = False) -> list[StorageEntry]:
        entries: list[StorageEntry] = []
        seen_paths = set()

        def _walk(current_path: str):
            target = "/" + current_path.strip("/")
            for item in self.list_files(target):
                # Normalize path: easywebdav item.name is often the full DAV path
                raw_path = str(getattr(item, "name", ""))
                
                clean_path = raw_path
                if clean_path.startswith(self.dav_path):
                    clean_path = clean_path[len(self.dav_path):]
                
                norm_path = "/" + clean_path.strip("/")
                
                # Skip current directory or empty names
                if not clean_path or norm_path == target:
                    continue
                
                if norm_path in seen_paths:
                    continue
                
                # Proactive skip of noise directories
                if any(skip in norm_path.split("/") for skip in [
                    "node_modules", ".venv", "venv", ".git", "__pycache__", ".pytest_cache", 
                    ".cache", ".local", ".vscode", ".idea", "dist", "build", ".tox", ".nox",
                    "site-packages", "bin", "include", "lib", "lib64"
                ]):
                    continue
                
                is_dir = self._is_directory(item)
                
                entry = StorageEntry(
                    path=norm_path,
                    name=self._basename(norm_path),
                    is_dir=is_dir,
                    size=getattr(item, "size", None),
                    mtime=str(getattr(item, "mtime", "")) or None,
                    content_type=getattr(item, "contenttype", None),
                )
                
                entries.append(entry)
                seen_paths.add(norm_path)
                
                if recursive and is_dir:
                    _walk(norm_path)

        _walk(path)
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
        # Ensure we use a clean path relative to self.path
        clean_path = remote_path
        if clean_path.startswith(self.dav_path):
            clean_path = clean_path[len(self.dav_path):]
            
        full_url = f"{self.protocol}://{self.host}{self.path}{clean_path.lstrip('/')}"
        log.info(f"NextCloud GET: {full_url}")
        
        try:
            resp = requests.get(
                full_url, 
                auth=(self.username, self.password),
                timeout=15.0
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
        content_type = str(getattr(item, "contenttype", "") or "").lower()
        if "directory" in content_type or content_type == "httpd/unix-directory":
            return True
        name = str(getattr(item, "name", ""))
        if name.endswith("/"):
            return True
        return False
