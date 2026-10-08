"""The stack is IPv4-only: AAAA questions get an immediate empty answer.

Observed: every new connection from a container to Nextcloud, Home Assistant
or Audiobookshelf waited ~4s (sometimes 8s). Lookups ask for A and AAAA; this
resolver forwarded the AAAA, could not encode the reply (A records only), and
sent nothing back, so each AAAA timed out. A records came back in ~60ms.
"""
import asyncio
import struct

import pytest

import services.dns.main as dns


def _query(name: str, qtype: int, tid: int = 0x1234) -> bytes:
    labels = b"".join(bytes([len(p)]) + p.encode() for p in name.split(".")) + b"\x00"
    return struct.pack("!HHHHHH", tid, 0x0100, 1, 0, 0, 0) + labels + struct.pack("!HH", qtype, 1)


class _Transport:
    def __init__(self):
        self.sent: list[bytes] = []

    def sendto(self, data, addr):
        self.sent.append(data)


class _Resolver:
    def __init__(self, records=None):
        self.records = records
        self.calls = 0

    async def resolve(self, query):
        self.calls += 1
        return self.records


def _server(records=None):
    resolver = _Resolver(records)
    server = dns.UDPServer(resolver)
    server.transport = _Transport()
    return server, resolver


def _header(reply: bytes) -> tuple[int, int, int]:
    tid, flags, _qd, ancount, _ns, _ar = struct.unpack("!HHHHHH", reply[:12])
    return tid, flags & 0xF, ancount


def test_an_ipv6_question_is_answered_at_once_with_no_address_and_never_forwarded():
    server, resolver = _server()
    asyncio.run(server._handle_query(_query("abs.sumemail.com", 28), ("127.0.0.1", 5000)))
    assert resolver.calls == 0, "AAAA must not go upstream"
    tid, rcode, ancount = _header(server.transport.sent[0])
    assert (tid, rcode, ancount) == (0x1234, 0, 0)  # NOERROR, no records


def test_an_ipv4_question_still_resolves():
    record = {"name": "abs.sumemail.com", "rtype": 1, "rclass": 1, "ttl": 60, "rdata": bytes([184, 190, 1, 29])}
    server, _ = _server([record])
    asyncio.run(server._handle_query(_query("abs.sumemail.com", 1), ("127.0.0.1", 5000)))
    reply = server.transport.sent[0]
    _, rcode, ancount = _header(reply)
    assert (rcode, ancount) == (0, 1)
    assert reply.endswith(bytes([184, 190, 1, 29]))


def test_an_unresolvable_name_fails_fast_instead_of_going_silent():
    server, _ = _server(None)
    asyncio.run(server._handle_query(_query("nope.invalid", 1), ("127.0.0.1", 5000)))
    _, rcode, ancount = _header(server.transport.sent[0])
    assert (rcode, ancount) == (dns.RCODE_SERVFAIL, 0)


@pytest.mark.parametrize("qtype", [15, 16, 33, 65])  # MX, TXT, SRV, HTTPS
def test_other_record_types_get_an_empty_answer_not_silence(qtype):
    server, resolver = _server()
    asyncio.run(server._handle_query(_query("example.com", qtype), ("127.0.0.1", 5000)))
    assert resolver.calls == 0
    assert _header(server.transport.sent[0])[1:] == (0, 0)


def test_the_answer_count_matches_the_records_actually_encoded():
    """A non-A record in the list used to be counted but not written."""
    a = {"name": "x.test", "rtype": 1, "rclass": 1, "ttl": 60, "rdata": bytes([1, 2, 3, 4])}
    aaaa = {"name": "x.test", "rtype": 28, "rclass": 1, "ttl": 60, "rdata": bytes(16)}
    server, _ = _server()
    reply = server._build_response(dns.DNSQuery(1, "x.test", 1), [a, aaaa])
    assert _header(reply)[2] == 1
