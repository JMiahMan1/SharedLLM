#!/usr/bin/env python3
"""DNS Sync Sidecar - Polls Identity for DNS mappings and runs a built-in DNS server."""
import os
import sys
import json
import time
import signal
import socket
import struct
import threading
from urllib.request import Request, urlopen

INTERNAL_SECRET = os.environ.get("INTERNAL_SECRET", "")
IDENTITY_URL = os.environ.get("IDENTITY_SVC_URL", "http://localhost:8001")
DNS_POLL_INTERVAL = int(os.environ.get("DNS_POLL_INTERVAL", "30"))
DNS_LISTEN_PORT = int(os.environ.get("DNS_LISTEN_PORT", "53"))
UPSTREAM_DNS = os.environ.get("UPSTREAM_DNS", "127.0.0.11")

POLL_INTERVAL = DNS_POLL_INTERVAL
running = True
dns_records = {}
dns_lock = threading.Lock()

def handle_signal(signum, frame):
    global running
    running = False

signal.signal(signal.SIGTERM, handle_signal)
signal.signal(signal.SIGINT, handle_signal)

def fetch_dns_mappings():
    """Fetch DNS mappings from Identity settings."""
    try:
        req = Request(
            f"{IDENTITY_URL}/api/settings",
            headers={"X-Internal-Secret": INTERNAL_SECRET}
        )
        with urlopen(req, timeout=10) as resp:
            settings = json.loads(resp.read())
            for s in settings:
                if s["key"] == "dns_mappings":
                    return json.loads(s["value"]) if s["value"] else {}
    except Exception as e:
        print(f"[dns-sync] Error fetching settings: {e}", flush=True)
    return {}

def update_dns_records(mappings):
    """Update in-memory DNS records from mappings."""
    global dns_records
    new_records = {}
    for hostname, ips in mappings.items():
        if not hostname:
            continue
        if isinstance(ips, str):
            ips = [ips]
        elif not isinstance(ips, list):
            continue
        if not hostname.endswith('.local'):
            hostname = f"{hostname}.local"
        new_records[hostname.lower()] = [ip for ip in ips if ip]
    with dns_lock:
        dns_records = new_records
    print(f"[dns-sync] Updated DNS records: {len(new_records)} hostnames", flush=True)

def parse_dns_query(data):
    """Parse a DNS query and return (txid, hostname, qtype)."""
    if len(data) < 12:
        return None, None, None
    txid = struct.unpack('!H', data[0:2])[0]
    qdcount = struct.unpack('!H', data[4:6])[0]
    if qdcount != 1:
        return txid, None, None
    idx = 12
    name = []
    while idx < len(data):
        length = data[idx]
        if length == 0:
            idx += 1
            break
        idx += 1
        if idx + length > len(data):
            return txid, None, None
        name.append(data[idx:idx+length].decode('ascii', errors='replace'))
        idx += length
    hostname = '.'.join(name).lower()
    if idx + 4 > len(data):
        return txid, hostname, None
    qtype = struct.unpack('!H', data[idx:idx+2])[0]
    return txid, hostname, qtype

def build_dns_response(txid, hostname, answers, rcode=0):
    """Build a DNS response packet."""
    flags = 0x8180 | rcode  # Response, recursion available
    header = struct.pack('!HHHHHH', txid, flags, 1, len(answers), 0, 0)
    qname = b''
    for label in hostname.split('.'):
        qname += bytes([len(label)]) + label.encode()
    qname += b'\x00'
    question = qname + struct.pack('!HH', 1, 1)  # Type A, Class IN
    ans = b''
    for ip in answers:
        ans += b'\xc0\x0c'  # Pointer to name at offset 12
        ans += struct.pack('!HHIH', 1, 1, 300, 4)  # Type A, Class IN, TTL 300, RDLEN 4
        ans += bytes(int(x) for x in ip.split('.'))
    return header + question + ans

def forward_query(hostname, qtype):
    """Forward query to upstream DNS server."""
    try:
        query = bytearray()
        query += b'\x00\x01'  # TXID
        query += b'\x01\x00'  # Flags: RD
        query += b'\x00\x01\x00\x00\x00\x00\x00\x00'
        for label in hostname.split('.'):
            query += bytes([len(label)]) + label.encode()
        query += b'\x00'
        query += struct.pack('!HH', qtype, 1)
        
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(2)
        sock.sendto(bytes(query), (UPSTREAM_DNS, 53))
        resp, _ = sock.recvfrom(512)
        sock.close()
        return resp
    except Exception:
        return None

def dns_server():
    """Run a simple DNS server."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(('0.0.0.0', DNS_LISTEN_PORT))
    print(f"[dns-sync] DNS server listening on 0.0.0.0:{DNS_LISTEN_PORT}", flush=True)
    
    while running:
        try:
            sock.settimeout(1)
            data, addr = sock.recvfrom(512)
            txid, hostname, qtype = parse_dns_query(data)
            if hostname is None:
                continue
            
            with dns_lock:
                answers = dns_records.get(hostname, [])
            
            if answers and qtype == 1:  # A record
                resp = build_dns_response(txid, hostname, answers)
                sock.sendto(resp, addr)
                print(f"[dns-sync] DNS: {hostname} -> {answers}", flush=True)
            else:
                # Forward to upstream
                resp = forward_query(hostname, qtype or 1)
                if resp:
                    # Replace TXID
                    resp = struct.pack('!H', txid) + resp[2:]
                    sock.sendto(resp, addr)
                else:
                    # Return NXDOMAIN
                    resp = build_dns_response(txid, hostname, [], rcode=3)
                    sock.sendto(resp, addr)
        except socket.timeout:
            continue
        except Exception as e:
            print(f"[dns-sync] DNS error: {e}", flush=True)
    
    sock.close()
    print("[dns-sync] DNS server stopped", flush=True)

def main():
    global running
    print(f"[dns-sync] Starting DNS sync sidecar (poll every {POLL_INTERVAL}s)", flush=True)
    
    # Start DNS server in background thread
    dns_thread = threading.Thread(target=dns_server, daemon=True)
    dns_thread.start()
    
    last_mappings = None
    
    while running:
        mappings = fetch_dns_mappings()
        if mappings != last_mappings:
            update_dns_records(mappings)
            last_mappings = mappings
        
        for _ in range(POLL_INTERVAL):
            if not running:
                break
            time.sleep(1)
    
    print("[dns-sync] Shutting down", flush=True)

if __name__ == "__main__":
    main()
