"""
Shared HTTP client management for the gateway services.

Provides per-event-loop aiohttp client pooling with DNS recovery.
Used by both main.py and agent_loop.py to avoid circular imports.
"""
import asyncio
import contextlib
import logging
import random
import threading
import time
from contextlib import asynccontextmanager
from typing import AsyncIterator

import aiohttp

from services.common.http import INTERNAL_KEEPALIVE_S

log = logging.getLogger("gateway.http_client")

_original_async_client = aiohttp.ClientSession
# One HTTP client per event loop. The Raven worker runs on its own event loop,
# so this module's client must be cached per-loop rather than as a single global
# that would thrash/leak when both the API loop and the worker loop call in.
_http_clients: dict[asyncio.AbstractEventLoop, aiohttp.ClientSession] = {}
_fallback_http_client: aiohttp.ClientSession | None = None
_dns_recovery_lock = threading.Lock()
_DNS_TTL = 60  # re-resolve DNS at most every 60s so pooled connectors don't go stale
_CLIENT_RECREATE_COOLDOWN = 10.0  # s; avoid tearing down the shared pool on every DNS blip
_last_client_recreate = 0.0


def _new_http_client() -> aiohttp.ClientSession:
    return _original_async_client(
        headers={"X-Request-Source": "shared-llm/app"},
        timeout=aiohttp.ClientTimeout(300.0, connect=30.0),
        connector=aiohttp.TCPConnector(limit=100, limit_per_host=20, ttl_dns_cache=_DNS_TTL,
                                       keepalive_timeout=INTERNAL_KEEPALIVE_S),
    )


def get_http_client() -> aiohttp.ClientSession:
    """Return the aiohttp client for the CURRENT event loop (lazy, one per loop).

    Caching per-loop is required now that the Raven worker runs on a separate
    event loop from the FastAPI API loop. Each loop keeps a stable client; the
    client is recreated on first use within a loop and reused thereafter.
    """
    try:
        current_loop = asyncio.get_running_loop()
    except RuntimeError:
        current_loop = None

    if current_loop is None:
        # No running loop (e.g. a synchronous context). Fall back to a module global.
        global _fallback_http_client
        if _fallback_http_client is None or _fallback_http_client.closed:
            _fallback_http_client = _new_http_client()
        return _fallback_http_client

    client = _http_clients.get(current_loop)
    if client is None or client.closed:
        client = _new_http_client()
        _http_clients[current_loop] = client
    return client


@contextlib.asynccontextmanager
async def shared_http_client() -> AsyncIterator[aiohttp.ClientSession]:
    """Yield the shared, pooled global HTTP client WITHOUT closing it on exit.

    Use this instead of ``async with aiohttp.ClientSession()`` for internal
    service-to-service calls so TCP connections are reused across requests
    (the global client is created once per event loop with a connection pool).
    The caller must not close the yielded session.
    """
    yield get_http_client()


async def recreate_http_client():
    """Close the current loop's client and mark it for recreation with fresh DNS
    resolution. This is needed when DNS changes (e.g., dns-sync restart) cause
    stale keepalive connections to fail with empty aiohttp.ClientError messages.

    Operates on the CURRENT event loop's client only (per-loop clients), so the
    API loop and the Raven worker loop each refresh independently. A short
    cooldown prevents storms of DNS failures from churning connections. The new
    client is created lazily on the next ``get_http_client()`` call for that loop.
    """
    with _dns_recovery_lock:
        global _last_client_recreate, _fallback_http_client
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None
        now = current_loop.time() if current_loop is not None else time.monotonic()
        if current_loop is not None:
            existing = _http_clients.get(current_loop)
            if existing is not None and now - _last_client_recreate < _CLIENT_RECREATE_COOLDOWN:
                log.debug("[DNSRecovery] Skipping client recreation (cooldown active); relying on ttl_dns_cache")
                return
            if existing is not None:
                log.info("[DNSRecovery] Closing stale HTTP client (loop=%s) to refresh DNS resolution", id(current_loop))
                # Close on the owning loop; we are already running on it here.
                await existing.close()
                _http_clients.pop(current_loop, None)
        else:
            if _fallback_http_client is not None and now - _last_client_recreate < _CLIENT_RECREATE_COOLDOWN:
                return
            if _fallback_http_client is not None:
                log.info("[DNSRecovery] Closing stale fallback HTTP client to refresh DNS resolution")
                with contextlib.suppress(Exception):
                    _fallback_http_client.close()
                _fallback_http_client = None
        _last_client_recreate = now
        log.info("[DNSRecovery] HTTP client marked for refresh (recreated on next use)")


def _is_dns_failure(e: aiohttp.ClientError) -> bool:
    """Detect DNS-related failures that indicate stale DNS cache.
    These manifest as aiohttp.ClientError with empty messages when using
    keepalive connections that were established before a DNS change."""
    msg = str(e).strip()
    # Empty message on a keepalive connection = stale DNS resolution
    if not msg:
        return True
    # Docker DNS failure patterns
    dns_patterns = ["nodename", "noname", "could not resolve", "getaddrinfo",
                    "Name or service not known", "DNS", "resolve"]
    return any(p.lower() in msg.lower() for p in dns_patterns)


def _retry_delay(base_delay: float, attempt: int) -> float:
    """Exponential backoff with jitter, so retries do not march in step.

    Several callers retry the same dependency at the same moment (every request
    in flight when identity blinks, say). Without jitter they all come back at
    the same instant and hit it together again.
    """
    return base_delay * (2 ** attempt) * (1.0 + random.random() * 0.5)


async def retry_http_request(func, service_name: str, max_retries: int = 2, base_delay: float = 0.1, dns_recovery: bool = True):
    """Retry an aiohttp request with exponential backoff for transient errors.
    Detects DNS-related failures (empty RequestError messages on stale connections)
    and automatically recreates the HTTP client to refresh DNS resolution."""
    for attempt in range(max_retries + 1):
        try:
            return await func()
        except RuntimeError as e:
            # Handle race condition where client was closed by DNS recovery
            if "client has been closed" in str(e):
                if attempt == max_retries:
                    log.error(f"{service_name}: Client closed, all retries exhausted")
                    raise
                log.warning(f"{service_name}: HTTP client was closed, recreating and retrying")
                try:
                    await recreate_http_client()
                except Exception as rec_err:
                    log.error(f"{service_name}: Failed to recreate HTTP client: {rec_err}")
                delay = _retry_delay(base_delay, attempt)
                await asyncio.sleep(delay)
            else:
                raise
        except aiohttp.ClientError as e:
            if attempt == max_retries:
                log.error(f"{service_name}: All {max_retries + 1} attempts failed: {e}")
                raise
            # If this looks like a DNS failure and recovery is enabled, recreate the client
            if dns_recovery and _is_dns_failure(e) and attempt == 0:
                log.warning(f"{service_name}: DNS-related failure detected, recreating HTTP client")
                try:
                    await recreate_http_client()
                except Exception as rec_err:
                    log.error(f"{service_name}: Failed to recreate HTTP client: {rec_err}")
            delay = _retry_delay(base_delay, attempt)
            log.warning(f"{service_name}: RequestError (attempt {attempt+1}/{max_retries+1}): {e}. Retrying in {delay}s")
            await asyncio.sleep(delay)
        except TimeoutError as e:
            if attempt == max_retries:
                log.error(f"{service_name}: Timed out after {max_retries + 1} attempts: {e}")
                # Re-raise as aiohttp.ClientError so callers' error handling returns clean 5xx
                raise aiohttp.ClientError(f"Timeout: {e}") from e
            delay = _retry_delay(base_delay, attempt)
            log.warning(f"{service_name}: Timeout (attempt {attempt+1}/{max_retries+1}): {e}. Retrying in {delay}s")
            await asyncio.sleep(delay)
    raise Exception(f"Unexpected: exhausted retries for {service_name}")


@asynccontextmanager
async def borrow_http_client() -> AsyncIterator[aiohttp.ClientSession]:
    """Alias for shared_http_client for backward compatibility."""
    yield get_http_client()


async def close_all_clients():
    """Close all HTTP clients. Call during application shutdown."""
    global _fallback_http_client
    for _client in list(_http_clients.values()):
        with contextlib.suppress(Exception):
            await _client.close()
    _http_clients.clear()
    if _fallback_http_client is not None:
        with contextlib.suppress(Exception):
            await _fallback_http_client.close()
        _fallback_http_client = None