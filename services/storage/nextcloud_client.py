# services/storage/nextcloud_client.py
import logging
import aiohttp
import xml.etree.ElementTree as ET
from pathlib import PurePosixPath
from urllib.parse import quote, urlparse
from typing import Optional, List, Dict, Any

try:
    from .models import StorageEntry
except ImportError:
    from models import StorageEntry

log = logging.getLogger("storage.nextcloud")

class NextCloudClient:
    def __init__(self, url: str, username: str, password: str):
        parsed = urlparse(url)
        self.host = parsed.netloc
        self.protocol = parsed.scheme
        self.username = username
        self.password = password
        
        # The base DAV path for this user
        self.dav_prefix = f"/remote.php/dav/files/{username}"
        # If the URL has a path, it might be a subfolder within NextCloud
        # But NextCloud DAV usually starts at /remote.php/dav/files/user/
        # We'll normalize the base path
        self.base_path = parsed.path.rstrip('/')
        if not self.base_path.startswith(self.dav_prefix):
            # If the user provided just the server URL, we append the DAV prefix
            self.base_path = self.dav_prefix + self.base_path
            
        self.base_url = f"{self.protocol}://{self.host}"
        
        self.client = aiohttp.ClientSession(
            auth=aiohttp.BasicAuth(self.username, self.password),
            timeout=aiohttp.ClientTimeout(total=15.0, sock_read=60.0),
            headers={"User-Agent": "JarvisOS-Storage/1.0"}
        )

    async def _full_url(self, remote_path: str) -> str:
        clean_path = "/" + str(remote_path).lstrip("/")
        # If the path already includes the base_path, don't double it
        if clean_path.startswith(self.base_path):
            relative_path = clean_path[len(self.base_path):]
        else:
            relative_path = clean_path
            
        quoted_path = quote(relative_path.lstrip("/"), safe="/")
        url = f"{self.base_url}{self.base_path}/{quoted_path}"
        log.info(f"[Nextcloud] Full URL construction: host={self.host}, base={self.base_path}, relative={relative_path} -> {url}")
        # Ensure trailing slash if the original remote_path had it and it's not already there
        if remote_path.endswith("/") and not url.endswith("/"):
            url += "/"
        # But if it's the root of the dav path, we usually want the slash
        if url == f"{self.base_url}{self.base_path}" or url == f"{self.base_url}{self.base_path}/":
            return f"{self.base_url}{self.base_path}/"
        return url

    def _parse_dav_response(self, xml_content: bytes) -> List[Dict[str, Any]]:
        """Parse WebDAV PROPFIND XML response."""
        items = []
        try:
            root = ET.fromstring(xml_content)
            ns = {"d": "DAV:"}
            
            for response in root.findall(".//d:response", ns):
                href_node = response.find("d:href", ns)
                if href_node is None or href_node.text is None:
                    continue
                href = href_node.text
                propstats = response.findall("d:propstat", ns)
                
                # We usually want the one with 200 OK
                props = {}
                for propstat in propstats:
                    status_node = propstat.find("d:status", ns)
                    if status_node is None or status_node.text is None:
                        continue
                    status = status_node.text
                    if "200" in status:
                        prop_node = propstat.find("d:prop", ns)
                        if prop_node is not None:
                            # Extract properties
                            props["mtime"] = getattr(prop_node.find("d:getlastmodified", ns), "text", None)
                            props["size"] = getattr(prop_node.find("d:getcontentlength", ns), "text", None)
                            props["contenttype"] = getattr(prop_node.find("d:getcontenttype", ns), "text", None)
                            
                            resourcetype = prop_node.find("d:resourcetype", ns)
                            if resourcetype is not None and resourcetype.find("d:collection", ns) is not None:
                                props["is_dir"] = True
                            else:
                                props["is_dir"] = False
                
                items.append({
                    "href": href,
                    "props": props
                })
        except Exception as e:
            log.error(f"Failed to parse WebDAV XML: {e}")
            
        return items

    async def list_files(self, remote_path: str = "/") -> List[Dict[str, Any]]:
        """List files using PROPFIND."""
        url = await self._full_url(remote_path)
        log.info(f"DAV PROPFIND: {url}")
        
        headers = {"Depth": "1"}
        try:
            resp = await self.client.request("PROPFIND", url, headers=headers)
            if resp.status >= 400:
                raise Exception(f"HTTP {resp.status}")
            xml_content = await resp.read()
            return self._parse_dav_response(xml_content)
        except Exception as e:
            log.error(f"Failed to list files in {remote_path}: {e}")
            return []

    async def list_entries(self, path: str = "/", recursive: bool = False) -> List[StorageEntry]:
        entries: List[StorageEntry] = []
        seen_paths = set()

        async def _walk(current_path: str):
            target = "/" + current_path.strip("/")
            items = await self.list_files(target)
            
            # The first item in PROPFIND Depth 1 is usually the directory itself
            # We need to find the base href to calculate relative paths correctly
            base_href = None
            if items:
                # Heuristic: the shortest href or the one matching the target is the base
                base_href = items[0]["href"]

            for item in items:
                href = item["href"]
                props = item["props"]
                
                # Normalize path: remove base_path and trailing slash
                from urllib.parse import unquote
                clean_path = unquote(href)
                if clean_path.startswith(self.base_path):
                    clean_path = clean_path[len(self.base_path):]
                
                norm_path = "/" + clean_path.strip("/")
                
                # Skip the current directory itself
                if norm_path == unquote(target) or not clean_path or (base_href and href == base_href):
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
                
                is_dir = props.get("is_dir", False)
                
                entry = StorageEntry(
                    path=norm_path,
                    name=norm_path.split("/")[-1],
                    is_dir=is_dir,
                    size=int(props["size"]) if props.get("size") else None,
                    mtime=props.get("mtime"),
                    content_type=props.get("contenttype"),
                )
                
                entries.append(entry)
                seen_paths.add(norm_path)
                
                if recursive and is_dir:
                    await _walk(norm_path)

        await _walk(path)
        return entries

    async def get_file_content(self, remote_path: str) -> Optional[str]:
        """Fetch content of a text file."""
        url = await self._full_url(remote_path)
        log.info(f"NextCloud GET: {url}")
        
        try:
            resp = await self.client.get(url)
            if resp.status >= 400:
                raise Exception(f"HTTP {resp.status}")
            return await resp.text()
        except Exception as e:
            log.error(f"Failed to fetch content for {remote_path}: {e}")
            return None

    async def ensure_directory(self, remote_path: str) -> None:
        """Ensure a directory exists using MKCOL."""
        normalized = "/" + str(remote_path).strip("/")
        if normalized == "/":
            return

        current = PurePosixPath("/")
        for part in PurePosixPath(normalized).parts[1:]:
            current = current / part
            url = await self._full_url(str(current))
            try:
                # WebDAV requires MKCOL to create a directory
                resp = await self.client.request("MKCOL", url)
                # 405 Method Not Allowed often means the directory already exists
                if resp.status not in {200, 201, 204, 301, 302, 405}:
                    log.warning(f"MKCOL {current} returned status {resp.status}")
            except Exception as e:
                log.error(f"Failed to ensure directory {current}: {e}")
                raise

    async def write_file_content(
        self,
        remote_path: str,
        content: str | bytes,
        create_parents: bool = True,
        verify: bool = True,
        is_binary: bool = False,
    ) -> Dict[str, Any]:
        normalized = "/" + str(remote_path).strip("/")
        if create_parents:
            parent = str(PurePosixPath(normalized).parent)
            await self.ensure_directory(parent)

        if not isinstance(content, bytes):
            content_bytes = content.encode("utf-8")
        else:
            content_bytes = content

        url = await self._full_url(normalized)
        try:
            headers = {"Content-Type": "application/octet-stream" if is_binary else "text/plain; charset=utf-8"}
            resp = await self.client.put(url, data=content_bytes, headers=headers)
            resp.raise_for_status()
        except Exception as e:
            log.error(f"Failed to upload content for {normalized}: {e}")
            raise

        return {
            "path": normalized,
            "bytes_written": len(content_bytes),
            "verified": True if verify else None, # Simplified
        }

    async def download_file(self, remote_path: str, local_path: str) -> bool:
        """Download a file to a local path."""
        url = await self._full_url(remote_path)
        try:
            with open(local_path, "wb") as f:
                async with self.client.get(url) as resp:
                    resp.raise_for_status()
                    async for chunk in resp.content.iter_chunked(8192):
                        f.write(chunk)
            return True
        except Exception as e:
            log.error(f"Failed to download {remote_path}: {e}")
            return False

    async def upload_directory(self, remote_path: str, local_path: str, excludes: Optional[List[str]] = None) -> Dict[str, Any]:
        """Recursively upload a local directory to a remote Nextcloud path."""
        import os
        from pathlib import Path

        # Default excludes if none provided
        if excludes is None:
            excludes = [
                "node_modules", ".venv", "venv", ".git", "__pycache__", ".pytest_cache", 
                ".cache", ".local", ".vscode", ".idea", "dist", "build", ".tox", ".nox"
            ]
        
        exclude_set = set(excludes)
        
        local_root = Path(local_path).resolve()
        if not local_root.is_dir():
            raise ValueError(f"Local path {local_path} is not a directory or does not exist.")
            
        remote_root = "/" + str(remote_path).strip("/")
        log.info(f"Uploading directory {local_root} to {remote_root}")
        
        uploaded_files = 0
        total_bytes = 0
        
        for root, dirs, files in os.walk(local_root):
            # Skip noise directories
            dirs[:] = [d for d in dirs if d not in exclude_set]
            
            rel_path = Path(root).relative_to(local_root)
            remote_dir = str(PurePosixPath(remote_root) / rel_path)
            
            # Ensure the directory exists on Nextcloud
            await self.ensure_directory(remote_dir)
            
            for file in files:
                local_file = Path(root) / file
                remote_file = str(PurePosixPath(remote_dir) / file)
                
                try:
                    with open(local_file, "rb") as f:
                        content = f.read()
                        await self.write_file_content(remote_file, content, create_parents=False, is_binary=True)
                        uploaded_files += 1
                        total_bytes += len(content)
                except Exception as e:
                    log.error(f"Failed to upload {local_file} to {remote_file}: {e}")
                    
        return {
            "status": "SUCCESS",
            "remote_root": remote_root,
            "uploaded_files": uploaded_files,
            "total_bytes": total_bytes
        }

    async def close(self):
        await self.client.close()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()
