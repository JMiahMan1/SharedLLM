"""
Resilient hostname resolver with multi-IP fallback.

When a hostname resolves to multiple IPs (via dnsmasq round-robin),
this utility tries each IP in order until one succeeds.
"""
import asyncio
import logging
import socket

log = logging.getLogger("gateway.resolver")


async def resolve_hostname_with_fallback(hostname: str, port: int = 0, timeout: float = 3.0) -> str | None:
    """
    Resolve a hostname and return the first reachable IP.
    Tries each resolved IP in order with a TCP connect test.
    """
    try:
        results = await asyncio.get_event_loop().run_in_executor(
            None, socket.getaddrinfo, hostname, port, socket.AF_INET, socket.SOCK_STREAM
        )
        ips = list(dict.fromkeys(str(r[4][0]) for r in results))  # deduplicate, preserve order
    except socket.gaierror:
        log.warning(f"[resolver] DNS lookup failed for {hostname}")
        return None

    if not ips:
        return None

    if len(ips) == 1:
        return ips[0]

    # Try each IP until one responds
    for ip in ips:
        try:
            _, writer = await asyncio.wait_for(
                asyncio.open_connection(ip, port), timeout=timeout
            )
            writer.close()
            await writer.wait_closed()
            log.info(f"[resolver] {hostname} -> {ip} (reachable)")
            return ip
        except (TimeoutError, ConnectionRefusedError, OSError):
            log.debug(f"[resolver] {hostname} -> {ip} (unreachable, trying next)")
            continue

    log.warning(f"[resolver] All IPs unreachable for {hostname}: {ips}")
    return ips[0]  # Return first IP as fallback even if unreachable
