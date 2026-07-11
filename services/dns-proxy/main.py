"""Python DNS Proxy - Minimal DNS resolver for Docker containers

Simplified architecture:
- Uses Docker events for real-time container discovery
- Containers self-register via HTTP endpoint
- Maintains local hostname→IP registry
- No polling required
"""

import asyncio
import json
import logging
import os
import socket
import struct
from dataclasses import dataclass, field
from datetime import datetime

# DNS constants
DNS_PORT = 5354  # Use 5354 to avoid conflict with systemd-resolved
DOCKER_SOCKET = "/var/run/docker.sock"
DOCKER_NETWORK = os.environ.get("DOCKER_NETWORK", "sharedllm_default")
SELF_REGISTER_PORT = int(os.environ.get("SELF_REGISTER_PORT", "8080"))
LOGGING_LEVEL = logging.INFO


@dataclass
class ContainerInfo:
    """Container information for DNS resolution"""
    id: str
    name: str
    ip_address: str
    networks: list[str] = field(default_factory=list)
    hostnames: set[str] = field(default_factory=set)
    last_seen: datetime = field(default_factory=datetime.now)


@dataclass
class DNSRequest:
    """Parsed DNS request"""
    id: int
    question_name: str
    question_type: int  # 1=A, 2=CNAME, 5=CNAME, 28=AAAA
    question_class: int = 1  # IN (Internet)


@dataclass
class DNSResponse:
    """DNS response to send back"""
    id: int
    is_authoritative: bool = False
    answers: list[dict] = field(default_factory=list)
    status: int = 0  # 0=NOERROR


class DockerEventClient:
    """Listen for Docker events (container start/stop)"""

    def __init__(self):
        self.listeners: list[callable] = []

    async def register_listener(self, listener):
        """Register a callback for Docker events"""
        self.listeners.append(listener)

    async def listen(self):
        """Listen for Docker events"""
        while True:
            try:
                async with asyncio.Semaphore(1):
                    # Create Unix socket connection
                    reader, writer = await asyncio.open_connection(
                        sock=socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                    )

                    # Build HTTP request
                    request = (
                        b"GET /events?stream=true HTTP/1.1\r\n"
                        b"Host: localhost\r\n"
                        b"Connection: keep-alive\r\n"
                        b"Accept: text/event-stream\r\n"
                        b"\r\n"
                    )

                    writer.write(request)
                    await writer.drain()

                    # Read events
                    while True:
                        line = await reader.readline()
                        if not line:
                            break

                        line = line.decode().strip()
                        if line.startswith("data:"):
                            data = line[5:].strip()
                            if data:
                                try:
                                    event = json.loads(data)
                                    await self._handle_event(event)
                                except json.JSONDecodeError:
                                    pass
            except Exception as e:
                logging.error(f"Docker event listener error: {e}")
                await asyncio.sleep(5)

    async def _handle_event(self, event: dict):
        """Handle Docker event"""
        action = event.get("Action", "")
        actor = event.get("Actor", {})
        attrs = actor.get("Attributes", {})

        # Filter for container events
        if attrs.get("type") == "container":
            container_id = actor.get("ID", "")

            for listener in self.listeners:
                await listener(action, container_id, attrs)


class DockerClient:
    """Query Docker socket for container information"""

    def __init__(self):
        self.containers: dict[str, ContainerInfo] = {}

    async def get_container(self, container_id: str) -> ContainerInfo | None:
        """Get container details by ID"""
        try:
            socket_path = DOCKER_SOCKET

            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.settimeout(5)
            sock.connect(socket_path)

            # Build HTTP request
            request = (
                f"GET /containers/{container_id}/json HTTP/1.1\r\n"
                f"Host: localhost\r\n"
                "Connection: close\r\n"
                "\r\n"
            ).encode()

            sock.sendall(request)

            # Read response
            data = b""
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                data += chunk

            sock.close()

            if data:
                body_start = data.find(b"\r\n\r\n") + 4
                body = data[body_start:]

                if body:
                    container_data = json.loads(body)
                    return self._parse_container(container_data)
        except Exception as e:
            logging.error(f"Failed to get container {container_id}: {e}")

        return None

    def _parse_container(self, container_data: dict) -> ContainerInfo | None:
        """Parse container data"""
        # Get container details
        names = container_data.get("Names", [])
        name = names[0].lstrip("/") if names else "unknown"

        # Get network settings
        net_settings = container_data.get("NetworkSettings", {})
        networks = net_settings.get("Networks", {})

        # Get IP address from target network or first available
        ip_address = ""
        network_names = []

        # Try target network first
        if DOCKER_NETWORK in networks:
            ip = networks[DOCKER_NETWORK].get("IPAddress", "")
            if ip:
                ip_address = ip

        # Fallback to first available
        if not ip_address:
            for net_name, net_config in networks.items():
                network_names.append(net_name)
                ip = net_config.get("IPAddress", "")
                if ip:
                    ip_address = ip
                    break
        else:
            network_names = list(networks.keys())

        # Get HOSTNAMES env var
        hostnames = set()
        for env in container_data.get("Config", {}).get("Env", []):
            if env.startswith("HOSTNAMES="):
                hostnames_str = env.split("=", 1)[1]
                hostnames = {h.strip() for h in hostnames_str.split(",") if h.strip()}
                break

        # Add container name as hostname
        hostnames.add(name)

        container_info = ContainerInfo(
            id=container_data.get("Id", ""),
            name=name,
            ip_address=ip_address,
            networks=network_names,
            hostnames=hostnames
        )

        return container_info




class DNSResolver:
    """Resolve hostnames to IPs using container registry"""

    def __init__(self, docker_client: DockerClient):
        self.docker_client = docker_client
        self.hostname_map: dict[str, str] = {}  # hostname -> IP
        self.upstream_dns = "8.8.8.8"  # Default upstream

    async def refresh_containers(self):
        """Refresh container registry from Docker"""
        containers = await self.docker_client.get_containers()
        self.hostname_map.clear()

        for container in containers:
            # Map container name
            if container.ip_address:
                self.hostname_map[container.name] = container.ip_address

            # Map hostnames from HOSTNAMES env var
            for hostname in container.env_hostnames:
                if container.ip_address:
                    # Handle wildcard patterns (. prefix)
                    if hostname.startswith("."):
                        domain = hostname.lstrip(".")
                        # Map all subdomains to this IP
                        for key in list(self.hostname_map.keys()):
                            if key.endswith(domain):
                                self.hostname_map[key] = container.ip_address
                    else:
                        self.hostname_map[hostname] = container.ip_address

        logging.info(f"Updated hostname map with {len(self.hostname_map)} entries")

    async def resolve(self, hostname: str, qtype: int = 1) -> str | None:
        """Resolve hostname to IP"""
        # Check exact match
        if hostname in self.hostname_map:
            return self.hostname_map[hostname]

        # Check with .local suffix
        if hostname.endswith(".local"):
            base_name = hostname[:-6]
            if base_name in self.hostname_map:
                return self.hostname_map[base_name]

        # Check wildcard patterns
        for pattern, ip in self.hostname_map.items():
            if pattern.startswith("."):
                domain = pattern.lstrip(".")
                if hostname.endswith(domain) or hostname == domain:
                    return ip

        return None

    async def forward_to_upstream(self, hostname: str, qtype: int) -> str | None:
        """Forward to upstream DNS server"""
        try:
            import socket

            # Simple DNS query to upstream
            query = self._build_dns_query(hostname, qtype)

            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.settimeout(2)

            sock.sendto(query, (self.upstream_dns, 53))

            data, addr = sock.recvfrom(1024)
            sock.close()

            return self._parse_dns_response(data)
        except Exception as e:
            logging.error(f"Upstream DNS forward failed: {e}")
            return None

    def _build_dns_query(self, hostname: str, qtype: int) -> bytes:
        """Build DNS query packet"""
        # Header: ID, Flags, QDCount, ANCount, NSCount, ARCount
        header = struct.pack("!HHHHHH", 0, 0x0100, 1, 0, 0, 0)

        # Question: name + type + class
        name_parts = hostname.split(".")
        name_encoded = b""
        for part in name_parts:
            name_encoded += bytes([len(part)]) + part.encode()
        name_encoded += b"\x00"

        question = name_encoded + struct.pack("!HH", qtype, 1)

        return header + question

    def _parse_dns_response(self, data: bytes) -> str | None:
        """Parse DNS response to extract IP address"""
        try:
            # Skip header (12 bytes)
            pos = 12

            # Read question (skip it)
            while data[pos] != 0:
                pos += data[pos] + 1
            pos += 1  # Skip null terminator

            # Read answer
            # Format: name (pointer or label), type, class, ttl, rdlength, rdata
            if data[pos] == 0xc0:  # Pointer
                pos += 2
            else:
                while data[pos] != 0:
                    pos += data[pos] + 1
                pos += 1

            pos += 4  # Skip type, class
            pos += 4  # Skip TTL

            rdlength = struct.unpack("!H", data[pos:pos+2])[0]
            pos += 2

            if rdlength == 4:  # A record (IPv4)
                ip = socket.inet_ntoa(data[pos:pos+4])
                return ip
        except Exception as e:
            logging.error(f"Failed to parse DNS response: {e}")

        return None


class DNSServer:
    """Async DNS server"""

    def __init__(self, resolver: DNSResolver, port: int = DNS_PORT):
        self.resolver = resolver
        self.port = port
        self.running = False

    async def start(self):
        """Start DNS server"""
        self.running = True
        logging.info(f"Starting DNS server on port {self.port}")

        # Create UDP socket
        loop = asyncio.get_running_loop()

        # Refresh containers on startup
        await self.resolver.refresh_containers()

        # Schedule periodic refresh
        asyncio.ensure_future(self._refresh_periodic())

        # Start receiving
        transport, protocol = await loop.create_datagram_endpoint(
            lambda: DNSProtocol(self.resolver),
            local_addr=('0.0.0.0', self.port)
        )

        logging.info(f"DNS server listening on UDP {self.port}")

        # Keep running
        while self.running:
            await asyncio.sleep(1)

    async def stop(self):
        """Stop DNS server"""
        self.running = False
        logging.info("DNS server stopping")

    async def _refresh_periodic(self):
        """Refresh container registry periodically"""
        while self.running:
            await asyncio.sleep(30)  # Refresh every 30 seconds
            if self.running:
                await self.resolver.refresh_containers()


class DNSProtocol(asyncio.DatagramProtocol):
    """Handle DNS UDP packets"""

    def __init__(self, resolver: DNSResolver):
        self.resolver = resolver
        self.transport = None

    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data, addr):
        """Handle incoming DNS query"""
        try:
            if len(data) < 12:
                return

            request_id = struct.unpack("!H", data[0:2])[0]
            flags = struct.unpack("!H", data[2:4])[0]

            if (flags & 0x8000) == 0:  # Not a response
                qdcount = struct.unpack("!H", data[4:6])[0]

                if qdcount > 0:
                    pos = 12
                    name = ""

                    while data[pos] != 0:
                        length = data[pos]
                        pos += 1
                        if length > 0:
                            name += data[pos:pos+length].decode() + "."
                            pos += length

                    pos += 1  # Skip null terminator

                    if pos + 4 <= len(data):
                        qtype = struct.unpack("!H", data[pos:pos+2])[0]

                        # Resolve (fire and forget for DNS)
                        asyncio.ensure_future(self._handle_query(request_id, name, qtype, addr))
        except Exception as e:
            logging.error(f"Error handling DNS request: {e}")

    async def _handle_query(self, request_id: int, name: str, qtype: int, addr: tuple):
        """Handle DNS query resolution"""
        ip = await self.resolver.resolve(name, qtype)

        if ip is None:
            ip = await self.resolver.forward_to_upstream(name, qtype)

        if ip and self.transport:
            response = self._build_response(request_id, name, ip, qtype)
            self.transport.sendto(response, addr)
            logging.debug(f"Served DNS: {name} -> {ip}")

    def _build_response(self, request_id: int, name: str, ip: str, qtype: int) -> bytes:
        """Build DNS response packet"""
        # Header
        header = struct.pack("!HHHHHH",
            request_id,
            0x8180,  # Response, recursion desired, no error
            1,  # QDCount
            1,  # ANCount
            0, 0
        )

        # Question (copy from request)
        name_parts = name.split(".")
        name_encoded = b""
        for part in name_parts:
            name_encoded += bytes([len(part)]) + part.encode()
        name_encoded += b"\x00"

        question = name_encoded + struct.pack("!HH", qtype, 1)

        # Answer
        # Name pointer (2 bytes) + type (2) + class (2) + TTL (4) + rdlength (2) + rdata
        answer = struct.pack("!H", 0xc00c)  # Pointer to name in question
        answer += struct.pack("!HHI", qtype, 1, 300)  # type, class, TTL=300s
        answer += struct.pack("!H", 4)  # rdlength for A record
        answer += socket.inet_aton(ip)  # IP address

        return header + question + answer


async def main():
    """Main entry point"""
    logging.basicConfig(
        level=LOGGING_LEVEL,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    docker_client = DockerClient()
    resolver = DNSResolver(docker_client)
    server = DNSServer(resolver)

    try:
        await server.start()
    except KeyboardInterrupt:
        await server.stop()


if __name__ == "__main__":
    asyncio.run(main())
