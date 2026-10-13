"""Several upstreams, an answer cache, and a stale answer when all fail.

The LAN upstream drops about one lookup in ten (17/20 answered when measured),
and each drop surfaced in containers as "Temporary failure in name resolution"
for cloud.sumemail.com, ha.sumemail.com and the like.
"""
import asyncio
import time

from services.dns.tests.test_upstream_timeout import _SlowUpstream, _load_dns


def test_a_list_of_upstreams_is_parsed():
    dns = _load_dns({"DNS_UPSTREAM_TIMEOUT": "1.0"})
    client = dns._DNSClient(" 192.168.2.1 , 8.8.8.8 ")
    assert client.servers == ["192.168.2.1", "8.8.8.8"]
    assert client.server == "192.168.2.1"


def test_a_dead_upstream_does_not_hide_a_live_one(monkeypatch):
    dns = _load_dns({"DNS_UPSTREAM_TIMEOUT": "1.0"})
    live = _SlowUpstream(delay=0.0)
    try:
        client = dns._DNSClient("127.0.0.1", port=live.port)
        # One address that never answers alongside the live one.
        client.servers = ["127.0.0.2", "127.0.0.1"]
        t0 = time.time()
        records = asyncio.run(client.resolve("cloud.example.com"))
        assert records and records[0]["ip"] == "1.2.3.4"
        assert time.time() - t0 < 0.9, "the live answer must not wait for the dead upstream"
    finally:
        live.close()


def test_an_answer_is_cached_for_its_ttl():
    dns = _load_dns({"DNS_UPSTREAM_TIMEOUT": "1.0"})
    up = _SlowUpstream(delay=0.0)
    try:
        client = dns._DNSClient("127.0.0.1", port=up.port)
        first = asyncio.run(client.resolve("ha.example.com"))
        second = asyncio.run(client.resolve("HA.example.com"))
    finally:
        up.close()
    assert first and second and second[0]["ip"] == "1.2.3.4"
    assert len(up.queries) == 1
    assert 1 <= second[0]["ttl"] <= 60


def test_a_failed_lookup_serves_the_last_answer():
    dns = _load_dns({"DNS_UPSTREAM_TIMEOUT": "0.3"})
    up = _SlowUpstream(delay=0.0)
    try:
        client = dns._DNSClient("127.0.0.1", port=up.port)
        assert asyncio.run(client.resolve("ha.example.com"))
    finally:
        up.close()
    # Expire it, with the upstream now gone.
    key = ("ha.example.com", 1)
    records, _ = client._cache[key]
    client._cache[key] = (records, time.monotonic() - 1)
    stale = asyncio.run(client.resolve("ha.example.com"))
    assert stale and stale[0]["ip"] == "1.2.3.4"


def test_with_nothing_cached_a_failure_is_none():
    dns = _load_dns({"DNS_UPSTREAM_TIMEOUT": "0.3"})
    client = dns._DNSClient("127.0.0.1", port=1)
    assert asyncio.run(client.resolve("gone.example.com")) is None
    assert client._cache == {}


def test_queries_carry_a_fresh_id():
    dns = _load_dns({})
    client = dns._DNSClient("127.0.0.1")
    ids = {client._build_query("a.example.com", 1)[:2] for _ in range(20)}
    assert len(ids) > 1
