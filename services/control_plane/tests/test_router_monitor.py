"""Router monitoring alerts (services/control_plane/router_monitor.py).

Snapshots here are made up but have the shape collect() builds from the
router's ubus replies.
"""
import json

import pytest

from services.control_plane import router_monitor as rm
from services.control_plane.router_client import RouterClient, RouterError

SERVER = "aa:00:00:00:00:01"
TV = "aa:00:00:00:00:02"


def snap(t, server_bytes=0, tv_bytes=0, busy=0, active=0, uptime=1000, log=(), leases_used=10):
    """A router with one 2.4 GHz access point and two clients at fixed link rates."""
    return {
        "system": {"uptime": uptime, "load1": 0.1, "mem_total": 1000, "mem_available": 600},
        "board": {"model": "Test router", "hostname": "router", "version": "25.12"},
        "interfaces": [{"name": "wan", "up": True, "uptime": 50, "proto": "static", "device": "wan", "ipv4": []}],
        "leases": [{"mac": SERVER, "ip": "192.168.2.205", "name": "server", "expires": 100},
                   {"mac": TV, "ip": "192.168.2.160", "name": "", "expires": 100}],
        "hosts": {TV: "LivingRoomTv"},
        "pools": [{"network": "devices", "size": 150, "used": leases_used}],
        "radios": [{
            "device": "wl0-ap0", "phy": "wl0", "ssid": "Home-Devices", "channel": 4, "mhz": 2427,
            "band": "2.4 GHz", "active_ms": active, "busy_ms": busy,
            "clients": [
                {"mac": SERVER, "signal": -45, "inactive_ms": 0, "connected_s": 100,
                 "rx_bytes": 0, "tx_bytes": server_bytes, "rx_kbps": 72200, "tx_kbps": 19500},
                {"mac": TV, "signal": -60, "inactive_ms": 0, "connected_s": 100,
                 "rx_bytes": 0, "tx_bytes": tv_bytes, "rx_kbps": 65000, "tx_kbps": 65000},
            ],
        }],
        "conntrack": {"count": 1000, "max": 15360},
        "log": list(log),
    }


def run_minutes(state, minutes, server_mb_per_min, busy_pct, tv_mb_per_min=1):
    """Feed one snapshot a minute; returns every notice and the last loads."""
    prev, notices, loads = None, [], []
    for m in range(minutes + 1):
        s = snap(m * 60, server_bytes=int(m * server_mb_per_min * 1e6), tv_bytes=int(m * tv_mb_per_min * 1e6),
                 busy=int(m * 60000 * busy_pct / 100), active=m * 60000)
        loads = rm.channel_load(prev, s, 60.0)
        notices += rm.airtime_notices(state, loads, s)
        prev = s
    return notices, loads


def test_a_slow_device_downloading_nonstop_is_named_once():
    state = {}
    # 80 MB a minute over a 19.5 Mbit/s link is about 55% of the airtime.
    notices, loads = run_minutes(state, 8, server_mb_per_min=80, busy_pct=75)
    assert [n["title"] for n in notices] == ["server is crowding the 2.4 GHz Wi-Fi"]
    assert "server (192.168.2.205)" in notices[0]["body"] and "Home-Devices" in notices[0]["body"]
    assert notices[0]["data"]["url"] == "/admin/network"
    top = max(loads[0]["clients"], key=lambda c: c["share_pct"])
    assert top["mac"] == SERVER and 50 < top["share_pct"] < 60
    # Still hogging: no repeat.
    more, _ = run_minutes(state, 8, server_mb_per_min=80, busy_pct=75)
    assert more == []


def test_a_busy_channel_with_no_one_to_blame_is_congestion():
    state = {}
    notices, _ = run_minutes(state, 12, server_mb_per_min=1, busy_pct=85)
    assert [n["title"] for n in notices] == ["2.4 GHz Wi-Fi is congested"]


def test_a_calm_channel_re_arms_the_alert():
    state = {}
    run_minutes(state, 8, server_mb_per_min=80, busy_pct=75)
    run_minutes(state, 8, server_mb_per_min=1, busy_pct=10)
    again, _ = run_minutes(state, 8, server_mb_per_min=80, busy_pct=75)
    assert len(again) == 1


def test_a_device_that_keeps_reconnecting_is_reported_once_a_day():
    state = {}
    lines = [{"id": i, "time": 1000 + i * 120, "priority": 5,
              "msg": f"hostapd: wl0-ap0: AP-STA-CONNECTED {TV} auth_alg=open"} for i in range(6)]
    notices = rm.flap_notices(state, snap(0, log=lines), now=1000 + 6 * 120)
    assert [n["title"] for n in notices] == ["LivingRoomTv keeps dropping off Wi-Fi"]
    assert "6 times" in notices[0]["body"]
    more = [{"id": 10 + i, "time": 2000 + i * 60, "priority": 5,
             "msg": f"AP-STA-CONNECTED {TV} auth_alg=open"} for i in range(6)]
    assert rm.flap_notices(state, snap(0, log=more), now=2400) == []


def test_internet_down_then_back_with_how_long():
    state = {}
    assert rm.internet_notices(state, False, 1000) == []
    down = rm.internet_notices(state, False, 1060)
    assert [n["title"] for n in down] == ["The internet is down"]
    assert rm.internet_notices(state, False, 1120) == []
    back = rm.internet_notices(state, True, 1000 + 18 * 60)
    assert [n["title"] for n in back] == ["The internet is back"]
    assert "18 minutes" in back[0]["body"]


def test_router_unreachable_after_three_checks_and_restart_detected():
    state = {}
    assert rm.router_notices(state, snap(0), 0) == []
    for t in (60, 120):
        assert rm.router_notices(state, None, t, "router unreachable: ConnectTimeout") == []
    lost = rm.router_notices(state, None, 180, "router unreachable: ConnectTimeout")
    assert [n["title"] for n in lost] == ["Jarvis can't reach the router"]
    back = rm.router_notices(state, snap(0, uptime=30), 300)
    assert [n["title"] for n in back] == ["Jarvis can reach the router again", "The router restarted"]


def test_pools_connections_and_memory_alert_once_and_clear():
    state = {}
    full = snap(0, leases_used=140)
    assert [n["title"] for n in rm.capacity_notices(state, full)] == ['Network "devices" is nearly out of addresses']
    assert rm.capacity_notices(state, full) == []
    assert rm.capacity_notices(state, snap(0, leases_used=100)) == []
    assert len(rm.capacity_notices(state, full)) == 1
    tight = snap(0)
    tight["system"]["mem_available"] = 50
    tight["conntrack"] = {"count": 14000, "max": 15360}
    titles = [n["title"] for n in rm.capacity_notices(state, tight)]
    assert "The router is low on memory" in titles and "The router's connection table is nearly full" in titles


def test_static_lease_names_win_and_pools_count_only_their_range():
    dhcp = {
        "cfg1": {".type": "host", "name": "tv", "mac": ["AA:00:00:00:00:02"]},
        "cfg2": {".type": "host", "name": "two-macs", "mac": "aa:00:00:00:00:03 aa:00:00:00:00:04"},
        "devices": {".type": "dhcp", ".name": "devices", "interface": "devices", "start": "30", "limit": "10"},
        "wan": {".type": "dhcp", ".name": "wan", "interface": "wan", "ignore": "1"},
    }
    assert rm._static_hosts(dhcp) == {TV: "tv", "aa:00:00:00:00:03": "two-macs", "aa:00:00:00:00:04": "two-macs"}
    interfaces = [{"interface": "devices", "ipv4-address": [{"address": "192.168.2.1", "mask": 24}]}]
    leases = [{"ipaddr": "192.168.2.30"}, {"ipaddr": "192.168.2.39"}, {"ipaddr": "192.168.2.40"}, {"ipaddr": "192.168.2.10"}]
    assert rm._pools(dhcp, interfaces, leases) == [{"network": "devices", "size": 10, "used": 2}]


class FakeRedis:
    def __init__(self):
        self.kv, self.lists, self.hashes = {}, {}, {}

    def get(self, k):
        return self.kv.get(k)

    def set(self, k, v):
        self.kv[k] = v

    def lpush(self, k, v):
        self.lists.setdefault(k, []).insert(0, v)

    def ltrim(self, k, a, b):
        self.lists[k] = self.lists.get(k, [])[a:b + 1]

    def lrange(self, k, a, b):
        return self.lists.get(k, [])[a:b + 1]

    def hincrby(self, k, f, n):
        h = self.hashes.setdefault(k, {})
        h[f] = h.get(f, 0) + n

    def hgetall(self, k):
        return self.hashes.get(k, {})

    def expire(self, k, s):
        pass

    def pipeline(self):
        return self

    def execute(self):
        pass


def test_the_monitor_stores_status_log_and_usage_and_hides_the_password(monkeypatch):
    r = FakeRedis()
    sent = []
    pins = []
    snaps = iter([snap(0, server_bytes=0, busy=0, active=0,
                       log=[{"id": 5, "time": 1, "priority": 3, "msg": "kernel: something failed"}]),
                  snap(60, server_bytes=10_000_000, busy=30000, active=60000)])
    monkeypatch.setattr(rm, "internet_up", lambda: True)
    monkeypatch.setattr(rm, "collect", lambda client, last_id: next(snaps))
    monkeypatch.setattr(RouterClient, "verify_certificate", lambda self: "ab" * 32)
    m = rm.Monitor(r, lambda: {"url": "https://192.168.2.1", "user": "jarvis", "password": "s3cret", "cert_sha256": None},
                   pins.append, notify=lambda n: sent.append(n) or True)
    first = m.tick(now=1000)
    assert first["connected"] and pins == ["ab" * 32]
    second = m.tick(now=1060)
    assert second["radios"][0]["busy_pct"] == 50.0
    assert "s3cret" not in json.dumps(second) and "s3cret" not in r.get(rm.STATUS_KEY)
    assert json.loads(r.lrange(rm.LOG_KEY, 0, 0)[0])["level"] == "err"
    assert sum(r.hgetall(next(iter(r.hashes))).values()) > 0
    assert sent == []


def test_not_configured_still_watches_the_internet(monkeypatch):
    r = FakeRedis()
    monkeypatch.setattr(rm, "internet_up", lambda: False)
    sent = []
    m = rm.Monitor(r, lambda: None, lambda fp: None, notify=lambda n: sent.append(n) or True)
    assert m.tick(now=0)["configured"] is False
    m.tick(now=60)
    assert [n["title"] for n in sent] == ["The internet is down"]


def test_a_changed_certificate_never_gets_the_password(monkeypatch):
    posted = []
    monkeypatch.setattr("services.control_plane.router_client.certificate_fingerprint", lambda url, timeout: "11" * 32)
    monkeypatch.setattr("services.control_plane.router_client.requests.post", lambda *a, **k: posted.append(k))
    client = RouterClient("https://192.168.2.1", "jarvis", "s3cret", pin="22" * 32)
    with pytest.raises(RouterError, match="certificate changed"):
        client.call("system", "info")
    assert posted == []


def test_an_expired_session_logs_in_again(monkeypatch):
    replies = iter([
        {"error": {"code": -32002, "message": "Access denied"}},
        {"result": [0, {"ubus_rpc_session": "fresh"}]},
        {"result": [0, {"uptime": 5}]},
    ])
    sessions = []

    class Resp:
        status_code = 200

        def __init__(self, body):
            self.body = body

        def json(self):
            return self.body

    def post(url, json=None, timeout=None, verify=None):
        sessions.append(json["params"][0])
        return Resp(next(replies))

    monkeypatch.setattr("services.control_plane.router_client.certificate_fingerprint", lambda url, timeout: "11" * 32)
    monkeypatch.setattr("services.control_plane.router_client.requests.post", post)
    client = RouterClient("https://192.168.2.1", "jarvis", "pw", pin="11" * 32)
    client.session = "stale"
    assert client.call("system", "info") == {"uptime": 5}
    # The stale session is refused, a new login follows, then the call is retried.
    assert sessions == ["stale", "0" * 32, "fresh"]
