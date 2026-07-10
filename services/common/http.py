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


def get_client() -> aiohttp.ClientSession:
    """Return a process-wide pooled aiohttp client (verifies TLS)."""
    global _client
    if _client is None or _client.closed:
        _client = aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(limit=100, limit_per_host=20, ttl_dns_cache=_DNS_TTL)
        )
    return _client
