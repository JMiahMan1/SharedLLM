"""Shared pooled aiohttp client for non-gateway/execution services.

Mirrors the gateway ``get_http_client()`` / execution ``get_session()`` pooling
pattern (Phase 1 rows 55/59) so other services stop opening a fresh
``aiohttp.ClientSession`` per request. The connector keeps a short
``ttl_dns_cache`` so DNS re-resolves independently per host (matching the
gateway/execution DNS-recovery behaviour) without churning every connection.

NOTE: this client verifies TLS. Services that talk to hosts with self-signed
certificates (e.g. Home Assistant, Roku, webOS) must use their own
``TCPConnector(ssl=False)`` session or execution's ``get_session(host_of(url))``
— do NOT route those through ``get_client()``.
"""

from __future__ import annotations

import aiohttp

_DNS_TTL = 60
_client: aiohttp.ClientSession | None = None
_client_insecure: aiohttp.ClientSession | None = None


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


def _session_dead(client: aiohttp.ClientSession | None) -> bool:
    """A session can report ``closed == False`` while its underlying connector
    has been closed (e.g. after a transient upstream disconnect). Reusing such a
    session raises ``AssertionError: Connector is closed`` on the next request,
    which is not a ``ClientError`` and escapes normal error handling. Treat a
    closed/missing connector as a dead session so it gets recreated."""
    if client is None or client.closed:
        return True
    connector = client.connector
    return connector is None or getattr(connector, "closed", False)


def get_client() -> aiohttp.ClientSession:
    """Return a process-wide pooled aiohttp client (verifies TLS)."""
    global _client
    if _session_dead(_client):
        _client = _make(True)
    return NonClosingSessionWrapper(_client)


def get_client_insecure() -> aiohttp.ClientSession:
    """Return a pooled client that does NOT verify TLS.

    For hosts with self-signed certificates (Home Assistant, Nextcloud, Roku,
    webOS, etc.). Do NOT use for public endpoints — use ``get_client()``.
    """
    global _client_insecure
    if _session_dead(_client_insecure):
        _client_insecure = _make(False)
    return NonClosingSessionWrapper(_client_insecure)
