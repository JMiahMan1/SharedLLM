"""Shared DNS resolver patch for services that need reliable .local resolution.

Patches socket.getaddrinfo so that `.local` hostnames are resolved through
the app's DNS service (the "dns" container on the sharedllm network) instead
of the flaky container DNS path. The DNS service serves the dns_mappings
entries (e.g. jeremiah-home-desktop.local -> 192.168.1.216). No hardcoded
LAN IPs: the DNS server IP is discovered at patch time by resolving the
"dns" service name (Docker's embedded resolver already knows it).
"""
import logging
import socket

log = logging.getLogger("sharedllm.dns_resolver")

# The DNS service container name on the sharedllm network. Its IP is resolved
# at patch time (see _resolve_dns_server_ip) rather than hardcoded.
_DNS_SYNC_HOST = "dns"
_DNS_SYNC_PORT = 53
_original_getaddrinfo = socket.getaddrinfo
_dns_server_ip = None


def _resolve_dns_server_ip() -> str | None:
    """Discover the DNS service IP by resolving its container name.

    Docker's embedded resolver already maps "dns" -> the service's bridge IP,
    so we lean on the working system path instead of a hardcoded address.
    """
    global _dns_server_ip
    if _dns_server_ip:
        return _dns_server_ip
    try:
        infos = socket.getaddrinfo(_DNS_SYNC_HOST, _DNS_SYNC_PORT, proto=socket.IPPROTO_UDP)
        for info in infos:
            ip = info[4][0]
            if ":" not in ip:  # IPv4 only
                _dns_server_ip = ip
                return _dns_server_ip
    except Exception as e:
        log.warning(f"[dns-sync] Could not resolve DNS server '{_DNS_SYNC_HOST}': {e}")
    return _dns_server_ip


def _resolve_via_dns_sync(hostname: str, family: int = socket.AF_INET):
    """Resolve hostname via the app's DNS service using dnspython."""
    server = _resolve_dns_server_ip()
    if not server:
        log.debug(f"[dns-sync] No DNS server discovered, falling back to system DNS")
        return None
    try:
        import dns.resolver
        resolver = dns.resolver.Resolver()
        resolver.nameservers = [server]
        resolver.port = _DNS_SYNC_PORT
        resolver.timeout = 2.0
        resolver.lifetime = 2.0

        answers = resolver.resolve(hostname, "A")
        for rdata in answers:
            ip = str(rdata)
            log.debug(f"[dns-sync] Resolved {hostname} -> {ip}")
            return ip
    except ImportError:
        log.warning("dnspython not installed, falling back to system DNS")
        return None
    except Exception as e:
        log.debug(f"[dns-sync] Resolution failed for {hostname}: {e}")
        return None


def _patched_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
    """Patched getaddrinfo that routes .local domains through dns-sync."""
    if host and host.endswith(".local"):
        ip = _resolve_via_dns_sync(host)
        if ip:
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port))]
        log.warning(f"[dns-sync] Failed to resolve {host}, falling back to system DNS")

    return _original_getaddrinfo(host, port, family, type, proto, flags)


def patch_dns_resolver():
    """Apply DNS resolver patch. Call once at startup."""
    socket.getaddrinfo = _patched_getaddrinfo
    log.info(
        f"[dns-sync] DNS resolver patched - .local domains will resolve via {_DNS_SYNC_IP}:{_DNS_SYNC_PORT}"
    )
