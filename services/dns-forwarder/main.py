import logging
import os
import socket
import time

import dns.message
import dns.query
import dns.rcode

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("dns_forwarder")

# Use configurable upstream host (default: dns-sync for Docker bridge)
# For cross-network (host-networked dns-sync), use host gateway IP
UPSTREAM_HOST = os.environ.get("DNS_SYNC_HOST", "dns-sync")
UPSTREAM_PORT = int(os.environ.get("DNS_SYNC_PORT", "5353"))
LISTEN_PORT = int(os.environ.get("LISTEN_PORT", "53"))
BUF_SIZE = 65535

# Try to discover host gateway IP if using host-networked dns-sync
if UPSTREAM_HOST == "dns-sync":
    # Docker bridge mode - service name resolution should work
    log.info(f"DNS forwarder in Docker bridge mode -> dns-sync:{UPSTREAM_PORT}")
else:
    log.info(f"DNS forwarder listening on 0.0.0.0:{LISTEN_PORT} -> {UPSTREAM_HOST}:{UPSTREAM_PORT}")


def forward(query_bytes: bytes, src_addr: tuple) -> bytes:
    """Forward DNS query to upstream with retry logic."""
    msg = dns.message.from_wire(query_bytes)
    retries = 2
    last_exception = None

    for attempt in range(retries + 1):
        try:
            response = dns.query.udp(msg, (UPSTREAM_HOST, UPSTREAM_PORT), timeout=5)
            return response.to_wire()
        except dns.query.BadResponse:
            response = dns.message.make_response(msg)
            response.set_rcode(dns.rcode.SERVFAIL)
            return response.to_wire()
        except Exception as e:
            last_exception = e
            if attempt < retries:
                log.debug(f"Upstream query attempt {attempt + 1} failed, retrying: {e}")
                time.sleep(0.1)  # Brief delay before retry

    # All retries exhausted
    log.warning(f"Upstream query failed after {retries + 1} attempts: {last_exception}")
    response = dns.message.make_response(msg)
    response.set_rcode(dns.rcode.SERVFAIL)
    return response.to_wire()


def main():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("0.0.0.0", LISTEN_PORT))
    log.info("Ready to forward DNS queries")

    while True:
        try:
            data, addr = sock.recvfrom(BUF_SIZE)
        except (OSError, KeyboardInterrupt):
            break
        try:
            response = forward(data, addr)
        except Exception as e:
            log.exception(f"Error forwarding query from {addr}: {e}")
            continue
        try:
            sock.sendto(response, addr)
        except (OSError, KeyboardInterrupt):
            break

    sock.close()


if __name__ == "__main__":
    main()
