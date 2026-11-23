"""mDNS device identity: what devices announce about themselves, kept in memory for the admin network view.

Every address and name here is made up (192.168.10.x, no real device names).
"""
from __future__ import annotations

import time

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from services.homehub.api import build_app
from services.homehub.auth import Auth
from services.homehub.core import Hub
from services.homehub.discovery import MDNS_IDENTITY_EXPIRE_S, DiscoveryManager
from services.homehub.runtime import Runtime
from services.homehub.store import Store

SECRET = "internal-secret"


class FakeRuntime(Runtime):
    """The real Runtime, except that entries are recorded instead of driven."""

    def __init__(self, hub, store):
        super().__init__(hub, store)
        self.started: list[str] = []
        self.stopped: list[str] = []

    def start_entry(self, entry):
        self.started.append(entry.entry_id)

    async def stop_entry(self, entry_id):
        self.stopped.append(entry_id)


@pytest.fixture
def env(tmp_path):
    store = Store(str(tmp_path / "hub.db"), Fernet.generate_key().decode())
    hub = Hub(time_zone="Etc/GMT+7")
    hub.store = store
    runtime = FakeRuntime(hub, store)

    async def notifier(payload):
        return True

    mgr = DiscoveryManager(hub, store, notifier=notifier)
    runtime.discovery = mgr
    return hub, store, runtime, mgr


@pytest.fixture
def client(env):
    _hub, store, runtime, mgr = env
    app = build_app(runtime, Auth(store, SECRET))
    return TestClient(app), mgr


def auth_headers():
    return {"Authorization": f"Bearer {SECRET}"}


def test_apple_device_info_model_recorded(env):
    _hub, _store, _rt, mgr = env
    mgr.handle_mdns("_device-info._tcp.local.", "MadeUpPad._device-info._tcp.local.", "192.168.10.21", 0,
                    {b"model": b"iPad13,4"}, "madeup-pad.local.")
    entry = mgr.identity["192.168.10.21"]
    assert entry["txt"]["model"] == "iPad13,4"
    assert "MadeUpPad" in entry["names"]
    assert entry["host"] == "madeup-pad.local"
    assert "_device-info._tcp" in entry["services"]
    assert isinstance(entry["last"], float)


def test_googlecast_md_fn_recorded(env):
    _hub, _store, _rt, mgr = env
    mgr.handle_mdns("_googlecast._tcp.local.", "MadeUpCast._googlecast._tcp.local.", "192.168.10.22", 8009,
                    {b"id": b"11112222333344445555666677778888", b"fn": b"Den Speaker", b"md": b"Chromecast Ultra"},
                    "madeup-cast.local.")
    entry = mgr.identity["192.168.10.22"]
    assert entry["txt"]["md"] == "Chromecast Ultra"
    assert entry["txt"]["fn"] == "Den Speaker"


def test_two_services_of_one_address_merged(env):
    _hub, _store, _rt, mgr = env
    mgr.handle_mdns("_airplay._tcp.local.", "MadeUpTV._airplay._tcp.local.", "192.168.10.23", 7000,
                    {b"model": b"AppleTV6,2", b"manufacturer": b"Apple Inc."}, "madeup-tv.local.")
    mgr.handle_mdns("_companion-link._tcp.local.", "MadeUpTV._companion-link._tcp.local.", "192.168.10.23", 49152,
                    {}, "madeup-tv.local.")
    entry = mgr.identity["192.168.10.23"]
    assert entry["txt"]["model"] == "AppleTV6,2"
    assert entry["txt"]["manufacturer"] == "Apple Inc."
    assert "_airplay._tcp" in entry["services"]
    assert "_companion-link._tcp" in entry["services"]
    assert "MadeUpTV" in entry["names"]
    assert len(mgr.identity) == 1


def test_ipv6_only_skipped(env):
    _hub, _store, _rt, mgr = env
    mgr.handle_mdns("_device-info._tcp.local.", "MadeUpPhone._device-info._tcp.local.", "fd00::10", 0,
                    {b"model": b"iPhone15,2"}, "madeup-phone.local.")
    assert mgr.identity == {}


def test_24h_expiry_and_1000_cap(env):
    _hub, _store, _rt, mgr = env
    mgr.handle_mdns("_device-info._tcp.local.", "OldPad._device-info._tcp.local.", "192.168.10.40", 0,
                    {b"model": b"iPad1,1"}, "old-pad.local.")
    mgr.identity["192.168.10.40"]["last"] = time.time() - MDNS_IDENTITY_EXPIRE_S - 5
    mgr.handle_mdns("_device-info._tcp.local.", "FreshPad._device-info._tcp.local.", "192.168.10.41", 0,
                    {b"model": b"iPad2,1"}, "fresh-pad.local.")
    assert "192.168.10.40" not in mgr.identity
    assert "192.168.10.41" in mgr.identity

    mgr.identity = {f"192.168.10.{i}": {"names": [f"Dev{i}"], "host": f"dev-{i}.local.", "services": ["_http._tcp"],
                                        "txt": {}, "last": float(i)} for i in range(1, 1002)}
    mgr._prune_identity(10_000.0)
    assert len(mgr.identity) == 1000
    assert "192.168.10.1" not in mgr.identity
    assert "192.168.10.1001" in mgr.identity


def test_identity_only_types_never_create_findings(env):
    _hub, _store, _rt, mgr = env
    for stype, name, txt in [
        ("_device-info._tcp.local.", "MadeUpPad._device-info._tcp.local.", {b"model": b"iPad13,4"}),
        ("_companion-link._tcp.local.", "MadeUpPhone._companion-link._tcp.local.", {}),
        ("_airplay._tcp.local.", "MadeUpTV._airplay._tcp.local.", {b"model": b"AppleTV6,2"}),
        ("_raop._tcp.local.", "MadeUpSpeaker._raop._tcp.local.", {b"am": b"AudioAccessory5,1"}),
        ("_ipp._tcp.local.", "MadeUpPrinter._ipp._tcp.local.", {b"ty": b"MadeUp Printer"}),
        ("_printer._tcp.local.", "MadeUpPrinter._printer._tcp.local.", {b"usb_MDL": b"MP100"}),
        ("_spotify-connect._tcp.local.", "MadeUpSpk._spotify-connect._tcp.local.", {}),
        ("_sonos._tcp.local.", "MadeUpSonos._sonos._tcp.local.", {}),
        ("_smb._tcp.local.", "MadeUpMac._smb._tcp.local.", {}),
        ("_workstation._tcp.local.", "MadeUpWS._workstation._tcp.local.", {}),
        ("_amzn-wplay._tcp.local.", "MadeUpEcho._amzn-wplay._tcp.local.", {}),
    ]:
        mgr.handle_mdns(stype, name, "192.168.10.50", 7000, txt, "madeup.local.")
    assert mgr.findings == {}
    assert mgr.list() == []
    assert "192.168.10.50" in mgr.identity


def test_mdns_identity_route_admin_only_and_shape(client):
    c, mgr = client
    assert c.get("/api/network/mdns").status_code == 401
    assert c.get("/api/network/mdns", headers={"Authorization": "Bearer nope"}).status_code == 401

    mgr.handle_mdns("_device-info._tcp.local.", "MadeUpPad._device-info._tcp.local.", "192.168.10.60", 0,
                    {b"model": b"iPad13,4"}, "madeup-pad.local.")
    r = c.get("/api/network/mdns", headers=auth_headers())
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 1
    assert "192.168.10.60" in body["devices"]
    entry = body["devices"]["192.168.10.60"]
    assert entry["txt"]["model"] == "iPad13,4"
    assert "MadeUpPad" in entry["names"]
    assert entry["host"] == "madeup-pad.local"
    assert "_device-info._tcp" in entry["services"]
    assert isinstance(entry["last"], (int, float))


def test_the_browser_watches_the_identity_types_too(monkeypatch):
    import asyncio

    from services.homehub import discovery

    seen = {}

    class FakeBrowser:
        def __init__(self, zc, types, handlers):
            seen["types"] = types

    class FakeZeroconf:
        zeroconf = object()

    import zeroconf.asyncio as zca
    monkeypatch.setattr(zca, "AsyncServiceBrowser", FakeBrowser)
    mgr = discovery.DiscoveryManager.__new__(discovery.DiscoveryManager)
    monkeypatch.setattr(discovery.DiscoveryManager, "zeroconf_factory", staticmethod(lambda: FakeZeroconf()))
    asyncio.run(mgr._start_mdns())
    assert set(discovery.MDNS_TYPES) <= set(seen["types"])
    assert "_device-info._tcp.local." in seen["types"]
