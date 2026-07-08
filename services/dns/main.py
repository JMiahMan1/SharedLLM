#!/usr/bin/env python3
"""
Minimal DNS Service for SharedLLM

Combines Docker container discovery with DNS resolution.
Listens on host network port 5353 and resolves container names/IPs.
"""

import asyncio
import json
import logging
import os
import socket
import struct

import docker

logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


class ContainerRegistry:
    """Manages container-to-IP mappings"""

    def __init__(self):
        self.containers: dict[str, dict] = {}
        self.lock = asyncio.Lock()

    def update_container(self, name: str, ip: str, hostname: str | None = None):
        """Add or update a container in the registry"""
        self.containers[name] = {
            'ip': ip,
            'hostname': hostname or name,
            'network': 'bridge'
        }
        logger.info(f"Registered container: {name} -> {ip}")

    def remove_container(self, name: str):
        """Remove a container from the registry"""
        if name in self.containers:
            del self.containers[name]
            logger.info(f"Removed container: {name}")

    def get_ip(self, name: str) -> str | None:
        """Get IP for a container name"""
        return self.containers.get(name, {}).get('ip')


class DockerWatcher:
    """Monitors Docker for container changes"""

    def __init__(self, client: docker.DockerClient, registry: ContainerRegistry):
        self.client = client
        self.registry = registry
        self._running = False

    async def start(self):
        """Start watching for container events"""
        self._running = True
        logger.info("Starting Docker watcher...")

        # Initial scan
        await self._sync_containers()

        # Watch for events - use executor to avoid blocking event loop
        await self._watch_events()

    async def _sync_containers(self):
        """Sync all running containers"""
        try:
            containers = self.client.containers.list(filters={'status': 'running'})
            for container in containers:
                await self._register_container(container)
        except Exception as e:
            logger.error(f"Error syncing containers: {e}")

    async def _register_container(self, container):
        """Register a container in the registry"""
        name = container.name
        try:
            # Get container network info
            networks = container.attrs.get('NetworkSettings', {}).get('Networks', {})
            ip = None

            # Try to get IP from sharedllm network first
            for net_name, net_config in networks.items():
                if 'sharedllm' in net_name.lower() or 'bridge' in net_name.lower():
                    ip = net_config.get('IPAddress')
                    if ip:
                        break

            # Fallback to first available IP
            if not ip:
                for net_name, net_config in networks.items():
                    ip = net_config.get('IPAddress')
                    if ip:
                        break

            if ip:
                hostname = container.attrs.get('Config', {}).get('Hostname', name)
                self.registry.update_container(name, ip, hostname)
        except Exception as e:
            logger.error(f"Error registering container {name}: {e}")

    async def _handle_event(self, event: dict):
        """Handle Docker event"""
        action = event.get('Action')
        actor = event.get('Actor', {})
        actor_id = actor.get('ID', '')

        if action in ('create', 'start'):
            try:
                container = self.client.containers.get(actor_id)
                await self._register_container(container)
            except Exception:
                pass
        elif action in ('die', 'remove'):
            try:
                container = self.client.containers.get(actor_id)
                if container.name:
                    self.registry.remove_container(container.name)
            except Exception:
                pass

    async def _watch_events(self):
        """Watch Docker events without blocking the event loop"""
        logger.info("Watcher: starting event loop")
        event_queue = asyncio.Queue()

        def watch_sync():
            """Sync event watcher that puts events in queue"""
            import threading
            logger.info(f"Watcher thread started (thread={threading.current_thread().name})")
            try:
                events = self.client.events(decode=True)
                for event in events:
                    if not self._running:
                        break
                    event_queue.put_nowait(event)
                logger.info("Watcher thread: events loop completed")
            except Exception as e:
                logger.error(f"Error watching Docker events: {e}")
                import traceback
                traceback.print_exc()

        logger.info("Watcher: creating thread task")
        try:
            watcher_task = asyncio.create_task(asyncio.to_thread(watch_sync))
            await asyncio.sleep(0.1)  # Give thread time to start
            logger.info("Watcher: thread task created, continuing")
        except Exception as e:
            logger.error(f"Failed to create watcher thread task: {e}")
            raise

        while self._running:
            try:
                event = await asyncio.wait_for(event_queue.get(), timeout=1.0)
                await self._handle_event(event)
            except TimeoutError:
                continue
            except Exception as e:
                logger.error(f"Error processing Docker event: {e}")
                await asyncio.sleep(1)

        watcher_task.cancel()

    def stop(self):
        """Stop the watcher"""
        self._running = False


class DNSResolver:
    """DNS query resolver"""

    def __init__(self, registry: ContainerRegistry, upstream_dns: str = '8.8.8.8', static_mappings: dict[str, str] | None = None):
        self.registry = registry
        self.upstream_dns = upstream_dns
        self.upstream_client = _DNSClient(upstream_dns)
        self.static_mappings = static_mappings or {}

    async def resolve(self, query: 'DNSQuery') -> list | None:
        """Resolve a DNS query"""
        print(f"DEBUG: Resolver called for: {query.question_name}", flush=True)
        logger.debug(f"Resolver called for: {query.question_name}")
        print("DEBUG: Entering try block", flush=True)
        logger.debug("Entering try block")
        name = query.question_name.rstrip('.')
        print(f"DEBUG: After rstrip, name={name}", flush=True)

        try:
            print(f"DEBUG: Inside try block, name={name}", flush=True)
            logger.debug(f"After try block, name={name}")

            # Check static mappings first (for external hosts like ollama-server.local)
            if name in self.static_mappings:
                ip = self.static_mappings[name]
                logger.debug(f"Found static mapping: {name} -> {ip}")
                return [self._make_a_record(name, ip, 300)]

            # Check if it's a .docker suffix query
            if name.endswith('.docker'):
                container_name = name[:-6]  # Remove .docker
                ip = self.registry.get_ip(container_name)
                if ip:
                    logger.debug(f"Found .docker match: {name} -> {ip}")
                    return [self._make_a_record(name, ip, 300)]
                return None

            # Check registry for exact match
            ip = self.registry.get_ip(name)
            if ip:
                logger.debug(f"Found exact match in registry: {name} -> {ip}")
                return [self._make_a_record(name, ip, 300)]

            # Check hostname match
            for container_name, info in self.registry.containers.items():
                if info.get('hostname') == name:
                    ip = info['ip']
                    logger.debug(f"Found hostname match: {name} -> {ip} (container: {container_name})")
                    return [self._make_a_record(name, ip, 300)]

            # Handle host.docker.internal
            if name == 'host.docker.internal':
                logger.debug("Found host.docker.internal match")
                return [self._make_a_record(name, '172.26.0.1', 300)]

            # Forward to upstream DNS
            logger.debug(f"Forwarding {name} to upstream DNS {self.upstream_dns}")
            logger.debug(f"upstream_client server: {self.upstream_client.server}, port: {self.upstream_client.port}")
            logger.debug(f"About to call upstream_client.resolve for {name}")
            records = await self.upstream_client.resolve(name, query.question_type)
            logger.debug(f"upstream_client.resolve returned for {name}")
            if records:
                logger.debug(f"Upstream DNS returned {len(records)} records for {name}")
                for record in records:
                    if 'ip' in record:
                        logger.debug(f"  Record: {record['name']} -> {record['ip']}")
                    else:
                        logger.debug(f"  Record: {record['name']} -> {record['rdata'].hex()}")
            else:
                logger.debug(f"Upstream DNS returned no records for {name}")
            return records
        except Exception as e:
            logger.error(f"Error in upstream DNS resolution: {e}")
            import traceback
            logger.debug(traceback.format_exc())
            return None

    def _make_a_record(self, name: str, ip: str, ttl: int) -> dict:
        """Create an A record"""
        ip_parts = [int(x) for x in ip.split('.')]
        rdata = struct.pack('!BBBB', *ip_parts)
        return {
            'name': name,
            'rtype': 1,  # A record
            'rclass': 1,
            'ttl': ttl,
            'rdata': rdata
        }


class _DNSClient:
    """DNS client for upstream queries"""

    def __init__(self, server: str, port: int = 53):
        self.server = server
        self.port = port

    async def resolve(self, name: str, qtype: int = 1) -> list | None:
        """Resolve a name via upstream DNS"""
        try:
            logger.debug(f"Sending DNS query for {name} to {self.server}")
            packet = self._build_query(name, qtype)
            result = await asyncio.to_thread(self._send_query, packet)
            logger.debug(f"DNS query result for {name}: {len(result) if result else 0} records")
            return result
        except Exception as e:
            logger.error(f"Upstream DNS resolution failed for {name}: {e}")
            import traceback
            logger.debug(traceback.format_exc())
            return None

    def _build_query(self, name: str, qtype: int) -> bytes:
        """Build a DNS query packet"""
        transaction_id = 0x1234
        flags = 0x0100  # Standard query, recursion desired
        questions = 1
        ancount = 0
        nscount = 0
        arcount = 0

        header = struct.pack('!HHHHHH', transaction_id, flags, questions, ancount, nscount, arcount)

        # Encode name
        name_bytes = b''
        for part in name.split('.'):
            name_bytes += bytes([len(part)]) + part.encode()
        name_bytes += b'\x00'

        # Question section
        question = name_bytes + struct.pack('!HH', qtype, 1)

        return header + question

    def _send_query(self, packet: bytes) -> list | None:
        """Send DNS query and parse response"""
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.settimeout(2)
            sock.sendto(packet, (self.server, self.port))

            response, _ = sock.recvfrom(512)
            return self._parse_response(response)

    def _parse_response(self, data: bytes) -> list:
        """Parse DNS response"""
        if len(data) < 12:
            return []

        _, flags, qdcount, ancount, _, _ = struct.unpack('!HHHHHH', data[:12])

        if flags & 0x8000 == 0:
            return []  # Not a response

        records = []
        offset = 12

        try:
            # Skip questions
            for _ in range(qdcount):
                offset = self._skip_name(data, offset)
                offset += 4  # Skip type and class

            # Parse answers
            for _ in range(ancount):
                if offset >= len(data):
                    break

                name, offset = self._parse_name(data, offset)
                rtype, rclass, ttl, rdlength = struct.unpack('!HHIH', data[offset:offset+10])
                offset += 10

                rdata = data[offset:offset+rdlength]
                offset += rdlength

                if rtype == 1 and rdlength == 4:  # A record
                    ip = '.'.join(str(b) for b in rdata)
                    records.append({
                        'name': name,
                        'rtype': rtype,
                        'rclass': rclass,
                        'ttl': ttl,
                        'rdata': rdata,
                        'ip': ip
                    })
        except Exception as e:
            logger.error(f"Error parsing DNS response: {e}")
            logger.debug(f"Response data: {data.hex()}")
            logger.debug(f"offset: {offset}, len(data): {len(data)}")

        return records

    def _skip_name(self, data: bytes, offset: int) -> int:
        """Skip a DNS name in the packet"""
        while offset < len(data):
            length = data[offset]
            if length == 0:
                offset += 1
                break
            if length & 0xC0 == 0xC0:
                offset += 2
                break
            offset += length + 1
        return offset

    def _parse_name(self, data: bytes, offset: int) -> tuple[str, int]:
        """Parse a DNS name from the packet"""
        names = []
        visited = set()
        original_offset = offset  # Save original position to return

        while offset < len(data):
            length = data[offset]
            if length == 0:
                offset += 1
                break
            if length & 0xC0 == 0xC0:
                pointer = ((length & 0x3F) << 8) | data[offset + 1]
                if pointer in visited:
                    break
                visited.add(pointer)
                offset = pointer
                continue
            offset += 1
            names.append(data[offset:offset+length].decode('ascii', errors='ignore'))
            offset += length

        return '.'.join(names), original_offset + 2


class DNSQuery:
    """Parsed DNS query"""

    def __init__(self, transaction_id: int, question_name: str, question_type: int, question_class: int = 1):
        self.transaction_id = transaction_id
        self.question_name = question_name
        self.question_type = question_type
        self.question_class = question_class


class UDPServer(asyncio.DatagramProtocol):
    """UDP DNS server"""

    def __init__(self, resolver: DNSResolver):
        self.resolver = resolver
        self.transport = None

    def connection_made(self, transport):
        self.transport = transport
        logger.info("UDP DNS server started")

    def datagram_received(self, data, addr):
        logger.debug(f"DNS query received from {addr}, {len(data)} bytes")
        asyncio.ensure_future(self._handle_query(data, addr))

    async def _handle_query(self, data: bytes, addr: tuple[str, int]):
        """Handle a DNS query"""
        try:
            logger.debug(f"Processing DNS query from {addr}")
            query = self._parse_query(data)
            if not query:
                logger.debug(f"Failed to parse query from {addr}")
                return

            logger.debug(f"Resolved {query.question_name} -> {addr}")
            records = await self.resolver.resolve(query)
            if records:
                response = self._build_response(query, records)
                self.transport.sendto(response, addr)
                logger.debug(f"Sent response to {addr}: {len(response)} bytes")
        except Exception as e:
            logger.error(f"Error handling DNS query: {e}")

    def _parse_query(self, data: bytes) -> DNSQuery | None:
        """Parse a DNS query from data"""
        if len(data) < 12:
            return None

        transaction_id, flags, _, _, _, _ = struct.unpack('!HHHHHH', data[:12])

        if flags & 0x8000 != 0:
            return None  # Not a query

        offset = 12
        try:
            name, offset = self._parse_name(data, offset)
            qtype, qclass = struct.unpack('!HH', data[offset:offset+4])
            return DNSQuery(
                transaction_id=transaction_id,
                question_name=name,
                question_type=qtype,
                question_class=qclass
            )
        except Exception:
            return None

    def _build_response(self, query: DNSQuery, records: list) -> bytes:
        """Build a DNS response"""
        transaction_id = query.transaction_id
        flags = 0x8180  # Response, recursion desired, recursion available
        qdcount = 1
        ancount = len(records)

        header = struct.pack('!HHHHHH', transaction_id, flags, qdcount, ancount, 0, 0)

        # Question section
        question = self._encode_name(query.question_name) + struct.pack('!HH', query.question_type, 1)

        # Answer section
        answers = b''
        for record in records:
            name_bytes = self._encode_name(record['name'])
            if record['rtype'] == 1 and len(record['rdata']) == 4:  # A record
                answers += name_bytes + struct.pack('!HHIH', record['rtype'], record['rclass'], record['ttl'], 4) + record['rdata']

        return header + question + answers

    def _encode_name(self, name: str) -> bytes:
        """Encode a DNS name"""
        result = b''
        for part in name.split('.'):
            result += bytes([len(part)]) + part.encode()
        result += b'\x00'
        return result

    def _parse_name(self, data: bytes, offset: int) -> tuple[str, int]:
        """Parse a DNS name"""
        names = []
        visited = set()

        while offset < len(data):
            length = data[offset]
            if length == 0:
                offset += 1
                break
            if length & 0xC0 == 0xC0:
                pointer = ((length & 0x3F) << 8) | data[offset + 1]
                if pointer in visited:
                    break
                visited.add(pointer)
                offset = pointer
                continue
            offset += 1
            names.append(data[offset:offset+length].decode('ascii', errors='ignore'))
            offset += length

        return '.'.join(names), offset


async def health_check(request):
    """Health check endpoint"""
    return web.json_response({'status': 'healthy'})


async def registry_status(request):
    """Registry status endpoint"""
    registry = request.app['registry']
    return web.json_response({
        'containers': len(registry.containers),
        'registry': registry.containers
    })


async def register_container(request):
    """Manually register a container"""
    registry = request.app['registry']
    data = await request.json()
    name = data.get('name')
    ip = data.get('ip')
    hostname = data.get('hostname')

    if not name or not ip:
        return web.json_response({'error': 'name and ip required'}, status=400)

    registry.update_container(name, ip, hostname)
    return web.json_response({'status': 'registered'})


async def main():
    """Main entry point"""
    # Configuration
    port = int(os.environ.get('DNS_PORT', 5353))
    upstream_dns = os.environ.get('UPSTREAM_DNS', '8.8.8.8')

    # Initialize components
    client = docker.from_env()
    registry = ContainerRegistry()
    resolver = DNSResolver(registry, upstream_dns)

    # Parse static DNS mappings from DNS_MAPPINGS env var (fallback)
    mappings_str = os.environ.get('DNS_MAPPINGS', '')
    flat_mappings = {}
    if mappings_str:
        try:
            mappings = json.loads(mappings_str)
            for hostname, ip in mappings.items():
                if isinstance(ip, list):
                    for ip_addr in ip:
                        flat_mappings[hostname] = ip_addr
                else:
                    flat_mappings[hostname] = ip
            logger.info(f"Loaded {len(flat_mappings)} static DNS mappings from DNS_MAPPINGS env var")
        except json.JSONDecodeError as e:
            logger.warning(f"Invalid DNS_MAPPINGS JSON: {e}")

    resolver.static_mappings = flat_mappings

    # Fetch DNS records from Identity service and update periodically
    async def refresh_dns_mappings():
        """Fetch DNS records from Identity service and update resolver."""
        import aiohttp
        identity_url = os.environ.get('IDENTITY_SVC_URL')
        internal_secret = os.environ.get('INTERNAL_SECRET')

        if not identity_url or not internal_secret:
            logger.warning("IDENTITY_SVC_URL or INTERNAL_SECRET not set, using env var mappings only")
            return

        while True:
            try:
                async with aiohttp.ClientSession() as http_session:
                    resp = await http_session.get(
                        f"{identity_url}/api/dns",
                        headers={"X-Internal-Secret": internal_secret}
                    )
                    if resp.status == 200:
                        records = await resp.json()
                        new_mappings = {}
                        for record in records:
                            if not record.get('is_active', True):
                                continue
                            domain = record.get('domain')
                            values = record.get('values', [])
                            if domain and values:
                                if len(values) == 1:
                                    new_mappings[domain] = values[0]
                                else:
                                    # For multiple IPs, use first one (round-robin handled by resolver)
                                    new_mappings[domain] = values[0]
                                    logger.debug(f"DNS record with multiple values: {domain} -> {values}")

                        resolver.static_mappings = new_mappings
                        logger.info(f"Updated {len(new_mappings)} DNS mappings from Identity service")
            except Exception as e:
                logger.warning(f"Failed to fetch DNS records from Identity service: {e}")

            await asyncio.sleep(30)  # Refresh every 30 seconds

    # Start refresh task if Identity service URL is configured
    if os.environ.get('IDENTITY_SVC_URL'):
        asyncio.create_task(refresh_dns_mappings())

    # Start Docker watcher
    watcher = DockerWatcher(client, registry)
    asyncio.create_task(watcher.start())

    # Create HTTP app
    app = web.Application()
    app['registry'] = registry
    app.router.add_get('/health', health_check)
    app.router.add_get('/registry/status', registry_status)
    app.router.add_post('/registry/register', register_container)

    # Run HTTP server in background
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', 8009)
    await site.start()
    logger.info("HTTP API listening on port 8009")

    # Run DNS server
    logger.info("Starting UDP DNS server...")
    try:
        loop = asyncio.get_event_loop()
        transport, _ = await loop.create_datagram_endpoint(
            lambda: UDPServer(resolver),
            local_addr=('0.0.0.0', port)
        )
        logger.info(f"UDP DNS server listening on port {port}")
    except Exception as e:
        logger.error(f"Failed to start UDP DNS server: {e}")
        raise

    try:
        await asyncio.Future()  # Run forever
    except KeyboardInterrupt:
        pass
    finally:
        transport.close()
        watcher.stop()


if __name__ == '__main__':
    from aiohttp import web
    asyncio.run(main())
