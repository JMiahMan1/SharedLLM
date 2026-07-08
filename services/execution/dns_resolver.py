"""DNS resolver patch for execution service.
Patches socket.getaddrinfo to resolve .local domains via dns-sync,
enabling live failover without restarts when using network_mode: host.
"""
import logging
import socket

log = logging.getLogger("execution.dns_resolver")

_DNS_SYNC_IP = "127.0.0.1"
_DNS_SYNC_PORT = 5353
_original_getaddrinfo = socket.getaddrinfo

def _resolve_via_dns_sync(hostname: str, family: int = socket.AF_INET):
    """Resolve hostname via dns-sync server using Python's built-in DNS."""
    try:
        import dns.resolver
        resolver = dns.resolver.Resolver()
        resolver.nameservers = [_DNS_SYNC_IP]
        resolver.port = _DNS_SYNC_PORT
        resolver.timeout = 2.0
        resolver.lifetime = 2.0

        answers = resolver.resolve(hostname, 'A')
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
    if host and host.endswith('.local'):
        ip = _resolve_via_dns_sync(host)
        if ip:
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', (ip, port))]
        log.warning(f"[dns-sync] Failed to resolve {host}, falling back to system DNS")

    return _original_getaddrinfo(host, port, family, type, proto, flags)

def patch_dns_resolver():
    """Apply DNS resolver patch. Call once at startup."""
    socket.getaddrinfo = _patched_getaddrinfo
    log.info(f"[dns-sync] DNS resolver patched - .local domains will resolve via {_DNS_SYNC_IP}:{_DNS_SYNC_PORT}")
