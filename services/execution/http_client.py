# services/execution/http_client.py
"""
Shared aiohttp client with connection pooling for Nextcloud and other HTTP calls.
Provides:
- Connection pooling (reuses TCP connections)
- Session reuse (one session per host)
- Timeout configuration
- SSL verification control
- Proper cleanup
"""
import asyncio
import logging
from typing import Optional, Tuple, Union
from aiohttp import ClientSession, ClientTimeout, TCPConnector, BasicAuth

log = logging.getLogger("execution.http")

_DEFAULT_TIMEOUT = ClientTimeout(total=30, connect=5)
_NEXTCLOUD_TIMEOUT = ClientTimeout(total=60, connect=10)
_MAX_CONNECTIONS = 50
_MAX_CONNECTIONS_PER_HOST = 10

# Global session cache: {host: (session, created_at)}
_SESSION_CACHE: dict[str, Tuple[ClientSession, float]] = {}
_CACHE_MAX_AGE = 300  # 5 minutes


def _get_host(url: str) -> str:
    """Extract host from URL (e.g., 'https://nextcloud.example.com')"""
    from urllib.parse import urlparse
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.hostname}"


async def request(
    method: str,
    url: str,
    *,
    auth: Optional[Tuple[str, str]] = None,
    headers: Optional[dict] = None,
    params: Optional[dict] = None,
    data: Optional[Union[bytes, str, dict]] = None,
    json: Optional[dict] = None,
    timeout: Optional[Union[ClientTimeout, int]] = None,
    verify: bool = False,
) -> dict:
    """
    Make an HTTP request using a pooled connection.

    Returns:
        dict with keys: status_code, headers, text, content, ok
    """
    if timeout is None:
        if "nextcloud" in url or "remote.php" in url:
            timeout = _NEXTCLOUD_TIMEOUT
        else:
            timeout = _DEFAULT_TIMEOUT
    elif isinstance(timeout, int):
        timeout = ClientTimeout(total=timeout)

    host = _get_host(url)

    session = await get_session(host, verify)

    request_kwargs = {}
    if auth:
        request_kwargs["auth"] = BasicAuth(auth[0], auth[1])
    if headers:
        request_kwargs["headers"] = headers
    if params:
        request_kwargs["params"] = params
    if data is not None:
        request_kwargs["data"] = data
    if json is not None:
        request_kwargs["json"] = json

    async with session.request(method, url, timeout=timeout, **request_kwargs) as resp:
        response_text = await resp.text()
        response_content = await resp.read()
        return {
            "status_code": resp.status,
            "headers": dict(resp.headers),
            "text": response_text,
            "content": response_content,
            "ok": resp.status in (200, 201, 204),
        }


async def get_session(host: str, verify: bool = False) -> ClientSession:
    """Get or create a session for the given host with connection pooling."""
    now = asyncio.get_event_loop().time()
    cached = _SESSION_CACHE.get(host)

    if cached:
        session, created = cached
        if now - created < _CACHE_MAX_AGE:
            return session

    connector = TCPConnector(
        limit=_MAX_CONNECTIONS,
        limit_per_host=_MAX_CONNECTIONS_PER_HOST,
        enable_cleanup_closed=True,
    )
    if not verify:
        import ssl
        connector._ssl = ssl.create_default_context()  # type: ignore

    session = ClientSession(connector=connector)
    _SESSION_CACHE[host] = (session, now)
    return session


async def close_all_sessions():
    """Close all cached sessions."""
    for host in list(_SESSION_CACHE.keys()):
        session = _SESSION_CACHE.pop(host)[0]
        try:
            await session.close()
        except Exception:
            pass
