import socket
import struct
import sys
import time
import logging

import dns.message
import dns.query
import dns.rdatatype
import dns.rcode

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("dns_forwarder")

UPSTREAM_HOST = "dns-sync"
UPSTREAM_PORT = 5353
LISTEN_PORT = 53
BUF_SIZE = 65535

log.info(f"DNS forwarder listening on 0.0.0.0:{LISTEN_PORT} -> {UPSTREAM_HOST}:{UPSTREAM_PORT}")


def forward(query_bytes: bytes, src_addr: tuple) -> bytes:
    msg = dns.message.from_wire(query_bytes)
    try:
        response = dns.query.udp(msg, (UPSTREAM_HOST, UPSTREAM_PORT), timeout=5)
    except dns.query.BadResponse:
        response = dns.message.make_response(msg)
        response.set_rcode(dns.rcode.SERVFAIL)
    except Exception as e:
        log.warning(f"Upstream query failed: {e}")
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
