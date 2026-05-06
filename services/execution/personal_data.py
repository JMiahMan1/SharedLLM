from dataclasses import dataclass
from typing import Any, Optional, Protocol

import caldav
import requests

try:
    from .nextcloud_client import ensure_webdav_dir, ocs_request, resolve_credentials, safe_filename, webdav_url
except ImportError:
    from nextcloud_client import ensure_webdav_dir, ocs_request, resolve_credentials, safe_filename, webdav_url


class PersonalDataProvider(Protocol):
    kind: str
    base_url: str
    username: str
    password: str

    def calendar_client(self) -> caldav.DAVClient: ...
    def ensure_directory(self, path: str) -> None: ...
    def file_url(self, path: str) -> str: ...
    def upload_file(self, path: str, data: bytes, content_type: str) -> requests.Response: ...
    def request(
        self,
        method: str,
        endpoint: str,
        *,
        params: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
        timeout: int = 30,
    ) -> tuple[bool, Any, str]: ...
    def sanitize_filename(self, value: str, fallback: str) -> str: ...


@dataclass
class NextcloudPersonalDataProvider:
    base_url: str
    username: str
    password: str
    kind: str = "nextcloud"

    def calendar_client(self) -> caldav.DAVClient:
        return caldav.DAVClient(
            url=f"{self.base_url.rstrip('/')}/remote.php/dav",
            username=self.username,
            password=self.password,
            timeout=60,
        )

    def ensure_directory(self, path: str) -> None:
        ensure_webdav_dir(self.base_url, self.username, self.password, path)

    def file_url(self, path: str) -> str:
        return webdav_url(self.base_url, self.username, path)

    def upload_file(self, path: str, data: bytes, content_type: str) -> requests.Response:
        return requests.put(
            self.file_url(path),
            data=data,
            auth=(self.username, self.password),
            headers={"Content-Type": content_type},
            timeout=60,
            verify=False,
        )

    def request(
        self,
        method: str,
        endpoint: str,
        *,
        params: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
        timeout: int = 30,
    ) -> tuple[bool, Any, str]:
        return ocs_request(
            method,
            self.base_url,
            self.username,
            self.password,
            endpoint,
            params=params,
            data=data,
            timeout=timeout,
        )

    def sanitize_filename(self, value: str, fallback: str) -> str:
        return safe_filename(value, fallback)


def resolve_personal_data_provider(user_context: Any) -> Optional[PersonalDataProvider]:
    base_url, username, password = resolve_credentials(user_context)
    if not (base_url and username and password):
        return None
    return NextcloudPersonalDataProvider(base_url=base_url, username=username, password=password)
