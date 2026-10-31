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
import random
import socket
import struct
import time

from aiohttp import web

import docker
from services.common.http import get_client
from services.common.secrets import secret_matches

logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

_BACKGROUND_TASKS: set[asyncio.Task] = set()


def _run_in_background(coro) -> asyncio.Task:
    """Schedule a coroutine and hold a strong reference until it finishes."""
    task = asyncio.ensure_future(coro)
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TASKS.discard)
    return task

# Health-aware resolution for static mappings with multiple IPs (e.g. a LAN
# Ollama host that moves between devices). Only currently-reachable IPs are
# returned, and the answer flips automatically when the available device
# changes. Ports/timeout are overridable via env for other services.
DNS_HEALTH_PORTS = [int(p) for p in os.environ.get("DNS_HEALTH_PORTS", "11434,80,443,8080,8000,9000").split(",") if p.strip()]
DNS_HEALTH_TIMEOUT = float(os.environ.get("DNS_HEALTH_TIMEOUT", "1.0"))
DNS_HEALTH_TTL = float(os.environ.get("DNS_HEALTH_TTL", "15.0"))
DNS_REFRESH_INTERVAL = float(os.environ.get("DNS_REFRESH_INTERVAL", "30.0"))

# How long to wait for an upstream resolver before giving up.
#
# This was a hardcoded 2s socket timeout, which silently dropped every lookup
# whenever the upstream was slower than that, and the upstream on this LAN has been
# measured answering anywhere from 0.6s to 8.7s. A dropped lookup is not reported
# as a failure to the caller: this resolver returns nothing, Docker's embedded
# resolver turns that into SERVFAIL, and the service using it just sees
# "Temporary failure in name resolution". That is how ABS and Music Assistant came
# to look "down" from inside containers while both were running fine.
#
# Overridable so a slow or remote upstream can be tuned without a code change,
# matching the other knobs above. Raising this cannot break resolution; it only
# stops giving up before a slow-but-valid answer arrives.
DNS_UPSTREAM_TIMEOUT = float(os.environ.get("DNS_UPSTREAM_TIMEOUT", "5.0"))


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

    #: Longest wait before reconnecting a Docker event stream that ended.
    RECONNECT_DELAY_CAP_S = 60

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
                for net_config in networks.values():
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

        restarts = 0
        while self._running:
            if watcher_task.done():
                # The Docker event stream ended (daemon restart, socket error).
                # Waiting on the queue would wait forever while containers come
                # and go unregistered, so rescan for anything missed and start a
                # fresh stream, backing off if it keeps ending.
                restarts += 1
                delay = min(self.RECONNECT_DELAY_CAP_S, 2 ** min(restarts, 6))
                logger.warning(f"Docker event stream ended; rescanning and reconnecting in {delay}s")
                await asyncio.sleep(delay)
                await self._sync_containers()
                watcher_task = asyncio.create_task(asyncio.to_thread(watch_sync))
                continue
            try:
                event = await asyncio.wait_for(event_queue.get(), timeout=1.0)
                restarts = 0
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
        self.health_aware = True  # toggled by the dns_failover_enabled setting
        self.health_ports = DNS_HEALTH_PORTS
        self.health_path = os.environ.get("DNS_HEALTH_PATH", "")
        self._health: dict[str, tuple[bool, float]] = {}  # ip -> (is_up, checked_at)
        self._refreshing: set[str] = set()
        self._last_reachable: dict[str, list[str]] = {}  # name -> last answer with a live IP

    async def resolve(self, query: 'DNSQuery') -> list | None:
        """Resolve a DNS query"""
        logger.debug(f"Resolver called for: {query.question_name}")
        name = query.question_name.rstrip('.')

        try:
            # Check static mappings first (for external LAN hosts, e.g. a local Ollama server)
            if name in self.static_mappings:
                entry = self.static_mappings[name]
                ips = entry if isinstance(entry, list) else [entry]
                ttl = 300
                if self.health_aware and len(ips) > 1:
                    # Only return IPs that are currently reachable; the answer
                    # flips automatically when the available device changes.
                    # Checked together, not one after another: six candidates
                    # at ~1s each took longer than a resolver waits, so the
                    # name timed out (seen for jeremiah-home-desktop.local).
                    up = await asyncio.gather(*(self._is_reachable(ip) for ip in ips))
                    reachable = [ip for ip, ok in zip(ips, up, strict=False) if ok]
                    if reachable:
                        self._last_reachable[name] = reachable
                    # None answering: the last address that did, not the whole
                    # pool -- handing out dead candidates is what sent callers
                    # to hosts that refuse the connection.
                    ips = reachable or self._last_reachable.get(name) or ips[:1]
                    # Short-lived so a client's cache follows a switch within
                    # one health interval instead of holding a dead IP 5 min.
                    ttl = max(5, int(DNS_HEALTH_TTL))
                records = [self._make_a_record(name, ip, ttl) for ip in ips if ip]
                logger.debug(f"Found static mapping: {name} -> {ips}")
                return records

            # Check if it's a .docker suffix query
            if name.endswith('.docker'):
                container_name = name[:-len('.docker')]  # Remove '.docker' (7 chars)
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
            records = await self.upstream_client.resolve(name, query.question_type)
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

    async def _is_reachable(self, ip: str) -> bool:
        """Cached reachability for an IP.

        An expired answer is still returned at once and refreshed in the
        background (stale-while-revalidate), so a DNS reply only ever waits on
        a probe the very first time an address is seen.
        """
        now = time.monotonic()
        cached = self._health.get(ip)
        if cached and now - cached[1] < DNS_HEALTH_TTL:
            return cached[0]
        if cached:
            if ip not in self._refreshing:
                self._refreshing.add(ip)
                task = asyncio.ensure_future(self._refresh(ip))
                task.add_done_callback(lambda _t, ip=ip: self._refreshing.discard(ip))
            return cached[0]
        up = await self._probe(ip)
        self._health[ip] = (up, now)
        return up

    async def _refresh(self, ip: str) -> None:
        up = await self._probe(ip)
        self._health[ip] = (up, time.monotonic())

    async def _probe(self, ip: str) -> bool:
        """Probe an IP for liveness. If a health path is configured, do an HTTP
        GET to it; otherwise just attempt a TCP connect to each health port.
        All candidate ports are probed concurrently with a short timeout."""
        ports = [int(p) for p in (self.health_ports or DNS_HEALTH_PORTS)]
        path = (self.health_path or "").strip()

        async def try_port(port: int) -> bool:
            try:
                if path:
                    try:
                        import aiohttp
                        url = f"http://{ip}:{port}{path if path.startswith('/') else '/' + path}"
                        async with get_client() as s, s.get(url, timeout=aiohttp.ClientTimeout(total=DNS_HEALTH_TIMEOUT)) as r:
                            return r.status < 500
                    except ImportError:
                        pass
                _, writer = await asyncio.wait_for(
                    asyncio.open_connection(ip, port), DNS_HEALTH_TIMEOUT
                )
                writer.close()
                return True
            except Exception:
                return False

        results = await asyncio.gather(
            *(try_port(p) for p in ports), return_exceptions=True
        )
        return any(r is True for r in results)


class _DNSClient:
    """Forwards lookups the resolver cannot answer itself.

    ``server`` may list several upstreams ("192.168.2.1,8.8.8.8"). Each query
    goes to all of them at once and the first answer wins, so one slow or
    lossy upstream no longer turns into a timeout for the container asking --
    the LAN upstream drops roughly one lookup in ten. Answers are cached for
    their TTL, and when every upstream fails a recently expired answer is
    served instead of nothing.
    """

    #: Bounds on how long an answer is kept, whatever TTL the upstream gave.
    MIN_TTL = 30
    MAX_TTL = 300
    #: How long "no such name" is remembered.
    NEGATIVE_TTL = 30
    #: How long past expiry an answer may still stand in for a failed lookup.
    STALE_FOR = 3600
    MAX_ENTRIES = 4096

    def __init__(self, server: str, port: int = 53):
        self.servers = [s.strip() for s in str(server).split(',') if s.strip()]
        self.server = self.servers[0] if self.servers else ''
        self.port = port
        self._cache: dict[tuple[str, int], tuple[list, float]] = {}

    async def resolve(self, name: str, qtype: int = 1) -> list | None:
        """Resolve a name via upstream DNS; None when no upstream answered."""
        key = (name.lower(), qtype)
        now = time.monotonic()
        cached = self._cache.get(key)
        if cached and cached[1] > now:
            return self._with_ttl(cached[0], cached[1] - now)
        try:
            records = await asyncio.to_thread(self._send_query, self._build_query(name, qtype))
        except Exception as e:
            if cached and now - cached[1] < self.STALE_FOR:
                logger.info(f"Upstream DNS failed for {name} ({e or type(e).__name__}); serving the last answer")
                return self._with_ttl(cached[0], self.MIN_TTL)
            logger.warning(f"Upstream DNS failed for {name} via {','.join(self.servers)}: {e or type(e).__name__}")
            return None
        self._remember(key, records, now)
        return records

    def _remember(self, key: tuple[str, int], records: list, now: float) -> None:
        ttl = min(max(min(r.get('ttl', 0) for r in records), self.MIN_TTL), self.MAX_TTL) if records else self.NEGATIVE_TTL
        if len(self._cache) >= self.MAX_ENTRIES:
            # Drop whatever expires soonest; the cache only has to stay bounded.
            for old in sorted(self._cache, key=lambda k: self._cache[k][1])[: self.MAX_ENTRIES // 4]:
                del self._cache[old]
        self._cache[key] = (records, now + ttl)

    @staticmethod
    def _with_ttl(records: list, remaining: float) -> list:
        return [{**r, 'ttl': max(1, int(remaining))} for r in records]

    def _build_query(self, name: str, qtype: int) -> bytes:
        """Build a DNS query packet"""
        transaction_id = random.randint(0, 0xFFFF)
        flags = 0x0100  # Standard query, recursion desired
        header = struct.pack('!HHHHHH', transaction_id, flags, 1, 0, 0, 0)

        name_bytes = b''
        for part in name.split('.'):
            if part:
                name_bytes += bytes([len(part)]) + part.encode()
        name_bytes += b'\x00'

        return header + name_bytes + struct.pack('!HH', qtype, 1)

    def _send_query(self, packet: bytes) -> list:
        """Ask every upstream at once; return the first matching answer.

        Raises ``TimeoutError`` when none answers within DNS_UPSTREAM_TIMEOUT.
        """
        addresses = {(s, self.port) for s in self.servers}
        deadline = time.monotonic() + DNS_UPSTREAM_TIMEOUT
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            for address in addresses:
                try:
                    sock.sendto(packet, address)
                except OSError as e:
                    logger.debug(f"Could not send to upstream {address[0]}: {e}")
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError('timed out')
                sock.settimeout(remaining)
                try:
                    response, sender = sock.recvfrom(4096)
                except ConnectionRefusedError:
                    continue  # one upstream is down; another may still answer
                # Ignore strays: an answer must come from an upstream we asked
                # and carry the id we sent.
                if sender in addresses and response[:2] == packet[:2]:
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
        ret_offset = None

        while offset < len(data):
            length = data[offset]
            if length == 0:
                offset += 1
                break
            if length & 0xC0 == 0xC0:
                if ret_offset is None:
                    ret_offset = offset + 2
                pointer = ((length & 0x3F) << 8) | data[offset + 1]
                if pointer in visited:
                    break
                visited.add(pointer)
                offset = pointer
                continue
            offset += 1
            names.append(data[offset:offset+length].decode('ascii', errors='ignore'))
            offset += length

        if ret_offset is None:
            ret_offset = offset

        return '.'.join(names), ret_offset


QTYPE_A = 1
RCODE_SERVFAIL = 2


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
        _run_in_background(self._handle_query(data, addr))

    async def _handle_query(self, data: bytes, addr: tuple[str, int]):
        """Answer one DNS query -- always, and at once when there is nothing to find.

        The stack runs IPv4 only. An AAAA (or any non-A) question used to be
        forwarded and then dropped, because only A records can be encoded and
        a query with nothing to send got no reply at all. Every lookup asks for
        both A and AAAA, so every new connection from a container waited ~4s
        for the AAAA to time out. Now non-A questions get an immediate empty
        answer (NOERROR, no records: "no IPv6 address here"), and an A
        question with no answer gets SERVFAIL instead of silence.
        """
        try:
            logger.debug(f"Processing DNS query from {addr}")
            query = self._parse_query(data)
            if not query:
                logger.debug(f"Failed to parse query from {addr}")
                return

            if query.question_type != QTYPE_A:
                self.transport.sendto(self._build_response(query, []), addr)
                return

            records = await self.resolver.resolve(query)
            response = self._build_response(query, records) if records else self._build_response(query, [], rcode=RCODE_SERVFAIL)
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

    def _build_response(self, query: DNSQuery, records: list, rcode: int = 0) -> bytes:
        """Build a DNS response (A records only; ``rcode`` 0 = NOERROR)."""
        transaction_id = query.transaction_id
        flags = 0x8180 | (rcode & 0xF)  # Response, recursion desired/available, rcode
        qdcount = 1
        records = [r for r in records if r['rtype'] == QTYPE_A and len(r['rdata']) == 4]
        ancount = len(records)

        header = struct.pack('!HHHHHH', transaction_id, flags, qdcount, ancount, 0, 0)

        # Question section
        question = self._encode_name(query.question_name) + struct.pack('!HH', query.question_type, 1)

        # Answer section. Every A record is answered under the name that was
        # asked (CNAME flattening). Upstream answers a CDN-hosted name with a
        # CNAME and then A records owned by the CNAME's target; only the A
        # records are encoded here, so they used to go out under the target's
        # name with nothing linking it to the question, and glibc threw the
        # answer away: auth.docker.io never resolved inside a container, so
        # the control plane's update check failed every run (2026-10-30).
        answers = b''
        name_bytes = self._encode_name(query.question_name)
        for record in records:
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


def _require_internal_secret(request) -> web.StreamResponse | None:
    """Return a 403 response when the caller lacks the internal secret."""
    expected = os.environ.get('INTERNAL_SECRET', '')
    provided = request.headers.get('X-Internal-Secret', '')
    if not expected or not secret_matches(provided, expected):
        return web.json_response({'error': 'Unauthorized'}, status=403)
    return None


async def registry_status(request):
    """Registry status endpoint"""
    denied = _require_internal_secret(request)
    if denied is not None:
        return denied
    registry = request.app['registry']
    return web.json_response({
        'containers': len(registry.containers),
        'registry': registry.containers
    })


async def register_container(request):
    """Manually register a container"""
    # Registry answers are consulted BEFORE upstream DNS; an unauthenticated
    # write here would let any container spoof names for the whole stack.
    denied = _require_internal_secret(request)
    if denied is not None:
        return denied
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
    def _normalize_dns_entry(entry) -> list[str]:
        """Flatten a dns_mappings entry (str | list | dict) into a clean IP list."""
        if isinstance(entry, str):
            return [entry.strip()] if entry.strip() else []
        if isinstance(entry, list):
            return [str(v).strip() for v in entry if str(v).strip()]
        if isinstance(entry, dict):
            vals = entry.get("values")
            if isinstance(vals, list):
                return [str(v).strip() for v in vals if str(v).strip()]
            ip = entry.get("ip")
            if isinstance(ip, str):
                return [ip.strip()] if ip.strip() else []
            if isinstance(ip, list):
                return [str(v).strip() for v in ip if str(v).strip()]
        return []

    async def refresh_dns_mappings():
        """Fetch DNS mappings + failover settings from Identity and update resolver.

        The source of truth is the `dns_mappings` global setting, the same store
        the UI (DnsManagementPanel via gateway /api/dns) edits. Each host may carry
        several IPs; the resolver returns only the reachable ones when health-aware
        failover is enabled."""
        identity_url = os.environ.get('IDENTITY_SVC_URL')
        internal_secret = os.environ.get('INTERNAL_SECRET')

        if not identity_url or not internal_secret:
            logger.warning("IDENTITY_SVC_URL or INTERNAL_SECRET not set, using env var mappings only")
            return

        while True:
            try:
                async with get_client() as http_session:
                    # DNS mappings (UI-backed store)
                    resp = await http_session.get(
                        f"{identity_url}/api/settings/dns_mappings",
                        headers={"X-Internal-Secret": internal_secret}
                    )
                    if resp.status == 200:
                        item = await resp.json()
                        raw = item.get("value", "{}") if isinstance(item, dict) else "{}"
                        try:
                            mappings = json.loads(raw) if isinstance(raw, str) else raw
                        except (json.JSONDecodeError, TypeError):
                            mappings = {}
                        new_mappings = {}
                        for host, entry in (mappings or {}).items():
                            if not isinstance(host, str) or host.startswith("__"):
                                continue
                            values = _normalize_dns_entry(entry)
                            if values:
                                new_mappings[host] = values
                                if len(values) > 1:
                                    logger.debug(f"DNS mapping with multiple values: {host} -> {values}")
                        resolver.static_mappings = new_mappings
                        logger.info(f"Updated {len(new_mappings)} DNS mappings from Identity service")

                    # Failover config (health-aware DNS)
                    resp = await http_session.get(
                        f"{identity_url}/api/settings",
                        headers={"X-Internal-Secret": internal_secret}
                    )
                    if resp.status == 200:
                        settings = {item["key"]: item["value"] for item in await resp.json()}
                        resolver.health_aware = str(
                            settings.get("dns_failover_enabled", "true")
                        ).lower() in ("1", "true", "yes", "on")
                        if ports := settings.get("dns_health_ports"):
                            resolver.health_ports = [p.strip() for p in ports.split(",") if p.strip()]
                        if path := settings.get("dns_health_path", "").strip():
                            resolver.health_path = path
            except Exception as e:
                logger.warning(f"Failed to fetch DNS records from Identity service: {e}")

            await asyncio.sleep(DNS_REFRESH_INTERVAL)  # Refresh interval (env-configurable)

    # Start refresh task if Identity service URL is configured
    if os.environ.get('IDENTITY_SVC_URL'):
        _run_in_background(refresh_dns_mappings())

    # Start Docker watcher
    watcher = DockerWatcher(client, registry)
    _run_in_background(watcher.start())

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
        loop = asyncio.get_running_loop()
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
