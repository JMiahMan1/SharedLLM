from dataclasses import dataclass
from typing import Any, Protocol

import caldav

try:
    from nextcloud_client import ensure_webdav_dir, ocs_request, resolve_credentials, safe_filename, webdav_url
except ImportError:
    from .nextcloud_client import ensure_webdav_dir, ocs_request, resolve_credentials, safe_filename, webdav_url


class PersonalDataProvider(Protocol):
    kind: str
    base_url: str
    username: str
    password: str

    def calendar_client(self) -> caldav.DAVClient: ...
    async def ensure_directory(self, path: str) -> None: ...
    def file_url(self, path: str) -> str: ...
    async def upload_file(self, path: str, data: bytes, content_type: str) -> bool: ...
    async def request(
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
        client = caldav.DAVClient(
            url=f"{self.base_url.rstrip('/')}/remote.php/dav",
            username=self.username,
            password=self.password,
            timeout=60,
        )
        # Disable HTTP/3 (Alt-Svc) negotiation. Nextcloud advertises h3 the
        # client cannot use, so niquests pays a slow MustDowngradeError retry on
        # every request -- across many calendars this blows past the UI's 15s
        # timeout and aborts with ECONNABORTED. Auth is applied per-request
        # by caldav (self.auth), so swapping the session is safe.
        try:
            import niquests

            client.session = niquests.Session(disable_http3=True, multiplexed=False)
        except Exception:
            pass
        return client

    async def ensure_directory(self, path: str) -> None:
        await ensure_webdav_dir(self.base_url, self.username, self.password, path)

    def file_url(self, path: str) -> str:
        return webdav_url(self.base_url, self.username, path)

    async def upload_file(self, path: str, data: bytes, content_type: str) -> bool:
        from .http_client import request
        resp = await request(
            "PUT",
            self.file_url(path),
            data=data,
            auth=(self.username, self.password),
            headers={"Content-Type": content_type},
            timeout=60,
            verify=False,
        )
        return resp["ok"]

    async def request(
        self,
        method: str,
        endpoint: str,
        *,
        params: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
        timeout: int = 30,
    ) -> tuple[bool, Any, str]:
        return await ocs_request(
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


def resolve_personal_data_provider(user_context: Any) -> PersonalDataProvider | None:
    base_url, username, password = resolve_credentials(user_context)
    if not (base_url and username and password):
        return None
    return NextcloudPersonalDataProvider(base_url=base_url, username=username, password=password)
