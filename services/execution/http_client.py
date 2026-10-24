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

from aiohttp import BasicAuth, ClientSession, ClientTimeout, DummyCookieJar, TCPConnector

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
    keep_login: bool = True,
) -> dict:
    """
    Make an HTTP request using a pooled connection.

    With ``auth``, the server's session cookies for these exact credentials
    ride from one call to the next unless ``keep_login`` is off (see
    :data:`_LOGIN_COOKIES`).

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
    login_key = _login_key(host, auth) if keep_login and auth else None
    if login_key and _LOGIN_COOKIES.get(login_key):
        request_kwargs["cookies"] = dict(_LOGIN_COOKIES[login_key])

    for attempt in (1, 2):
        async with session.request(method, url, timeout=timeout, **request_kwargs) as resp:
            if attempt == 1 and login_key and resp.status == 401 and "cookies" in request_kwargs:
                # A session the server has since dropped: forget it and log
                # in afresh once, with the Basic auth alone.
                _LOGIN_COOKIES.pop(login_key, None)
                request_kwargs.pop("cookies")
                continue
            if login_key:
                _remember_login(login_key, resp)
            response_text = await resp.text()
            return {
                "status_code": resp.status,
                "headers": dict(resp.headers),
                "text": response_text,
                "content": response_text.encode("utf-8"),
                "ok": resp.status in (200, 201, 204),
            }
    raise AssertionError("unreachable")


async def fetch_bytes(
    url: str,
    *,
    auth: tuple[str, str] | None = None,
    params: dict | None = None,
    timeout: int = 60,
    verify: bool = True,
    keep_login: bool = True,
) -> tuple[int, str, bytes]:
    """GET a binary body: (status, content type, bytes).

    :func:`request` decodes every body as text, which corrupts images and
    audio; this returns the bytes untouched.
    """
    host = host_of(url)
    session = await get_session(host, verify)
    kwargs: dict = {"timeout": ClientTimeout(total=timeout)}
    if auth:
        kwargs["auth"] = BasicAuth(auth[0], auth[1])
    if params:
        kwargs["params"] = params
    login_key = _login_key(host, auth) if keep_login and auth else None
    if login_key and _LOGIN_COOKIES.get(login_key):
        kwargs["cookies"] = dict(_LOGIN_COOKIES[login_key])
    for attempt in (1, 2):
        async with session.get(url, **kwargs) as resp:
            if attempt == 1 and login_key and resp.status == 401 and "cookies" in kwargs:
                _LOGIN_COOKIES.pop(login_key, None)
                kwargs.pop("cookies")
                continue
            if login_key:
                _remember_login(login_key, resp)
            body = await resp.read()
            return resp.status, resp.headers.get("Content-Type", "application/octet-stream"), body
    raise AssertionError("unreachable")


# Nextcloud checks the password behind Basic auth on every request that
# arrives without its session cookie, and that check costs it 2-5 s (measured
# from execution, against ~0.3 s with the cookie) -- on every Talk message,
# note and thumbnail. WebDAV writes with the cookie were verified to pass
# Nextcloud's CSRF check, since Basic auth rides along. The pooled session cannot
# keep cookies (see get_session), so they are kept here instead, per host and
# per exact credentials: a cookie only ever goes back with the login that
# earned it, and a changed password starts a fresh session.
_LOGIN_COOKIES: dict[tuple[str, str, str], dict[str, str]] = {}
_LOGIN_COOKIES_MAX = 64


def _login_key(host: str, auth: tuple[str, str]) -> tuple[str, str, str]:
    import hashlib

    return host, auth[0], hashlib.sha256(auth[1].encode("utf-8")).hexdigest()


def _remember_login(key: tuple[str, str, str], resp) -> None:
    if not resp.cookies:
        return
    jar = _LOGIN_COOKIES.setdefault(key, {})
    for name, morsel in resp.cookies.items():
        if not morsel.value or morsel.value == "deleted" or morsel["max-age"] in ("0", "-1"):
            jar.pop(name, None)
        else:
            jar[name] = morsel.value
    if not jar:
        _LOGIN_COOKIES.pop(key, None)
    while len(_LOGIN_COOKIES) > _LOGIN_COOKIES_MAX:
        _LOGIN_COOKIES.pop(next(iter(_LOGIN_COOKIES)))


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
    if previous is not None and not previous[0].closed:
        # Retire, do not close: requests still running on the old session
        # failed with "Connector is closed" when it was closed here at once.
        # It is closed once the longest request it can be serving has ended.
        _retire_later(previous[0])
    # No cookie jar. One session per host is shared by every user, and
    # Nextcloud answers each request with session cookies; kept, they rode
    # along on the next user's request beside its own Basic auth, so a
    # request could run inside someone else's Nextcloud session (seen as
    # chat thumbnails 404ing for the person who could see them). Every caller
    # here sends its own credentials, so cookies are never needed.
    session = ClientSession(connector=connector, cookie_jar=DummyCookieJar())
    _SESSION_CACHE[cache_key] = (session, now)
    return session


#: Longer than any timeout a caller passes here, so a retired session is only
#: closed once nothing can still be using it.
_RETIRE_AFTER_S = 330.0


def _retire_later(session: ClientSession) -> None:
    async def close_when_idle() -> None:
        await asyncio.sleep(_RETIRE_AFTER_S)
        _RETIRING.pop(session, None)
        with contextlib.suppress(Exception):
            await session.close()

    _RETIRING[session] = asyncio.get_running_loop().create_task(close_when_idle())


_RETIRING: dict[ClientSession, asyncio.Task] = {}


async def close_all_sessions():
    """Close all cached sessions."""
    for host in list(_SESSION_CACHE.keys()):
        session = _SESSION_CACHE.pop(host)[0]
        with contextlib.suppress(Exception):
            await session.close()
    for session, task in list(_RETIRING.items()):
        task.cancel()
        _RETIRING.pop(session, None)
        with contextlib.suppress(Exception):
            await session.close()
