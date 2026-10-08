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
    async def download_file(self, path: str) -> tuple[int, str, bytes]: ...
    async def preview(self, file_id: int, size: int) -> tuple[int, str, bytes]: ...
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

    async def download_file(self, path: str) -> tuple[int, str, bytes]:
        from .http_client import fetch_bytes
        return await fetch_bytes(self.file_url(path), auth=(self.username, self.password), timeout=120, verify=False)

    async def preview(self, file_id: int, size: int) -> tuple[int, str, bytes]:
        """Nextcloud's own thumbnail of a file, ``size`` px on the long side."""
        from .http_client import fetch_bytes
        return await fetch_bytes(
            f"{self.base_url.rstrip('/')}/index.php/core/preview",
            auth=(self.username, self.password),
            params={"fileId": str(file_id), "x": str(size), "y": str(size), "a": "1", "mode": "cover"},
            verify=False,
        )

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


def _context_value(user_context: Any, key: str) -> Any:
    if isinstance(user_context, dict):
        return user_context.get(key)
    return getattr(user_context, key, None)


def resolve_personal_data_provider(user_context: Any) -> PersonalDataProvider | None:
    """The Nextcloud account a user's Talk, notes and calendar act through.

    When the context names a user, only that user's own login counts: Identity
    already resolved it to their own account, or to the shared one when an admin
    granted that. Filling the gaps from the server's NEXTCLOUD_USER (the Admin
    account) made every action of a user without a login -- their chat
    messages, their notes -- happen as Admin. Only the server URL, which is the
    same for everyone, may still come from the environment. A context with no
    user (a system job) keeps the environment account.
    """
    base_url, username, password = resolve_credentials(user_context)
    if _context_value(user_context, "user"):
        username = _context_value(user_context, "nextcloud_user")
        password = _context_value(user_context, "nextcloud_pass")
    if not (base_url and username and password):
        return None
    return NextcloudPersonalDataProvider(base_url=base_url, username=username, password=password)


def missing_account_message(user_context: Any) -> str:
    """What to tell a user whose Nextcloud account is not set up."""
    user = _context_value(user_context, "user") or "this account"
    return (
        f"No Nextcloud account is set up for {user}. Add one under Settings > "
        "Integrations, or ask an admin to share the household account with you."
    )
