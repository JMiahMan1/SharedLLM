"""Shared pooled aiohttp client for non-gateway/execution services.

Mirrors the gateway ``get_http_client()`` / execution ``get_session()`` pooling
pattern (Phase 1 rows 55/59) so other services stop opening a fresh
``aiohttp.ClientSession`` per request. The connector keeps a short
``ttl_dns_cache`` so DNS re-resolves independently per host (matching the
gateway/execution DNS-recovery behaviour) without churning every connection.

NOTE: this client verifies TLS. Services that talk to hosts with self-signed
certificates (e.g. Home Assistant, Roku, webOS) must use their own
``TCPConnector(ssl=False)`` session or execution's ``get_session(host_of(url))``.
Do NOT route those through ``get_client()``.
"""

from __future__ import annotations

import asyncio

import aiohttp

_DNS_TTL = 60

#: One session per (TLS verification, event loop). A session belongs to the
#: loop that created it, so a worker thread running its own loop (RavenWorker)
#: gets its own session instead of sharing, or closing, the main loop's. The
#: ``None`` loop key is for callers outside any running loop.
_clients: dict[tuple[bool, asyncio.AbstractEventLoop | None], aiohttp.ClientSession] = {}


class NonClosingSessionWrapper:
    """Delegation wrapper that prevents a shared ClientSession from being closed
    when exited as an asynchronous context manager.
    """
    def __init__(self, session: aiohttp.ClientSession):
        self._session = session
    async def __aenter__(self) -> NonClosingSessionWrapper:
        return self
    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        pass
    def __getattr__(self, name):
        return getattr(self._session, name)


def _make(verify: bool) -> aiohttp.ClientSession:
    return aiohttp.ClientSession(
        connector=aiohttp.TCPConnector(
            limit=100, limit_per_host=20, ttl_dns_cache=_DNS_TTL, ssl=verify
        )
    )


def _close_connector(client: aiohttp.ClientSession) -> None:
    """Close a session's connector without awaiting (its loop may be gone).

    ``close()`` is a coroutine on newer aiohttp, which cannot run here;
    ``_close()`` releases the connections synchronously on every 3.x release.
    """
    if client.connector is not None and not client.connector.closed:
        client.connector._close()


def _retire_session(client: aiohttp.ClientSession | None) -> None:
    """Safely close or retire an obsolete session of the current loop."""
    if client is not None and not client.closed:
        try:
            current_loop = asyncio.get_running_loop()
            if current_loop.is_running():
                current_loop.create_task(client.close())
            else:
                _close_connector(client)
        except RuntimeError:
            _close_connector(client)


def _session_dead(client: aiohttp.ClientSession | None) -> bool:
    """A session can report ``closed == False`` while its underlying connector
    has been closed (e.g. after a transient upstream disconnect). Reusing such a
    session raises ``AssertionError: Connector is closed`` on the next request,
    which is not a ``ClientError`` and escapes normal error handling. Treat a
    closed/missing connector as a dead session so it gets recreated."""
    if client is None or client.closed:
        return True
    connector = client.connector
    return connector is None or bool(getattr(connector, "closed", False))


def _running_loop() -> asyncio.AbstractEventLoop | None:
    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        return None


def _forget_closed_loops() -> None:
    """Drop the sessions of loops that have since closed (a finished worker
    thread, a test's loop), so neither they nor their loops are kept."""
    for key in [k for k in _clients if k[1] is not None and k[1].is_closed()]:
        _close_connector(_clients.pop(key))


def _session(verify: bool) -> NonClosingSessionWrapper:
    _forget_closed_loops()
    key = (verify, _running_loop())
    client = _clients.get(key)
    if _session_dead(client):
        _retire_session(client)
        client = _clients[key] = _make(verify)
    return NonClosingSessionWrapper(client)


def get_client() -> aiohttp.ClientSession:
    """Return the pooled aiohttp client of the running event loop (verifies TLS)."""
    return _session(True)


def get_client_insecure() -> aiohttp.ClientSession:
    """Return a pooled client that does NOT verify TLS (per event loop).

    For hosts with self-signed certificates (Home Assistant, Nextcloud, Roku,
    webOS, etc.). Do NOT use for public endpoints; use ``get_client()``.
    """
    return _session(False)


async def close_client() -> None:
    """Close the pooled sessions on service shutdown.

    The running loop's sessions are awaited; a session of another loop that is
    still running is closed on that loop, and one whose loop has stopped has
    its connector closed directly.
    """
    current = _running_loop()
    for (_, loop), c in list(_clients.items()):
        if c.closed:
            continue
        try:
            if loop is current:
                await c.close()
            elif loop is not None and loop.is_running():
                asyncio.run_coroutine_threadsafe(c.close(), loop)
            else:
                _close_connector(c)
        except Exception:
            pass
    _clients.clear()
