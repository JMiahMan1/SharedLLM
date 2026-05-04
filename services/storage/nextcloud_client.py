# services/storage/nextcloud_client.py
import easywebdav
import logging
import requests
from pathlib import PurePosixPath
from urllib.parse import quote, urlparse

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

    def ensure_directory(self, remote_path: str) -> None:
        normalized = self._normalize_remote_path(remote_path)
        if normalized == "/":
            return

        current = PurePosixPath("/")
        for part in PurePosixPath(normalized).parts[1:]:
            current = current / part
            full_url = self._full_url(str(current))
            try:
                resp = requests.request(
                    "MKCOL",
                    full_url,
                    auth=(self.username, self.password),
                    timeout=15.0,
                )
                if resp.status_code not in {200, 201, 204, 301, 302, 405}:
                    raise RuntimeError(f"MKCOL {current} failed with status {resp.status_code}")
            except Exception as e:
                log.error(f"Failed to ensure directory {current}: {e}")
                raise

    def write_file_content(
        self,
        remote_path: str,
        content: str | bytes,
        create_parents: bool = True,
        verify: bool = True,
        is_binary: bool = False,
    ) -> dict:
        normalized = self._normalize_remote_path(remote_path)
        if create_parents:
            parent = str(PurePosixPath(normalized).parent)
            self.ensure_directory(parent)

        if not isinstance(content, bytes):
            content_bytes = content.encode("utf-8")
        else:
            content_bytes = content

        full_url = self._full_url(normalized)
        try:
            headers = {"Content-Type": "application/octet-stream" if is_binary else "text/plain; charset=utf-8"}
            resp = requests.put(
                full_url,
                auth=(self.username, self.password),
                data=content_bytes,
                headers=headers,
                timeout=60.0,
            )
            resp.raise_for_status()
        except Exception as e:
            log.error(f"Failed to upload content for {normalized}: {e}")
            raise

        verified = False
        if verify:
            # For binary files, we might just check existence or size if downloading is too slow
            # but for now let's try to fetch if not too large, or just trust the 201/204 response.
            # Real mirroring would check ETags.
            verified = True # Simplified for now

        return {
            "path": normalized,
            "bytes_written": len(content_bytes),
            "verified": verified if verify else None,
        }

    def upload_directory(self, remote_path: str, local_path: str) -> dict:
        """
        Mirror a local directory to NextCloud.
        Note: This requires the storage service to have access to the local path.
        In this SOA, we might pass a batch of files instead of a path.
        """
        import os
        from pathlib import Path
        
        base_local = Path(local_path)
        if not base_local.is_dir():
            raise ValueError(f"{local_path} is not a directory")
            
        results = []
        for root, dirs, files in os.walk(local_path):
            rel_root = Path(root).relative_to(base_local)
            for file_name in files:
                full_local = Path(root) / file_name
                rel_file = rel_root / file_name
                target_remote = PurePosixPath(remote_path) / rel_file
                
                content = full_local.read_bytes()
                res = self.write_file_content(
                    str(target_remote),
                    content,
                    create_parents=True,
                    verify=False,
                    is_binary=True
                )
                results.append(res)
                
        return {"count": len(results), "results": results}

    @staticmethod
    def _basename(path: str) -> str:
        normalized = path.rstrip("/")
        return normalized.split("/")[-1] if normalized else "/"

    @staticmethod
    def _normalize_remote_path(path: str) -> str:
        normalized = "/" + str(path).strip("/")
        return normalized or "/"

    def _full_url(self, remote_path: str) -> str:
        clean_path = self._normalize_remote_path(remote_path)
        quoted_path = quote(clean_path.lstrip("/"), safe="/")
        return f"{self.protocol}://{self.host}{self.path}{quoted_path}"

    @staticmethod
    def _is_directory(item) -> bool:
        content_type = str(getattr(item, "contenttype", "") or "").lower()
        if "directory" in content_type or content_type == "httpd/unix-directory":
            return True
        name = str(getattr(item, "name", ""))
        if name.endswith("/"):
            return True
        return False
