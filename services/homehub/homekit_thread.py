"""HomeKit over Thread: pairing and connecting through aiohomekit's CoAP transport.

A Thread accessory announces ``_hap._udp`` with an IPv6 address on the Thread
mesh. The hub reaches it over plain IPv6 + CoAP, routed by a Thread border
router (HomePod mini, Apple TV 4K, a Google/Nest hub, an OpenThread Border
Router). The border router advertises the mesh's prefix in its router
advertisements; the hub's host learns that route only if it accepts route
information options of that length (``accept_ra_rt_info_max_plen`` >= 64, and
``accept_ra = 2`` when IPv6 forwarding is on, as Docker turns it on).

Nothing here waits on a hope: before any CoAP packet is sent the host's
routing table must have a route that covers the accessory's address. If it
does not, the flow fails at once and says why, naming the border routers seen
on the LAN (``_meshcop._udp``) or saying that none was found.

Pairing data is aiohomekit's own CoAP dict (``Connection: CoAP``,
``AccessoryIP``, ``AccessoryPort`` and the keys), so the HomeKit driver loads
it with ``CoAPController.load_pairing`` the way it loads an IP pairing, and
every entity mapping (``homekit_services.py``) is shared with the IP path.
"""
from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import logging
from collections.abc import Awaitable, Callable
from types import SimpleNamespace
from typing import Any

from services.homehub.core import DeviceUnavailable, HubError
from services.homehub.flows import FlowError

log = logging.getLogger("homehub.homekit_thread")

HAP_UDP = "_hap._udp.local."
MESHCOP = "_meshcop._udp.local."
ROUTES_PATH = "/proc/net/ipv6_route"
#: How long the border-router browse listens before giving its answer.
BORDER_ROUTER_BROWSE_S = 3.0
PAIR_TIMEOUT_S = 60.0
CONNECT_TIMEOUT_S = 20.0

ROUTE_HELP = ("Let the hub's host learn the border router's route: set "
              "net.ipv6.conf.<lan interface>.accept_ra_rt_info_max_plen=64 (and accept_ra=2 on that interface, "
              "since Docker turns IPv6 forwarding on), then try again.")


# -- reachability --------------------------------------------------------------------------

def thread_address(raw: Any) -> ipaddress.IPv6Address:
    """The accessory's address, which for a Thread accessory is always IPv6."""
    text = str(raw or "").strip().strip("[]").split("%", 1)[0]
    try:
        addr = ipaddress.ip_address(text)
    except ValueError as exc:
        raise FlowError(f"The Thread accessory announced no usable address ({raw!r})", 400) from exc
    if not isinstance(addr, ipaddress.IPv6Address):
        raise FlowError(f"The accessory announced {addr} for HomeKit over Thread, which is not an IPv6 address; "
                        "Thread accessories are reached over IPv6 only", 400)
    return addr


def ipv6_routes(path: str = ROUTES_PATH) -> list[tuple[ipaddress.IPv6Network, str]]:
    """The kernel's IPv6 routes as ``(network, interface)`` (``/proc/net/ipv6_route``)."""
    out = []
    try:
        with open(path, encoding="ascii") as f:
            lines = f.read().splitlines()
    except OSError as exc:
        raise HubError(f"Cannot read the IPv6 routing table ({path}): {exc}") from exc
    for line in lines:
        parts = line.split()
        if len(parts) < 10:
            continue
        try:
            dest = ipaddress.IPv6Address(int(parts[0], 16))
            plen = int(parts[1], 16)
            out.append((ipaddress.IPv6Network((dest, plen), strict=False), parts[9]))
        except ValueError:
            continue
    return out


def route_to(addr: ipaddress.IPv6Address, routes: list[tuple[ipaddress.IPv6Network, str]]
             ) -> tuple[ipaddress.IPv6Network, str] | None:
    """The most specific route that covers ``addr``, never the default route or loopback."""
    best = None
    for net, dev in routes:
        if net.prefixlen == 0 or dev == "lo" or addr not in net:
            continue
        if best is None or net.prefixlen > best[0].prefixlen:
            best = (net, dev)
    return best


async def browse_border_routers(seconds: float = BORDER_ROUTER_BROWSE_S) -> list[str]:
    """Names of the Thread border routers announcing ``_meshcop._udp`` on the LAN."""
    from zeroconf import ServiceListener
    from zeroconf.asyncio import AsyncServiceBrowser, AsyncZeroconf

    names: set[str] = set()

    class _Collect(ServiceListener):
        def add_service(self, _zc: Any, _type: str, name: str) -> None:
            names.add(name.removesuffix("." + MESHCOP))

        def update_service(self, _zc: Any, _type: str, name: str) -> None:
            names.add(name.removesuffix("." + MESHCOP))

        def remove_service(self, *_a: Any) -> None: ...

    azc = AsyncZeroconf()
    try:
        browser = AsyncServiceBrowser(azc.zeroconf, [MESHCOP], listener=_Collect())
        await asyncio.sleep(seconds)
        await browser.async_cancel()
    finally:
        await azc.async_close()
    return sorted(names)


BorderRouters = Callable[[], Awaitable[list[str]]]


async def require_route(raw_address: Any, *, routes_path: str = ROUTES_PATH,
                        border_routers: BorderRouters | None = None) -> tuple[ipaddress.IPv6Address, str]:
    """Fail fast unless the host can route to the accessory; returns ``(address, interface)``."""
    addr = thread_address(raw_address)
    found = route_to(addr, ipv6_routes(routes_path))
    if found is not None:
        return addr, found[1]
    try:
        routers = await (border_routers or browse_border_routers)()
    except Exception as exc:  # the browse only explains the failure; say it could not look
        log.warning("Thread border router browse failed: %s", exc)
        raise FlowError(f"This host has no route to the Thread network of {addr}, and looking for a Thread border "
                        f"router on the LAN failed ({exc}). {ROUTE_HELP}", 409) from exc
    if not routers:
        raise FlowError(f"No Thread border router was found on the network (nothing announces _meshcop._udp), and "
                        f"this host has no route to {addr}. HomeKit over Thread needs a border router on the same "
                        "LAN as the hub: a HomePod mini, an Apple TV 4K, a Google/Nest hub or an OpenThread Border "
                        "Router.", 409)
    raise FlowError(f"Found Thread border router(s) {', '.join(routers)}, but this host has no route to the Thread "
                    f"network of {addr}. {ROUTE_HELP}", 409)


# -- pairing and connecting ----------------------------------------------------------------

def _description(finding: dict[str, Any], address: str) -> Any:
    from aiohomekit.model import Categories
    from aiohomekit.model.feature_flags import FeatureFlags
    from aiohomekit.model.status_flags import StatusFlags
    from aiohomekit.zeroconf import HomeKitService

    props = finding.get("props") or {}

    def num(key: str, default: int) -> int:
        try:
            return int(props.get(key) or default)
        except (TypeError, ValueError):
            return default

    try:
        category = Categories(num("category", 1))
    except ValueError:
        category = Categories(1)
    port = finding.get("port")
    if not port:
        raise FlowError("The Thread accessory announced no CoAP port", 400)
    return HomeKitService(
        name=str(finding.get("name") or address), id=str(props.get("pairing_id") or (finding.get("ids") or [""])[0]),
        model=str(finding.get("model") or ""), feature_flags=FeatureFlags(num("ff", 0)),
        status_flags=StatusFlags(1), config_num=num("c#", 1), state_num=num("s#", 1), category=category,
        protocol_version=str(props.get("pv") or "1.1"), type=HAP_UDP, address=address, addresses=[address],
        port=int(port))


def _controller(zeroconf_instance: Any = None) -> Any:
    from aiohomekit.characteristic_cache import CharacteristicCacheMemory
    from aiohomekit.controller.coap.controller import CoAPController

    return CoAPController(char_cache=CharacteristicCacheMemory(),
                          zeroconf_instance=zeroconf_instance or SimpleNamespace(zeroconf=None))


async def pair(finding: dict[str, Any], code: str, alias: str, *, routes_path: str = ROUTES_PATH,
               border_routers: BorderRouters | None = None, timeout: float = PAIR_TIMEOUT_S,
               make_discovery: Callable[[Any, Any], Any] | None = None) -> tuple[dict[str, Any], list[Any]]:
    """Pair a Thread accessory with its setup code; returns ``(pairing data, accessories)`` like the IP path."""
    addr, _dev = await require_route(finding.get("host"), routes_path=routes_path, border_routers=border_routers)
    desc = _description(finding, str(addr))
    controller = _controller()
    if make_discovery is None:
        from aiohomekit.controller.coap.discovery import CoAPDiscovery

        make_discovery = CoAPDiscovery
    discovery = make_discovery(controller, desc)
    try:
        try:
            finish = await asyncio.wait_for(discovery.async_start_pairing(alias), timeout)
            pairing = await asyncio.wait_for(finish(code), timeout)
        except TimeoutError as exc:
            raise FlowError(f"The Thread accessory at {addr} did not answer within {timeout:.0f} s. Its border "
                            "router routes to it, but the accessory may be asleep or out of range; wake it (press "
                            "a button) and try again.", 504) from exc
    finally:
        with contextlib.suppress(Exception):
            await discovery.close()
    try:
        accessories = await asyncio.wait_for(pairing.list_accessories_and_characteristics(), timeout)
    finally:
        with contextlib.suppress(Exception):
            await pairing.close()
    data = dict(pairing.pairing_data)
    if data.get("Connection") != "CoAP":
        raise FlowError("The accessory paired, but not over CoAP; it cannot be driven as a Thread accessory", 502)
    return data, list(accessories)


async def load_pairing(entry: Any, zeroconf_instance: Any = None, *, routes_path: str = ROUTES_PATH
                       ) -> tuple[Any, Any]:
    """A started CoAP controller and the entry's pairing; refuses (setup retries) when there is no route."""
    data = dict(entry.data or {})
    if not data.get("AccessoryIP") or not data.get("AccessoryPort"):
        raise HubError("the stored Thread pairing has no AccessoryIP/AccessoryPort")
    try:
        addr = thread_address(data["AccessoryIP"])
    except FlowError as exc:
        raise HubError(str(exc)) from exc
    if route_to(addr, ipv6_routes(routes_path)) is None:
        raise DeviceUnavailable(f"no route to the Thread network of {addr}: is its Thread border router up? "
                                f"{ROUTE_HELP}")
    controller = _controller(zeroconf_instance)
    try:
        await asyncio.wait_for(controller.async_start(), CONNECT_TIMEOUT_S)
    except Exception as exc:  # rediscovery is a nicety: the stored address still works
        log.warning("%s: no _hap._udp browser, using the stored address: %s", getattr(entry, "title", ""), exc)
        controller = _controller()
        await controller.async_start()
    pairing = controller.load_pairing(entry.entry_id, data)
    if pairing is None:
        raise HubError("pairing data is not a CoAP (Thread) pairing")
    return controller, pairing

