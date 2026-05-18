#!/usr/bin/env python3
"""DNS Sync Sidecar - Polls Identity for DNS mappings, health-checks IPs, and serves DNS with automatic failover."""
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
HEALTH_CHECK_INTERVAL = int(os.environ.get("HEALTH_CHECK_INTERVAL", "10"))
HEALTH_CHECK_TIMEOUT = int(os.environ.get("HEALTH_CHECK_TIMEOUT", "2"))

# Default health check ports by hostname pattern
DEFAULT_HEALTH_PORTS = {
    "ollama-server": 11434,
    "llama-server": 11434,
    "ai": 8080,
}

POLL_INTERVAL = DNS_POLL_INTERVAL
running = True
dns_records = {}        # hostname -> list of all configured IPs
health_status = {}      # (hostname, ip) -> bool (True = alive)
dns_lock = threading.Lock()
health_lock = threading.Lock()

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

def get_health_port(hostname):
    """Determine health check port for a hostname."""
    base = hostname.replace('.local', '')
    for pattern, port in DEFAULT_HEALTH_PORTS.items():
        if pattern in base:
            return port
    return 80  # default fallback

def check_ip_alive(ip, port):
    """TCP connect check to see if an IP is reachable."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(HEALTH_CHECK_TIMEOUT)
        result = sock.connect_ex((ip, port))
        sock.close()
        return result == 0
    except Exception:
        return False

def health_checker():
    """Background thread that health-checks all configured IPs."""
    global health_status
    print(f"[dns-sync] Health checker started (interval={HEALTH_CHECK_INTERVAL}s, timeout={HEALTH_CHECK_TIMEOUT}s)", flush=True)
    
    while running:
        with dns_lock:
            current_records = dict(dns_records)
        
        new_health = {}
        changed = False
        
        for hostname, ips in current_records.items():
            port = get_health_port(hostname)
            for ip in ips:
                alive = check_ip_alive(ip, port)
                key = (hostname, ip)
                with health_lock:
                    old = health_status.get(key)
                    if old != alive:
                        changed = True
                        status_str = "ALIVE" if alive else "DEAD"
                        print(f"[dns-sync] HEALTH: {ip}:{port} {hostname} -> {status_str}", flush=True)
                    health_status[key] = alive
                new_health[key] = alive
        
        if changed:
            _print_health_summary()
        
        for _ in range(HEALTH_CHECK_INTERVAL):
            if not running:
                break
            time.sleep(1)

def _print_health_summary():
    """Print current health status summary."""
    with dns_lock:
        records = dict(dns_records)
    with health_lock:
        status = dict(health_status)
    
    for hostname, ips in records.items():
        alive_ips = [ip for ip in ips if status.get((hostname, ip), False)]
        dead_ips = [ip for ip in ips if not status.get((hostname, ip), False)]
        if alive_ips:
            print(f"[dns-sync] DNS {hostname}: alive={alive_ips}, dead={dead_ips}", flush=True)

def get_alive_ips(hostname):
    """Get list of alive IPs for a hostname. Only returns alive IPs."""
    with dns_lock:
        all_ips = list(dns_records.get(hostname, []))
    
    if not all_ips:
        return []
    
    with health_lock:
        alive = [ip for ip in all_ips if health_status.get((hostname, ip), False)]
    
    # Only return alive IPs. If all dead, return all as last resort.
    return alive if alive else all_ips

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
        clean_ips = [ip for ip in ips if ip]
        new_records[hostname.lower()] = clean_ips
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
    flags = 0x8180 | rcode
    header = struct.pack('!HHHHHH', txid, flags, 1, len(answers), 0, 0)
    qname = b''
    for label in hostname.split('.'):
        qname += bytes([len(label)]) + label.encode()
    qname += b'\x00'
    question = qname + struct.pack('!HH', 1, 1)
    ans = b''
    for ip in answers:
        ans += b'\xc0\x0c'
        ans += struct.pack('!HHIH', 1, 1, 5, 4)  # TTL 5s for fast failover
        ans += bytes(int(x) for x in ip.split('.'))
    return header + question + ans

def forward_query(hostname, qtype):
    """Forward query to upstream DNS server."""
    try:
        query = bytearray()
        query += b'\x00\x01'
        query += b'\x01\x00'
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
    """Run a simple DNS server with health-aware responses."""
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
            
            if qtype == 1:  # A record
                answers = get_alive_ips(hostname)
                if answers:
                    resp = build_dns_response(txid, hostname, answers)
                    sock.sendto(resp, addr)
                    print(f"[dns-sync] DNS: {hostname} -> {answers}", flush=True)
                else:
                    resp = build_dns_response(txid, hostname, [], rcode=3)
                    sock.sendto(resp, addr)
            else:
                resp = forward_query(hostname, qtype or 1)
                if resp:
                    resp = struct.pack('!H', txid) + resp[2:]
                    sock.sendto(resp, addr)
                else:
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
    
    # Start health checker
    health_thread = threading.Thread(target=health_checker, daemon=True)
    health_thread.start()
    
    # Start DNS server
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
