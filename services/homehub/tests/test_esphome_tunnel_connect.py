"""ESPHome driver: a registered mobile tunnel is tried before the node's own addresses.

The driver asks ``hub.echo_tunnels.local_endpoint(device_name)`` first; when that yields an
endpoint it is connected to ahead of the stored host, and a tunnel that refuses falls back to
the node's own addresses. Without a tunnel the host is tried first, as before.
"""
from __future__ import annotations

from typing import Any

from aioesphomeapi import DeviceInfo

from services.homehub.core import DeviceUnavailable
from services.homehub.drivers.esphome import ESPHomeDriver
from services.homehub.tests.conftest import make_hub
from services.homehub.tests.test_esphome_hassmic_entities import (
    EVENT_INFOS,
    MAC,
    UPDATE_INFOS,
    FakeClient,
)

DATA = {"host": "192.0.2.127", "port": 6053, "password": "", "noise_psk": "not-a-real-key",
        "device_name": "smart-speaker"}
TUNNEL_ENDPOINT = ("127.0.0.1", 40001)


class StubTunnels:
    """A stand-in for EchoTunnelManager: reports one fixed endpoint (or none) for any node."""

    def __init__(self, endpoint: tuple[str, int] | None) -> None:
        self.endpoint = endpoint
        self.asked: list[str] = []

    def local_endpoint(self, node: str) -> tuple[str, int] | None:
        self.asked.append(node)
        return self.endpoint


def _make_driver() -> tuple[Any, ESPHomeDriver]:
    hub, entry = make_hub("esphome", dict(DATA))
    drv = ESPHomeDriver(hub, entry)
    hub.drivers[entry.entry_id] = drv
    return hub, drv


def _fake_connect(calls: list[tuple[str, int | None]], fail: set[str] | None = None):
    """An async ``_try_connect`` that records (host, port) and returns the fakes' values."""
    fail = fail or set()

    async def fake(host: str, port: int | None = None):
        calls.append((host, port))
        if host in fail:
            raise DeviceUnavailable(f"{host}:{port}: refused")
        return (FakeClient(), DeviceInfo(name="smart-speaker", mac_address=MAC),
                list(EVENT_INFOS) + list(UPDATE_INFOS), [])

    return fake


async def test_tunnel_is_tried_first_and_used(monkeypatch):
    _hub, drv = _make_driver()
    tunnels = StubTunnels(TUNNEL_ENDPOINT)
    drv.hub.echo_tunnels = tunnels
    calls: list[tuple[str, int | None]] = []
    monkeypatch.setattr(drv, "_try_connect", _fake_connect(calls))

    await drv._connect()

    assert calls == [TUNNEL_ENDPOINT]
    assert tunnels.asked == [drv.device_name]
    assert drv.host == "192.0.2.127"  # a tunnel connection leaves the stored host alone


async def test_tunnel_failure_falls_back_to_host(monkeypatch):
    _hub, drv = _make_driver()
    drv.hub.echo_tunnels = StubTunnels(TUNNEL_ENDPOINT)
    calls: list[tuple[str, int | None]] = []
    monkeypatch.setattr(drv, "_try_connect", _fake_connect(calls, fail={"127.0.0.1"}))

    await drv._connect()

    assert calls[0] == TUNNEL_ENDPOINT
    assert calls[1] == ("192.0.2.127", None)
    assert drv.host == "192.0.2.127"


async def test_without_tunnel_host_is_tried_first(monkeypatch):
    _hub, drv = _make_driver()
    tunnels = StubTunnels(None)
    drv.hub.echo_tunnels = tunnels
    calls: list[tuple[str, int | None]] = []
    monkeypatch.setattr(drv, "_try_connect", _fake_connect(calls))

    await drv._connect()

    assert calls == [("192.0.2.127", None)]
    assert tunnels.asked == [drv.device_name]
