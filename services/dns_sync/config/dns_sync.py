#!/usr/bin/env python3
"""DNS Sync - Cross-network DNS discovery and serving.

Discovers all containers (any network) via Docker API and serves DNS
records with health checking for both Docker-networked and host-networked
services. Discovers network configuration dynamically.

Integration Points:
- REST API for service discovery
"""
import os
import json
import time
import signal
import socket
import struct
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse

# Try to import docker library for container discovery
try:
    import docker
    DOCKER_CLIENT = docker.from_env()
    DOCKER_AVAILABLE = True
except ImportError:
    DOCKER_CLIENT = None
    DOCKER_AVAILABLE = False

INTERNAL_SECRET = os.environ.get("INTERNAL_SECRET", "")
DNS_POLL_INTERVAL = int(os.environ.get("DNS_POLL_INTERVAL", "30"))
DNS_LISTEN_PORT = int(os.environ.get("DNS_LISTEN_PORT", "5353"))
UPSTREAM_DNS = os.environ.get("UPSTREAM_DNS", "127.0.0.11")
HEALTH_CHECK_INTERVAL = int(os.environ.get("HEALTH_CHECK_INTERVAL", "10"))
HEALTH_CHECK_TIMEOUT = int(os.environ.get("HEALTH_CHECK_TIMEOUT", "2"))

# Network discovery
DISCOVERED_NETWORKS = {}
LAST_DISCOVERY_TIME = 0

# Default health check ports by hostname pattern
DEFAULT_HEALTH_PORTS = {
    "ollama-server": 11434,
    "llama-server": 11434,
    "ai": 8080,
    "execution": 8003,
    "identity": 8001,
    "gateway": 11435,
    "rag": 8002,
    "ui": 8080,
    "caddy": 80,
    "redis": 6379,
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


def get_host_ip():
    """Get host's actual IP address (not Docker gateway)."""
    # Use socket to get IP of default route
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        try:
            s.close()
        except Exception:
            pass
    
    # Fallback: try hostname -I
    try:
        import subprocess
        result = subprocess.run(
            ["hostname", "-I"],
            capture_output=True,
            text=True,
            timeout=5
        )
        if result.returncode == 0:
            ips = result.stdout.strip().split()
            if ips:
                return ips[0]
    except Exception:
        pass
    
    return None


def discover_networks():
    """Discover Docker networks and gateway IPs."""
    global DISCOVERED_NETWORKS
    if not DOCKER_AVAILABLE:
        return
    
    try:
        for network in DOCKER_CLIENT.networks.list():
            net_config = network.attrs.get('IPAM', {}).get('Config', [])
            gateway = None
            subnet = None
            for config in net_config:
                if config.get('Gateway'):
                    gateway = config['Gateway']
                if config.get('Subnet'):
                    subnet = config['Subnet']
            
            if gateway:
                DISCOVERED_NETWORKS[network.name] = {
                    'gateway': gateway,
                    'subnet': subnet
                }
                print(f"[dns-sync] Network: {network.name} -> gateway={gateway}, subnet={subnet}", flush=True)
    except Exception as e:
        print(f"[dns-sync] Error discovering networks: {e}", flush=True)


def discover_containers_via_docker():
    """Discover all containers and their IPs via Docker API.
    
    Returns dict of service_name -> {ip, host_networked}
    """
    if not DOCKER_AVAILABLE:
        return {}
    
    containers = {}
    try:
        for container in DOCKER_CLIENT.containers.list(all=True):
            name = container.name.replace('sharedllm_', '')
            if not name:
                continue
            
            # Get container IP from any network
            networks = container.attrs['NetworkSettings']['Networks']
            ip = None
            for _, net_config in networks.items():
                if net_config.get('IPAddress'):
                    ip = net_config['IPAddress']
                    break
            
            # Check if host-networked
            network_mode = container.attrs['HostConfig'].get('NetworkMode', '')
            host_networked = (network_mode == 'host')
            
            containers[name] = {
                'ip': ip,
                'host_networked': host_networked,
                'status': container.status
            }
    except Exception as e:
        print(f"[dns-sync] Error discovering containers: {e}", flush=True)
    
    return containers


def build_dns_records(containers, host_ip):
    """Build DNS records mapping hostnames to IPs.
    
    For host-networked services, use host IP.
    For Docker-networked services, use container IP.
    """
    records = {}
    
    for name, info in containers.items():
        hostname = f"{name}.local" if not name.endswith('.local') else name
        hostname = hostname.lower()
        
        if info['host_networked'] or not info['ip']:
            # Use host IP for host-networked services or if no container IP
            if host_ip:
                records[hostname] = [host_ip]
        elif info['ip']:
            # Use container IP
            records[hostname] = [info['ip']]
    
    return records


# ─── Integration: Service Discovery API ───────────────────────────────────────

def get_service_discovery_data():
    """Get current service discovery data for control plane."""
    with dns_lock:
        records = dict(dns_records)
    with health_lock:
        status = dict(health_status)
    
    services = {}
    for hostname, ips in records.items():
        alive_ips = [ip for ip in ips if status.get((hostname, ip), False)]
        services[hostname] = {
            "ips": ips,
            "alive_ips": alive_ips,
            "healthy": len(alive_ips) > 0
        }
    
    return {
        "services": services,
        "networks": DISCOVERED_NETWORKS,
        "container_count": len(records),
        "last_discovery": LAST_DISCOVERY_TIME,
        "host_ip": get_host_ip()
    }


class DiscoveryAPIHandler(BaseHTTPRequestHandler):
    """HTTP handler for service discovery API."""
    
    def log_message(self, format, *args):
        """Suppress default logging."""
        pass
    
    def do_GET(self):
        """Handle GET requests."""
        parsed = urlparse(self.path)
        path = parsed.path
        
        # Check authentication
        secret = self.headers.get("X-Internal-Secret", "")
        if secret != INTERNAL_SECRET:
            self.send_response(401)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": "Unauthorized"}).encode())
            return
        
        if path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({
                "status": "ok",
                "service": "dns_sync",
                "container_count": len(dns_records)
            }).encode())
        
        elif path == "/api/discovery":
            data = get_service_discovery_data()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(data).encode())
        
        elif path == "/api/discovery/services":
            services = get_service_discovery_data()["services"]
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(services).encode())
        
        elif path == "/api/discovery/alive":
            services = get_service_discovery_data()["services"]
            alive = {k: v for k, v in services.items() if v["healthy"]}
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(alive).encode())
        
        elif path.startswith("/api/discovery/"):
            # Get specific service: /api/discovery/identity.local
            service_name = path.split("/")[-1]
            services = get_service_discovery_data()["services"]
            if service_name in services:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(services[service_name]).encode())
            else:
                self.send_response(404)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": "Service not found"}).encode())
        
        else:
            self.send_response(404)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": "Not found"}).encode())


def start_discovery_api():
    """Start the service discovery HTTP API server."""
    api_port = 8009
    server = HTTPServer(("0.0.0.0", api_port), DiscoveryAPIHandler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    print(f"[dns-sync] Service discovery API listening on port {api_port}", flush=True)
    return server


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
    """Forward query to upstream DNS server with fallback."""
    upstreams = [UPSTREAM_DNS]
    if DISCOVERED_NETWORKS.get('gateway'):
        upstreams.append(DISCOVERED_NETWORKS['gateway'])
    upstreams.append("8.8.8.8")
    
    for upstream in upstreams:
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
            sock.sendto(bytes(query), (upstream, 53))
            resp, _ = sock.recvfrom(512)
            sock.close()
            return resp
        except Exception:
            continue
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
                    continue

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
    print(f"[dns-sync] Docker API available: {DOCKER_AVAILABLE}", flush=True)
    
    # Discover network configuration
    discover_networks()
    
    if not DISCOVERED_NETWORKS.get('gateway'):
        print("[dns-sync] Warning: No network gateway discovered, using default upstream DNS", flush=True)
    
    # Start health checker
    health_thread = threading.Thread(target=health_checker, daemon=True)
    health_thread.start()
    
    # Start DNS server
    dns_thread = threading.Thread(target=dns_server, daemon=True)
    dns_thread.start()
    
    last_records = {}
    last_discovery = time.time()
    discovery_interval = 10  # Discover containers every 10 seconds
    
    while running:
        # Discover networks and containers periodically
        if time.time() - last_discovery > discovery_interval:
            discover_networks()
            host_ip = get_host_ip()
            containers = discover_containers_via_docker()
            records = build_dns_records(containers, host_ip)
            
            if records != last_records:
                with dns_lock:
                    global dns_records
                    dns_records = records
                last_records = records
                print(f"[dns-sync] Discovered {len(containers)} containers, {len(records)} DNS records", flush=True)
            
            last_discovery = time.time()
        
        for _ in range(POLL_INTERVAL):
            if not running:
                break
            time.sleep(1)
    
    print("[dns-sync] Shutting down", flush=True)


if __name__ == "__main__":
    main()
