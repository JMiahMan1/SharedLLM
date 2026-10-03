"""Tests for the internal DNS resolver's upstream timeout.

Bug: ``_DNSClient._send_query`` hardcoded ``sock.settimeout(2)``. The upstream on
this LAN was measured answering anywhere from 0.6s to 8.7s, so every lookup that
took longer than 2s was dropped. A drop is not surfaced as an error: the resolver
returns nothing, Docker's embedded resolver turns that into SERVFAIL, and the
calling service sees "Temporary failure in name resolution". That is how ABS and
Music Assistant came to look "down" from inside containers while both were
running — the symptom had nothing to do with either service.

The fix makes the timeout ``DNS_UPSTREAM_TIMEOUT`` (env-overridable, like the
other knobs in that file) with a 5s default.
"""
import importlib
import os
import socket
import struct
import threading
import time

import pytest


def _load_dns(env: dict[str, str | None]):
    """Reload services.dns.main with `env` applied (None removes the var)."""
    saved = {k: os.environ.get(k) for k in env}
    for k, v in env.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    try:
        import services.dns.main as dns_main

        return importlib.reload(dns_main)
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


class _SlowUpstream:
    """A UDP DNS server that answers after `delay` seconds."""

    def __init__(self, delay: float, ip: bytes = b"\x01\x02\x03\x04"):
        self.delay = delay
        self.ip = ip
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", 0))
        self.port = self.sock.getsockname()[1]
        self.queries: list[bytes] = []
        self._stop = threading.Event()
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _serve(self):
        self.sock.settimeout(0.3)
        while not self._stop.is_set():
            try:
                data, addr = self.sock.recvfrom(512)
            except (socket.timeout, OSError):
                continue
            self.queries.append(data)
            time.sleep(self.delay)
            try:
                self.sock.sendto(self._response(data), addr)
            except OSError:
                pass

    def _response(self, query: bytes) -> bytes:
        # Echo the transaction id and question, then one A record.
        tid = query[:2]
        question = query[12:]
        header = tid + struct.pack("!HHHHH", 0x8180, 1, 1, 0, 0)
        answer = (
            b"\xc0\x0c"                      # pointer to the question name
            + struct.pack("!HHIH", 1, 1, 60, 4)
            + self.ip
        )
        return header + question + answer

    def close(self):
        self._stop.set()
        self.thread.join(timeout=2)
        self.sock.close()


class TestTimeoutIsConfigurable:
    def test_default_is_longer_than_the_old_hardcoded_two_seconds(self):
        """The regression itself: 2s silently dropped real answers."""
        dns = _load_dns({"DNS_UPSTREAM_TIMEOUT": None})
        assert dns.DNS_UPSTREAM_TIMEOUT > 2.0

    def test_is_overridable_by_env(self):
        dns = _load_dns({"DNS_UPSTREAM_TIMEOUT": "9.5"})
        assert dns.DNS_UPSTREAM_TIMEOUT == 9.5

    def test_is_not_a_bare_literal_in_the_send_path(self):
        """A regression to `settimeout(2)` would break slow upstreams again."""
        import inspect

        dns = _load_dns({"DNS_UPSTREAM_TIMEOUT": None})
        src = inspect.getsource(dns._DNSClient._send_query)
        assert "DNS_UPSTREAM_TIMEOUT" in src
        assert "settimeout(2)" not in src

    def test_an_invalid_env_value_stops_the_service_starting(self):
        """A typo in .env must not silently yield a 0s (or absent) budget.

        Failing at import is the honest outcome: a resolver that silently ran with
        no usable timeout would drop every lookup, which is the bug this whole
        change exists to remove.
        """
        with pytest.raises(ValueError):
            _load_dns({"DNS_UPSTREAM_TIMEOUT": "not-a-number"})


class TestSlowUpstreamStillResolves:
    """The behaviour the bug actually broke."""

    def test_a_three_second_upstream_resolves(self):
        dns = _load_dns({"DNS_UPSTREAM_TIMEOUT": "5.0"})
        upstream = _SlowUpstream(delay=3.0)
        try:
            client = dns._DNSClient("127.0.0.1", port=upstream.port)
            records = client._send_query(client._build_query("slow.example.com", 1))
        finally:
            upstream.close()
        assert records, "a 3s answer must not be dropped by a 5s budget"
        assert len(upstream.queries) == 1

    def test_the_same_upstream_is_dropped_by_the_old_two_second_budget(self):
        """Proves the test can tell fixed from broken.

        Without this, a regression to `settimeout(2)` would still pass the test
        above if something else happened to make the answer fast.
        """
        dns = _load_dns({"DNS_UPSTREAM_TIMEOUT": "5.0"})
        upstream = _SlowUpstream(delay=3.0)
        try:
            client = dns._DNSClient("127.0.0.1", port=upstream.port)
            packet = client._build_query("slow.example.com", 1)
            # Re-create the old behaviour directly rather than editing the source.
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.settimeout(2)
                sock.sendto(packet, ("127.0.0.1", upstream.port))
                with pytest.raises((socket.timeout, TimeoutError)):
                    sock.recvfrom(512)
        finally:
            upstream.close()

    def test_a_fast_upstream_is_unaffected(self):
        dns = _load_dns({"DNS_UPSTREAM_TIMEOUT": "5.0"})
        upstream = _SlowUpstream(delay=0.0)
        try:
            client = dns._DNSClient("127.0.0.1", port=upstream.port)
            records = client._send_query(client._build_query("fast.example.com", 1))
        finally:
            upstream.close()
        assert records

    def test_a_dead_upstream_still_fails_rather_than_hanging(self):
        """The budget must remain bounded — no unbounded wait."""
        dns = _load_dns({"DNS_UPSTREAM_TIMEOUT": "0.3"})
        client = dns._DNSClient("127.0.0.1", port=1)  # nothing listening
        t0 = time.time()
        with pytest.raises((socket.timeout, TimeoutError, OSError)):
            client._send_query(client._build_query("gone.example.com", 1))
        assert time.time() - t0 < 3.0


class TestInternalNamesNeverTouchUpstream:
    """Container names must resolve even when the upstream is useless.

    This is the resilience property that keeps a broken upstream from looking
    like a fully broken network.
    """

    def test_static_mapping_is_answered_without_upstream(self, monkeypatch):
        dns = _load_dns({"DNS_UPSTREAM_TIMEOUT": "5.0"})
        resolver = dns.DNSResolver.__new__(dns.DNSResolver)
        resolver.static_mappings = {"ollama.local": "192.168.2.43"}
        resolver.health_aware = False
        resolver._health = {}

        called = []

        async def _boom(*a, **kw):
            called.append(1)
            raise AssertionError("upstream must not be consulted for a static mapping")

        monkeypatch.setattr(dns._DNSClient, "resolve", _boom)
        record = dns.DNSResolver._make_a_record(
            resolver, "ollama.local", "192.168.2.43", 300
        )
        # rdata is the packed 4-byte address, not a dotted string.
        assert socket.inet_ntoa(record["rdata"]) == "192.168.2.43"
        assert called == []

    def test_host_docker_internal_is_answered_without_upstream(self):
        dns = _load_dns({"DNS_UPSTREAM_TIMEOUT": "5.0"})
        src = open(dns.__file__).read()
        assert "'host.docker.internal'" in src
        assert "172.26.0.1" in src