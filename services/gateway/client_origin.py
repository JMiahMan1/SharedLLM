"""Where a request really came from: inside the house, or the internet.

A request without an API key may borrow the household identity (kiosks and
voice satellites have no key). That must only ever happen inside the house.
Every request reaches the gateway through Caddy, so the peer address is always
a container; the caller is found in ``X-Forwarded-For``.

Caddy replaces that header for a caller it does not trust and appends to it
for its trusted front proxy (``PUBLIC_PROXY_IP``), so the chain is read from
the right: each trusted proxy hop is skipped and the first other address is
the caller. A spoofed entry a caller adds on the left is never reached.
"""

from __future__ import annotations

import ipaddress
import os

#: Front proxies Caddy trusts (it appends to their X-Forwarded-For). Read once
#: at start up.
TRUSTED_PROXIES: frozenset[str] = frozenset(
    p.strip() for p in (os.getenv("PUBLIC_PROXY_IP") or "").split(",") if p.strip()
)


#: The home network, WireGuard, the containers and the box itself. Listed
#: explicitly: ``ip.is_private`` also counts documentation and other reserved
#: ranges that can arrive from the internet.
_LOCAL_NETWORKS = tuple(
    ipaddress.ip_network(n)
    for n in (
        "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "127.0.0.0/8", "169.254.0.0/16",
        "::1/128", "fc00::/7", "fe80::/10",
    )
)


def _is_local(addr: str | None) -> bool:
    """The LAN, WireGuard, the containers or loopback."""
    if not addr:
        return False
    try:
        ip = ipaddress.ip_address(addr.strip().strip("[]"))
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return any(ip in net for net in _LOCAL_NETWORKS if net.version == ip.version)


def caller_address(peer: str | None, forwarded_for: str | None, trusted: frozenset[str] = TRUSTED_PROXIES) -> str | None:
    """The caller's address, given the TCP peer and the X-Forwarded-For header."""
    if not forwarded_for:
        return peer
    if not _is_local(peer):
        # Only Caddy (a container) may vouch for a forwarded address.
        return peer
    hops = [h.strip() for h in forwarded_for.split(",") if h.strip()]
    for hop in reversed(hops):
        if hop not in trusted:
            return hop
    return hops[0] if hops else peer


def is_inside(peer: str | None, forwarded_for: str | None, trusted: frozenset[str] = TRUSTED_PROXIES) -> bool:
    """Whether the request came from the home network, WireGuard or the stack."""
    return _is_local(caller_address(peer, forwarded_for, trusted))


def request_is_inside(request) -> bool:
    peer = request.client.host if getattr(request, "client", None) else None
    return is_inside(peer, request.headers.get("X-Forwarded-For"))
