"""Device registry (services/control_plane/network_devices.py)."""
from __future__ import annotations

import json

from services.control_plane import network_devices as nd
from services.control_plane import network_routes as nr
from services.control_plane import router_monitor as rm
from services.control_plane.router_client import RouterClient
from services.control_plane.router_monitor import device_name

MAC = "02:00:00:00:00:05"


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


def test_registry_create_update(tmp_path):
    store = nd.Store(tmp_path / "router.db")
    now = 1000.0
    store.ensure(MAC, now)
    rec = store.get(MAC)
    assert rec is not None
    assert rec["first_seen"] == now
    assert rec["last_seen"] == now

    store.set_manual(MAC, {"alias": "Laptop", "kind": "laptop", "make": "Apple",
                          "model": "MacBook", "os": "macOS", "owner": "alex", "notes": "mine"})
    rec = store.get(MAC)
    assert rec["alias"] == "Laptop"
    assert rec["kind"] == "laptop" and rec["kind_source"] == "manual"
    assert rec["make"] == "Apple" and rec["make_source"] == "manual"
    assert rec["model"] == "MacBook" and rec["model_source"] == "manual"
    assert rec["os"] == "macOS" and rec["os_source"] == "manual"
    assert rec["owner"] == "alex" and rec["notes"] == "mine"

    # detection never overwrites manual values
    store.apply_detection(MAC, {"kind": "phone", "make": "Samsung", "model": "X", "os": "Android"}, "ua")
    rec = store.get(MAC)
    assert rec["kind"] == "laptop" and rec["kind_source"] == "manual"
    assert rec["make"] == "Apple" and rec["make_source"] == "manual"
    assert rec["model"] == "MacBook" and rec["model_source"] == "manual"
    assert rec["os"] == "macOS" and rec["os_source"] == "manual"

    # empty string clears a manual value
    store.set_manual(MAC, {"alias": ""})
    rec = store.get(MAC)
    assert rec["alias"] is None


def test_device_name_order():
    mac = "02:00:00:00:00:06"
    snap_static = {"hosts": {mac: "static-host"}, "leases": [{"mac": mac, "name": "dhcp-host", "ip": "192.168.10.6"}]}
    snap_dhcp = {"hosts": {}, "leases": [{"mac": mac, "name": "dhcp-host", "ip": "192.168.10.6"}]}
    snap_none = {"hosts": {}, "leases": []}

    assert device_name(mac, snap_static, {mac: {"mac": mac, "alias": "Alias"}})[0] == "Alias"
    assert device_name(mac, snap_static, {mac: {"mac": mac, "make": "Apple", "model": "iPhone"}})[0] == "Apple iPhone"
    assert device_name(mac, snap_static, {mac: {"mac": mac, "kind": "tablet", "make": "Apple"}})[0] == "tablet (Apple)"
    assert device_name(mac, snap_static, {mac: {"mac": mac}})[0] == "static-host"
    assert device_name(mac, snap_dhcp, {mac: {"mac": mac}})[0] == "dhcp-host"
    assert device_name(mac, snap_none, {mac: {"mac": mac}})[0] == mac


def _snap_for(macs):
    leases = [{"mac": m, "ip": f"192.168.10.{10 + i}", "name": "", "expires": 100} for i, m in enumerate(macs)]
    return {
        "system": {"uptime": 1000, "load1": 0.1, "mem_total": 1000, "mem_available": 600},
        "board": {"model": "Test router", "hostname": "router", "version": "25.12"},
        "interfaces": [],
        "leases": leases,
        "hosts": {},
        "pools": [],
        "radios": [],
        "conntrack": {"count": 0, "max": 1},
        "log": [],
    }


def _monitor(tmp_path, monkeypatch, snap, env=None):
    if env is not None:
        monkeypatch.setenv("NETWORK_NEW_DEVICE_NOTICE", env)
    import services.control_plane.network_devices as nd
    store = nd.Store(tmp_path / "router.db")
    nd._store = store
    monkeypatch.setattr(nd, "store", lambda: store)
    r = FakeRedis()
    sent = []
    monkeypatch.setattr(rm, "internet_up", lambda: True)
    monkeypatch.setattr(rm, "collect", lambda client, last_id: snap)
    monkeypatch.setattr(RouterClient, "verify_certificate", lambda self: "ab" * 32)
    m = rm.Monitor(r, lambda: {"url": "https://192.0.2.1", "user": "jarvis", "password": "s3cret", "cert_sha256": None},
                   lambda fp: None, notify=lambda n: sent.append(n) or True)
    return m, store, sent


def test_new_device_notice_once_and_at_most_three(monkeypatch, tmp_path):
    monkeypatch.delenv("NETWORK_NEW_DEVICE_NOTICE", raising=False)
    macs = [f"02:00:00:00:00:{10 + i:02x}" for i in range(5)]
    snap = _snap_for(macs)
    m, _store, sent = _monitor(tmp_path, monkeypatch, snap)
    m.tick(now=1000.0)
    titles = [n["title"] for n in sent]
    assert titles.count("New device on the network") == 3
    assert "New devices on the network" in titles
    group = next(n for n in sent if n["title"] == "New devices on the network")
    assert "2 more devices joined" in group["body"]
    # a second tick adds nothing new
    sent2 = []
    m.notify = lambda n: sent2.append(n) or True
    m.tick(now=1060.0)
    assert not any("New device" in n["title"] for n in sent2)


def test_new_device_notice_carries_link_and_action(monkeypatch, tmp_path):
    monkeypatch.delenv("NETWORK_NEW_DEVICE_NOTICE", raising=False)
    mac = "02:00:00:00:00:10"
    snap = _snap_for([mac])
    m, _store, sent = _monitor(tmp_path, monkeypatch, snap)
    m.tick(now=1000.0)
    notice = next(n for n in sent if n["title"] == "New device on the network")
    assert notice["actions"] and notice["actions"][0]["label"] == "Add details"
    from urllib.parse import unquote

    href = unquote(notice["data"]["link"]["href"])
    assert "device=" in href and mac.lower() in href


def test_new_device_notice_disabled(monkeypatch, tmp_path):
    monkeypatch.setenv("NETWORK_NEW_DEVICE_NOTICE", "0")
    mac = "02:00:00:00:00:11"
    snap = _snap_for([mac])
    m, _store, sent = _monitor(tmp_path, monkeypatch, snap)
    m.tick(now=1000.0)
    assert not any("New device" in n["title"] for n in sent)
    monkeypatch.delenv("NETWORK_NEW_DEVICE_NOTICE", raising=False)

def test_control_plane_device_routes(tmp_path, monkeypatch):
    store = nd.Store(tmp_path / "router.db")
    nd._store = store
    monkeypatch.setattr(nd, "store", lambda: store)

    r = FakeRedis()
    r.kv[rm.STATUS_KEY] = json.dumps({"devices": {}, "radios": []})
    monkeypatch.setattr(nr, "monitor", lambda: type("M", (), {"r": r})())

    mac = "02:00:00:00:00:09"
    assert nr.network_devices()["devices"] == []

    store.ensure(mac, 1000.0, touch=False)
    body = nr.network_devices()["devices"]
    assert len(body) == 1 and body[0]["mac"] == mac

    assert nr.update_network_device(mac, {"alias": "Tablet", "kind": "tablet"})["status"] == "ok"
    assert store.get(mac)["alias"] == "Tablet"

    assert nr.delete_network_device(mac)["status"] == "ok"
    assert store.get(mac) is None


def test_bonjour_model_from_homehub_identifies_a_guest_ipad(monkeypatch, tmp_path):
    mac = "02:00:00:00:00:21"  # a private (randomised) address, as guests' phones use
    snap = _snap_for([mac])
    monkeypatch.setattr(rm.mdns_peers, "fetch", lambda: {"192.168.10.10": {"txt": {"model": "iPad14,1"}}})
    m, store, _sent = _monitor(tmp_path, monkeypatch, snap)
    m.tick(now=1000.0)
    rec = store.get(mac)
    assert (rec["kind"], rec["os"], rec["kind_source"]) == ("tablet", "iPadOS", "mdns")


def test_manual_details_beat_bonjour(monkeypatch, tmp_path):
    mac = "02:00:00:00:00:22"
    snap = _snap_for([mac])
    monkeypatch.setattr(rm.mdns_peers, "fetch", lambda: {"192.168.10.10": {"txt": {"model": "iPad14,1"}}})
    m, store, _sent = _monitor(tmp_path, monkeypatch, snap)
    store.set_manual(mac, {"kind": "phone"})
    m.tick(now=1000.0)
    assert store.get(mac)["kind"] == "phone"
