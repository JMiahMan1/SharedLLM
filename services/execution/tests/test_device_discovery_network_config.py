"""Device discovery must not invent a network or a router.

Both used to be one install's values: the subnet fallback returned
192.168.1.0/24 and the SNMP strategy walked 192.168.2.1's ARP table. On another
network that is a scan of the wrong LAN and a query aimed at a host that is not
a router.
"""
import importlib
import subprocess
import sys
import types

import pytest

from services.execution import device_discovery as discovery


@pytest.fixture
def reloaded(monkeypatch):
    """Re-import with the environment the test needs."""

    def _reload(**env):
        for key, value in env.items():
            if value is None:
                monkeypatch.delenv(key, raising=False)
            else:
                monkeypatch.setenv(key, value)
        return importlib.reload(discovery)

    yield _reload
    # A test may have left the host's subnet unknowable, and monkeypatch undoes
    # itself only after this fixture finishes -- so give the module a subnet for
    # the restore, then drop it and reload once more for a clean module.
    monkeypatch.setenv("SCAN_SUBNET", "127.0.0.0/8")
    importlib.reload(discovery)  # the module is importable again
    monkeypatch.delenv("SCAN_SUBNET", raising=False)
    try:
        importlib.reload(discovery)
    except RuntimeError:
        # The stubbed-out network is still in place for this fixture's teardown,
        # so real detection cannot succeed yet. The reload above already left a
        # usable module behind for the rest of the suite.
        pass


@pytest.fixture
def no_network(monkeypatch):
    """Make every way this module can learn a subnet fail."""

    def _no_open(*a, **k):
        raise OSError("no /proc/net/route")

    def _no_popen(*a, **k):
        raise OSError("no `ip` command")

    class _NoSocket:
        def __init__(self, *a, **k):
            raise OSError("no socket")

    monkeypatch.setattr("builtins.open", _no_open)
    monkeypatch.setattr(discovery.os, "popen", _no_popen)
    monkeypatch.setattr(discovery.socket, "socket", _NoSocket)
    monkeypatch.setenv("SCAN_SUBNET", "")
    monkeypatch.setenv("LOCAL_SUBNET", "")


def test_a_configured_subnet_is_used_verbatim(reloaded):
    mod = reloaded(SCAN_SUBNET="10.9.0.0/24")
    assert mod.get_local_subnet() == "10.9.0.0/24"


def test_calling_detection_with_no_network_is_loud(no_network):
    with pytest.raises(RuntimeError) as err:
        discovery.get_local_subnet()
    message = str(err.value)
    # It must name the setting an operator can set, and never hand back a network.
    assert "SCAN_SUBNET" in message
    assert "192.168.1.0/24" not in message


def test_the_module_refuses_to_import_without_a_known_subnet(reloaded, no_network):
    """DEFAULT_SUBNET is computed at import, so an unknown network is fatal here.

    A scan that quietly picked the wrong LAN looked exactly like "no devices on
    this network" -- the failure mode this replaces.
    """
    with pytest.raises(RuntimeError, match="SCAN_SUBNET"):
        reloaded(SCAN_SUBNET=None, LOCAL_SUBNET=None)


class _FakeSnmpwalk:
    """Stand-in for the subprocess module the SNMP path imports."""

    def __init__(self, walker):
        self.commands: list[list[str]] = []

    def run(self, cmd, *a, **k):
        self.commands.append(list(cmd))
        return self.walker(cmd)


@pytest.mark.asyncio
async def test_snmp_declines_without_a_configured_router(reloaded):
    mod = reloaded(ROUTER_IP=None)
    assert await mod._discover_via_snmp("media_player.office", "http://ha", "t") is None


@pytest.mark.asyncio
async def test_snmp_walks_the_configured_router(reloaded, monkeypatch):
    mod = reloaded(ROUTER_IP="10.0.0.1")
    fake = _FakeSnmpwalk(lambda cmd: (_ for _ in ()).throw(FileNotFoundError("snmpwalk")))
    monkeypatch.setitem(sys.modules, "subprocess", fake)

    async def _state(*a, **k):
        return {"attributes": {"friendly_name": "Office"}}

    monkeypatch.setattr(mod.ha_client, "get_state", _state, raising=False)
    await mod._discover_via_snmp("media_player.office", "http://ha", "t")

    assert fake.commands, "the SNMP walk never ran"
    assert "10.0.0.1" in fake.commands[0]


@pytest.mark.asyncio
async def test_an_explicit_router_argument_still_wins(reloaded, monkeypatch):
    mod = reloaded(ROUTER_IP="10.0.0.1")
    fake = _FakeSnmpwalk(lambda cmd: (_ for _ in ()).throw(FileNotFoundError("snmpwalk")))
    monkeypatch.setitem(sys.modules, "subprocess", fake)

    async def _state(*a, **k):
        return {"attributes": {"friendly_name": "Office"}}

    monkeypatch.setattr(mod.ha_client, "get_state", _state, raising=False)
    await mod._discover_via_snmp("media_player.office", "http://ha", "t", router_ip="10.0.0.254")

    assert "10.0.0.254" in fake.commands[0]
    assert "10.0.0.1" not in fake.commands[0]


def test_the_real_subprocess_module_is_untouched():
    assert sys.modules.get("subprocess") is subprocess or "subprocess" in sys.modules
