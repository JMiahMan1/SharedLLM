"""Static mappings as the DNS panel stores them: hosts that move and services on several machines.

The alpaca / Ollama box (``jeremiah-home-desktop.local``) is listed with every
address it has had on every network it joins; only the ones that answer are
handed out. A service can also run on more than one machine at once, so every
answering address is given, in the order listed.
"""
import asyncio
import json
import socket
import struct

import pytest

from services.dns.tests.test_upstream_timeout import _load_dns

ALPACA = "jeremiah-home-desktop.local"
# The live entry's shape (Identity ``dns_mappings``), as the UI writes it.
ALPACA_ENTRY = {
    "ip": "192.168.2.43", "record_type": "A", "ttl": 300, "is_active": True,
    "created_at": "", "updated_at": "2026-09-18T13:47:25.572835Z",
    "values": ["192.168.2.43", "192.168.2.46", "192.168.2.44", "192.168.2.201", "192.168.4.179", "192.168.1.216"],
}


class _Query:
    def __init__(self, name, qtype=1):
        self.question_name = name
        self.question_type = qtype


def _ips(records):
    return [socket.inet_ntoa(bytes(r["rdata"])) for r in records or []]


@pytest.fixture
def dns():
    return _load_dns({})


def _resolver(dns, mappings, up=None, upstream=None):
    r = dns.DNSResolver(registry=dns.ContainerRegistry(), upstream_dns="127.0.0.1", static_mappings=mappings)
    r.health_aware = True
    live = set(up or ())

    async def probe(ip):
        return ip in live

    async def upstream_resolve(name, qtype=1):
        ip = (upstream or {}).get(name)
        return [r._make_a_record(name, ip, 120)] if ip else None

    r._probe = probe
    r.upstream_client.resolve = upstream_resolve
    return r, live


def _ask(r, name):
    return asyncio.run(r.resolve(_Query(name)))


def test_the_live_alpaca_entry_answers_only_the_address_that_is_up(dns):
    r, _ = _resolver(dns, {ALPACA: ALPACA_ENTRY}, up={"192.168.2.43"})
    assert _ips(_ask(r, ALPACA)) == ["192.168.2.43"]


def test_when_the_box_moves_networks_the_answer_follows_it(dns):
    r, live = _resolver(dns, {ALPACA: ALPACA_ENTRY}, up={"192.168.2.43"})

    async def run():
        first = await r.resolve(_Query(ALPACA))
        live.clear()
        live.add("192.168.4.179")  # same machine, now on the other network
        r._health.clear()  # next health interval
        return first, await r.resolve(_Query(ALPACA))

    first, second = asyncio.run(run())
    assert _ips(first) == ["192.168.2.43"]
    assert _ips(second) == ["192.168.4.179"]


def test_a_service_on_several_machines_gets_every_live_one_in_listed_order(dns):
    r, _ = _resolver(dns, {ALPACA: ALPACA_ENTRY}, up={"192.168.1.216", "192.168.2.43"})
    assert _ips(_ask(r, ALPACA)) == ["192.168.2.43", "192.168.1.216"]


def test_an_address_taken_out_of_the_list_is_never_handed_out_again(dns):
    r, live = _resolver(dns, {ALPACA: ["10.0.0.1", "10.0.0.2"]}, up={"10.0.0.1"})

    async def run():
        await r.resolve(_Query(ALPACA))  # 10.0.0.1 remembered as the last that answered
        r.static_mappings = {ALPACA: ["10.0.0.2", "10.0.0.3"]}  # edited in the UI
        live.clear()  # nothing up right now
        r._health.clear()
        return await r.resolve(_Query(ALPACA))

    assert _ips(asyncio.run(run())) == ["10.0.0.2"]


def test_a_record_switched_off_in_the_panel_is_not_answered(dns):
    entry = {**ALPACA_ENTRY, "is_active": False}
    r, _ = _resolver(dns, {ALPACA: entry}, up={"192.168.2.43"})
    assert _ask(r, ALPACA) is None


def test_names_match_whatever_their_case(dns):
    r, _ = _resolver(dns, {"Jeremiah-Home-Desktop.local": "192.168.2.43"})
    assert _ips(_ask(r, ALPACA.upper())) == ["192.168.2.43"]


def test_a_mistyped_value_is_skipped_not_fatal(dns):
    r, _ = _resolver(dns, {"nas.local": {"values": ["192.168.2.300", "nas", "192.168.2.10"]}})
    assert _ips(_ask(r, "nas.local")) == ["192.168.2.10"]
    r2, _ = _resolver(dns, {"nas.local": ["not-an-ip"]})
    assert _ask(r2, "nas.local") is None


def test_the_records_own_ttl_is_used_and_health_checked_answers_stay_short(dns):
    r, _ = _resolver(dns, {"one.local": {"values": ["10.0.0.9"], "ttl": 60},
                           ALPACA: ALPACA_ENTRY}, up={"192.168.2.43"})
    assert [x["ttl"] for x in _ask(r, "one.local")] == [60]
    assert all(x["ttl"] <= max(5, int(dns.DNS_HEALTH_TTL)) for x in _ask(r, ALPACA))


def test_with_failover_off_every_listed_address_is_given(dns):
    r, _ = _resolver(dns, {ALPACA: ALPACA_ENTRY}, up=set())
    r.health_aware = False
    assert _ips(_ask(r, ALPACA)) == ALPACA_ENTRY["values"]


class TestAliases:
    """CNAME records from the DNS panel (they used to fail as "not an IP")."""

    def test_an_alias_to_a_mapped_host_answers_with_its_live_address(self, dns):
        r, _ = _resolver(dns, {
            ALPACA: ALPACA_ENTRY,
            "ollama.home": {"record_type": "CNAME", "values": [ALPACA], "ttl": 300},
        }, up={"192.168.2.43"})
        assert _ips(_ask(r, "ollama.home")) == ["192.168.2.43"]

    def test_an_alias_to_an_internet_name_goes_upstream(self, dns):
        r, _ = _resolver(dns, {"docs.home": {"record_type": "CNAME", "values": ["docs.example.com."]}},
                         upstream={"docs.example.com": "93.184.216.34"})
        assert _ips(_ask(r, "docs.home")) == ["93.184.216.34"]

    def test_an_alias_loop_gives_up_instead_of_hanging(self, dns):
        r, _ = _resolver(dns, {
            "a.home": {"record_type": "CNAME", "values": ["b.home"]},
            "b.home": {"record_type": "CNAME", "values": ["a.home"]},
        })
        assert _ask(r, "a.home") is None

    def test_an_alias_answer_goes_out_under_the_asked_name(self, dns):
        r, _ = _resolver(dns, {ALPACA: "192.168.2.43",
                               "ollama.home": {"record_type": "CNAME", "values": [ALPACA]}})
        server = dns.UDPServer(r)
        reply = server._build_response(dns.DNSQuery(1, "ollama.home", 1), _ask(r, "ollama.home"))
        name = b"\x06ollama\x04home\x00"
        assert reply.count(name) == 2  # the question and the answer
        assert reply.endswith(socket.inet_aton("192.168.2.43"))


def test_the_stored_shape_keeps_every_address(dns):
    """What the Identity refresh hands over is normalized on assignment, list or dict."""
    r = dns.DNSResolver(registry=dns.ContainerRegistry(), upstream_dns="127.0.0.1")
    r.static_mappings = json.loads(json.dumps({ALPACA: ALPACA_ENTRY, "old.local": ["10.0.0.1", "10.0.0.2"]}))
    assert r.static_mappings[ALPACA]["values"] == ALPACA_ENTRY["values"]
    assert r.static_mappings["old.local"]["values"] == ["10.0.0.1", "10.0.0.2"]


def test_the_service_never_reads_mappings_from_its_environment():
    """Identity is the authority; a stale .env must not put back a removed address."""
    import inspect

    import services.dns.main as main
    assert "DNS_MAPPINGS" not in inspect.getsource(main.main).replace("seeded once from DNS_MAPPINGS", "")


# --- End to end: real probes, a real UDP server, a real query --------------

def _query_packet(name, tid=0x4242):
    labels = b"".join(bytes([len(p)]) + p.encode() for p in name.split(".")) + b"\x00"
    return struct.pack("!HHHHHH", tid, 0x0100, 1, 0, 0, 0) + labels + struct.pack("!HH", 1, 1)


def _walk(reply, ancount):
    offset = 12
    while reply[offset]:
        offset += reply[offset] + 1
    offset += 5  # root label, qtype, qclass
    ips = []
    for _ in range(ancount):
        offset += 2 if reply[offset] & 0xC0 == 0xC0 else _name_len(reply, offset)
        rtype, _cls, _ttl, rdlen = struct.unpack("!HHIH", reply[offset:offset + 10])
        offset += 10
        if rtype == 1:
            ips.append(socket.inet_ntoa(reply[offset:offset + rdlen]))
        offset += rdlen
    return ips


def _name_len(data, offset):
    start = offset
    while data[offset]:
        offset += data[offset] + 1
    return offset + 1 - start


def test_end_to_end_only_the_listening_machine_is_answered(dns):
    """Real TCP probes: 127.0.0.1 listens on the health port; 192.0.2.1
    (TEST-NET-1, never routed) does not. A real UDP query gets 127.0.0.1."""

    async def run():
        listener = await asyncio.start_server(lambda r, w: w.close(), "127.0.0.1", 0)
        port = listener.sockets[0].getsockname()[1]
        resolver = dns.DNSResolver(registry=dns.ContainerRegistry(), upstream_dns="127.0.0.1",
                                   static_mappings={ALPACA: {"values": ["192.0.2.1", "127.0.0.1"]}})
        resolver.health_ports = [port]
        loop = asyncio.get_running_loop()
        transport, _ = await loop.create_datagram_endpoint(lambda: dns.UDPServer(resolver), local_addr=("127.0.0.1", 0))
        server_addr = transport.get_extra_info("sockname")
        try:
            client = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            client.setblocking(False)
            client.sendto(_query_packet(ALPACA), server_addr)
            reply = await asyncio.wait_for(loop.sock_recv(client, 4096), timeout=dns.DNS_HEALTH_TIMEOUT + 2)
            client.close()
            return reply
        finally:
            transport.close()
            listener.close()

    reply = asyncio.run(run())
    assert reply[:2] == b"\x42\x42"
    assert _walk(reply, struct.unpack("!H", reply[6:8])[0]) == ["127.0.0.1"]
