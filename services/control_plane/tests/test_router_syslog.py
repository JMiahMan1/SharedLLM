"""The router's forwarded log: lookups per device, the log itself, and who may send.

Addresses are documentation ranges and MACs are made up.
"""
from __future__ import annotations

import json
import socket
import time

from services.control_plane import router_monitor as rm
from services.control_plane import router_syslog as rs

ROUTER = "192.0.2.1"
PHONE_IP, PHONE = "192.0.2.50", "02:00:00:00:00:50"
NOW = 1_794_500_000.0


def line(tag: str, msg: str, pri: int = 30) -> str:
    return f"<{pri}>Nov 12 20:51:02 {tag}: {msg}"


def test_lines_from_logd_are_read():
    q = rs.parse(line("dnsmasq[1234]", f"query[A] WWW.Example.com. from {PHONE_IP}"))
    assert q["dns"] == {"type": "A", "domain": "www.example.com", "ip": PHONE_IP}
    assert rs.parse(line("dnsmasq[1234]", "forwarded www.example.com to 198.51.100.53"))["skip"]
    assert rs.parse(line("dnsmasq[1234]", "reply www.example.com is 203.0.113.9"))["skip"]
    wifi = rs.parse(line("hostapd", "phy0-ap0: AP-STA-CONNECTED 02:00:00:00:00:50", pri=29))
    assert wifi == {"level": "notice", "source": "hostapd", "msg": "phy0-ap0: AP-STA-CONNECTED 02:00:00:00:00:50"}
    with_host = rs.parse("<27>Nov  2 03:04:05 router kernel: [123.456] oops")
    assert with_host["level"] == "err" and with_host["source"] == "kernel"
    assert rs.parse("not syslog") is None


def receiver(tmp_path, allowed=(ROUTER,)):
    store = rs.Store(tmp_path / "router.db")
    return rs.Receiver(store, lambda: {PHONE_IP: PHONE}, lambda: set(allowed)), store


def test_lookups_are_counted_per_device_and_hour_and_other_lines_kept(tmp_path):
    rec, store = receiver(tmp_path)
    for _ in range(3):
        rec.accept(ROUTER, line("dnsmasq[1]", f"query[A] video.example.com from {PHONE_IP}"), NOW)
    rec.accept(ROUTER, line("dnsmasq[1]", f"query[AAAA] video.example.com from {PHONE_IP}"), NOW)
    rec.accept(ROUTER, line("dnsmasq[1]", "query[A] other.example.org from 192.0.2.99"), NOW)
    rec.accept(ROUTER, line("dnsmasq[1]", "reply video.example.com is 203.0.113.9"), NOW)
    rec.accept(ROUTER, line("netifd", "Interface 'wan' is now up"), NOW)
    assert rec.drain() == 7
    sites = store.sites(NOW - 3600, None, 10)
    assert sites[0] == {"device": PHONE, "ip": PHONE_IP, "domain": "video.example.com", "lookups": 4, "last": NOW}
    assert {"device": "192.0.2.99", "domain": "other.example.org"}.items() <= sites[1].items()
    assert [s["domain"] for s in store.sites(NOW - 3600, PHONE, 10)] == ["video.example.com"]
    logged = store.log(10, None, None)
    assert [e["msg"] for e in logged] == ["netifd: Interface 'wan' is now up"]
    assert store.log(10, "warning", None) == [] and store.log(10, None, "WAN")[0]["level"] == "info"


def test_only_the_router_may_send(tmp_path):
    rec, store = receiver(tmp_path)
    rec.accept("192.0.2.66", line("netifd", "spoofed"), NOW)
    rec.drain()
    assert store.latest() is None and "192.0.2.66" in rec.refused


def test_old_lines_and_lookups_are_pruned(tmp_path, monkeypatch):
    rec, store = receiver(tmp_path)
    old = NOW - (rs.RETENTION_DAYS + 1) * 86400
    rec.accept(ROUTER, line("dnsmasq[1]", f"query[A] old.example.com from {PHONE_IP}"), old)
    rec.accept(ROUTER, line("netifd", "old line"), old)
    rec.accept(ROUTER, line("netifd", "new line"), NOW)
    rec.drain()
    store.prune(NOW)
    assert store.sites(old - 3600, None, 10) == [] and [e["msg"] for e in store.log(10, None, None)] == ["netifd: new line"]


def test_lines_arrive_over_tcp(tmp_path):
    rec, store = receiver(tmp_path, allowed=("127.0.0.1",))
    server = rs._TCP(("127.0.0.1", 0), rs._Handler)
    server.receiver = rec
    import threading
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with socket.create_connection(server.server_address, timeout=5) as s:
            s.sendall((line("netifd", "one") + "\n" + line("dnsmasq[1]", f"query[A] a.example.com from {PHONE_IP}") + "\n").encode())
        deadline = time.time() + 5
        while rec.q.qsize() < 2 and time.time() < deadline:
            time.sleep(0.02)
        rec.drain()
    finally:
        server.shutdown()
        server.server_close()
    assert store.log(5, None, None)[0]["msg"] == "netifd: one"
    assert store.sites(time.time() - 3600, PHONE, 5)[0]["domain"] == "a.example.com"


def test_devices_and_router_address_come_from_what_jarvis_already_knows():
    class R:
        def get(self, key):
            return json.dumps({"devices": {PHONE: {"name": "phone", "ip": PHONE_IP}, "02:00:00:00:00:51": {"name": "x"}}})

    assert rs.devices_from_status(R()) == {PHONE_IP: PHONE}
    assert ROUTER in rs.router_addresses(lambda: {"url": f"https://{ROUTER}"})
    assert rs.router_addresses(lambda: (_ for _ in ()).throw(RuntimeError("identity starting"))) == set(rs.ALLOW)


def test_the_polled_alert_log_leaves_out_dns_lookups():
    lines = [{"id": 1, "time": 1000, "priority": 30, "msg": "dnsmasq[9]: query[A] a.example.com from 192.0.2.5"},
             {"id": 2, "time": 1000, "priority": 30, "msg": "dnsmasq[9]: reply a.example.com is 203.0.113.1"},
             {"id": 3, "time": 1000, "priority": 27, "msg": "netifd: Interface 'wan' is now down"}]
    assert [e["id"] for e in rm.log_entries(lines)] == [3]
