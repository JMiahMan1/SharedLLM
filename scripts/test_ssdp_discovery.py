#!/usr/bin/env python3
"""
Direct SSDP discovery test - run outside Docker to verify Roku is discoverable
"""
import socket

SSDP_ADDR = "239.255.255.250"
SSDP_PORT = 1900

ssdp_request = (
    f'M-SEARCH * HTTP/1.1\r\n'
    f'HOST: {SSDP_ADDR}:{SSDP_PORT}\r\n'
    f'MAN: "ssdp:discover"\r\n'
    f'MX: 3\r\n'
    f'ST: roku:ecp\r\n'
    f'\r\n'
)

print("Sending SSDP discovery request for Roku devices...")
print(f"Request:\n{ssdp_request}")

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.settimeout(5)
sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)

try:
    sock.sendto(ssdp_request.encode('utf-8'), (SSDP_ADDR, SSDP_PORT))
    print(f"\nSent to {SSDP_ADDR}:{SSDP_PORT}")
    print("Waiting for responses (5s timeout)...\n")

    found = 0
    while True:
        try:
            data, addr = sock.recvfrom(4096)
            response = data.decode('utf-8', errors='ignore')
            found += 1
            print(f"=== Response #{found} from {addr} ===")
            print(response)
            print()

        except TimeoutError:
            break

except Exception as e:
    print(f"Error: {e}")
finally:
    sock.close()

print(f"\nTotal devices found: {found}")
