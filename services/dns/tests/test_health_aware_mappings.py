"""Health-checked names answer quickly: candidates probed together, stale served."""
import asyncio
import time

from services.dns.tests.test_upstream_timeout import _load_dns


class _Query:
    def __init__(self, name):
        self.question_name = name
        self.question_type = 1


def _resolver(dns, mapping):
    r = dns.DNSResolver(registry=None, upstream_dns="127.0.0.1", static_mappings=mapping)
    r.health_aware = True
    return r


def test_six_candidates_are_probed_together_not_in_turn():
    dns = _load_dns({})
    r = _resolver(dns, {"desk.local": [f"10.0.0.{i}" for i in range(1, 7)]})

    async def slow_probe(ip):
        await asyncio.sleep(0.5)
        return ip == "10.0.0.1"

    r._probe = slow_probe
    t0 = time.monotonic()
    records = asyncio.run(r.resolve(_Query("desk.local")))
    assert time.monotonic() - t0 < 1.2, "in turn this took 3s and resolvers gave up"
    assert [bytes(rec["rdata"]) for rec in records] == [bytes([10, 0, 0, 1])]


def test_an_expired_result_answers_at_once_and_refreshes_behind():
    dns = _load_dns({})
    r = _resolver(dns, {"desk.local": ["10.0.0.1", "10.0.0.2"]})
    calls = []

    async def probe(ip):
        calls.append(ip)
        await asyncio.sleep(0.3)
        return True

    r._probe = probe
    r._health = {"10.0.0.1": (True, 0.0), "10.0.0.2": (False, 0.0)}  # long expired

    async def run():
        t0 = time.monotonic()
        records = await r.resolve(_Query("desk.local"))
        took = time.monotonic() - t0
        await asyncio.sleep(0.5)  # let the background refresh land
        return records, took

    records, took = asyncio.run(run())
    assert took < 0.1, "a stale answer must not wait on a probe"
    assert len(records) == 1  # the cached view: only .1 was up
    assert sorted(calls) == ["10.0.0.1", "10.0.0.2"]
    assert r._health["10.0.0.2"][0] is True
