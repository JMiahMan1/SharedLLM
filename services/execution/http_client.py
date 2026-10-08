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
import contextlib
import logging
import os

from aiohttp import BasicAuth, ClientSession, ClientTimeout, TCPConnector

log = logging.getLogger("execution.http")

_DEFAULT_TIMEOUT = ClientTimeout(total=30, connect=5)
_NEXTCLOUD_TIMEOUT = ClientTimeout(total=60, connect=10)
_MAX_CONNECTIONS = 50
_MAX_CONNECTIONS_PER_HOST = 10
# Global session cache: {(host, effective_verify): (session, created_at)}
_SESSION_CACHE: dict[tuple[str, bool], tuple[ClientSession, float]] = {}
_CACHE_MAX_AGE = 300  # 5 minutes
_DNS_TTL = 60  # re-resolve DNS at most every 60s so pooled connectors don't go stale


def host_of(url: str) -> str:
    """Extract host from URL (e.g., 'https://nextcloud.example.com')"""
    from urllib.parse import urlparse

    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.hostname}"


def _insecure_tls_allowed() -> bool:
    """True only when the operator explicitly opted out via env (BUG-09)."""
    return (os.getenv("MEDIA_ALLOW_INSECURE_TLS", "false") or "false").strip().lower() in ("1", "true", "yes")


async def request(
    method: str,
    url: str,
    *,
    auth: tuple[str, str] | None = None,
    headers: dict | None = None,
    params: dict | None = None,
    data: bytes | str | dict | None = None,
    json: dict | None = None,
    timeout: ClientTimeout | int | None = None,
    verify: bool = True,
) -> dict:
    """
    Make an HTTP request using a pooled connection.

    Returns:
        dict with keys: status_code, headers, text, content, ok
    """
    if timeout is None:
        timeout = _NEXTCLOUD_TIMEOUT if "nextcloud" in url or "remote.php" in url else _DEFAULT_TIMEOUT
    elif isinstance(timeout, int):
        timeout = ClientTimeout(total=timeout)

    host = host_of(url)

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
        return {
            "status_code": resp.status,
            "headers": dict(resp.headers),
            "text": response_text,
            "content": response_text.encode("utf-8"),
            "ok": resp.status in (200, 201, 204),
        }


async def fetch_bytes(
    url: str,
    *,
    auth: tuple[str, str] | None = None,
    params: dict | None = None,
    timeout: int = 60,
    verify: bool = True,
) -> tuple[int, str, bytes]:
    """GET a binary body: (status, content type, bytes).

    :func:`request` decodes every body as text, which corrupts images and
    audio; this returns the bytes untouched.
    """
    session = await get_session(host_of(url), verify)
    kwargs: dict = {"timeout": ClientTimeout(total=timeout)}
    if auth:
        kwargs["auth"] = BasicAuth(auth[0], auth[1])
    if params:
        kwargs["params"] = params
    async with session.get(url, **kwargs) as resp:
        body = await resp.read()
        return resp.status, resp.headers.get("Content-Type", "application/octet-stream"), body


_TLS_WARNED: set[tuple[str, str]] = set()


def _warn_once(host: str, message: str) -> None:
    """Say it once per host and message, not on every request: the Nextcloud
    callers ask for verify=False on each call, and repeating it (3,248 times
    in three hours in production) buried every other warning."""
    if (host, message) in _TLS_WARNED:
        return
    _TLS_WARNED.add((host, message))
    log.warning(message, host)


async def get_session(host: str, verify: bool = True) -> ClientSession:
    """Get or create a session for the given host with connection pooling.

    TLS verification is ON by default (BUG-09). Passing verify=False is an
    escape hatch for self-signed endpoints and only takes effect when the
    operator sets MEDIA_ALLOW_INSECURE_TLS=true; otherwise we warn and
    verify anyway (fail safe).
    """
    if verify:
        effective_verify = True
    elif _insecure_tls_allowed():
        _warn_once(host, "TLS verification DISABLED for %s (MEDIA_ALLOW_INSECURE_TLS)")
        effective_verify = False
    else:
        _warn_once(
            host,
            "TLS verification turned OFF for %s but MEDIA_ALLOW_INSECURE_TLS is not enabled; verifying anyway",
        )
        effective_verify = True

    now = asyncio.get_running_loop().time()
    cache_key = (host, effective_verify)
    cached = _SESSION_CACHE.get(cache_key)

    if cached:
        session, created = cached
        # Reuse while fresh AND still open. A closed connector (e.g. after a
        # DNS-sidecar restart dropped the connection) is recreated below so a
        # stale pooled connection never silently serves requests.
        if not session.closed and now - created < _CACHE_MAX_AGE:
            return session

    if effective_verify:
        import ssl

        ssl_param: bool | ssl.SSLContext = ssl.create_default_context()
    else:
        ssl_param = False

    connector = TCPConnector(
        limit=_MAX_CONNECTIONS,
        limit_per_host=_MAX_CONNECTIONS_PER_HOST,
        enable_cleanup_closed=True,
        ttl_dns_cache=_DNS_TTL,
        ssl=ssl_param,
    )

    previous = _SESSION_CACHE.get(cache_key)
    if previous is not None:
        with contextlib.suppress(Exception):
            await previous[0].close()
    session = ClientSession(connector=connector)
    _SESSION_CACHE[cache_key] = (session, now)
    return session


async def close_all_sessions():
    """Close all cached sessions."""
    for host in list(_SESSION_CACHE.keys()):
        session = _SESSION_CACHE.pop(host)[0]
        with contextlib.suppress(Exception):
            await session.close()
