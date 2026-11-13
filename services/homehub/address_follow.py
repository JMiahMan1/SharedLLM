"""Following a device to its new address when the router gives it a new lease.

An integration set up by IP (a camera's ``mjpeg_url``, a ``host``, a ``url``)
keeps that IP. When DHCP hands the device another one, the entry keeps talking
to the old address, which may by then belong to something else entirely (the
3D printer's camera pointed at a WiZ bulb for a day). Discovery's own "moved"
check only covers findings it can match by MAC, and only offered the update.

Here, every housekeeping pass (discovery's, once a minute):

1. **Learn.** An entry that works (loaded, with entities that answer) whose
   device has no MAC learns the MAC the neighbour table shows at its address,
   recorded on its device as a ``mac`` connection. Only while it works: the
   address of a device that stopped answering may already be someone else's.
2. **Follow.** An entry whose device has a MAC now seen at a different address
   gets that address in every place it keeps one (``ADDRESS_KEYS``, in its data
   or options) and is reloaded. Admins get one notice saying it happened.

MACs that answer for several addresses (an extender or bridge, ``shared_macs``)
never count. A host name follows the device by itself and is left alone.
"""
from __future__ import annotations

import ipaddress
import logging
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit, urlunsplit

log = logging.getLogger("homehub.address_follow")


def _norm(mac: str | None) -> str | None:
    """A MAC as twelve lowercase hex digits: the neighbour table and the registry write them differently."""
    from services.homehub.discovery import norm_mac

    return norm_mac(mac) if mac else None


def _colons(mac: str) -> str:
    return ":".join(mac[i:i + 2] for i in range(0, 12, 2))

#: Where entries keep an address: a bare host, or the host of a URL.
ADDRESS_KEYS = ("host", "url", "mjpeg_url", "still_image_url", "stream_url")


def _ip(text: str) -> str | None:
    try:
        addr = ipaddress.ip_address(text)
    except ValueError:
        return None
    return None if addr.is_loopback or addr.is_unspecified else str(addr)


def _host_of(value: str) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    if "://" in raw:
        return urlsplit(raw).hostname
    return raw.rsplit(":", 1)[0] if raw.count(":") == 1 else raw


def addresses(entry: Any) -> dict[str, tuple[str, str]]:
    """``{key: (where, ip)}`` for each address the entry keeps as an IP (``where`` is data or options)."""
    out: dict[str, tuple[str, str]] = {}
    for where in ("options", "data"):  # an options override wins, as in the drivers
        src = getattr(entry, where) or {}
        for key in ADDRESS_KEYS:
            if key in out or not src.get(key):
                continue
            ip = _ip(_host_of(str(src[key])) or "")
            if ip:
                out[key] = (where, ip)
    return out


def with_new_ip(value: str, new_ip: str) -> str:
    """``value`` (a host, host:port or URL) pointing at ``new_ip`` instead, the rest unchanged."""
    raw = str(value)
    if "://" not in raw:
        port = raw.rsplit(":", 1)[1] if raw.count(":") == 1 else None
        return f"{new_ip}:{port}" if port else new_ip
    parts = urlsplit(raw)
    netloc = new_ip if ":" not in new_ip else f"[{new_ip}]"
    if parts.port:
        netloc += f":{parts.port}"
    if parts.username is not None:
        auth = parts.username + (f":{parts.password}" if parts.password is not None else "")
        netloc = f"{auth}@{netloc}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def _devices(hub: Any, entry: Any) -> list[Any]:
    return [d for d in hub.devices.values() if entry.entry_id in (d.config_entries or [])]


def _macs(device: Any) -> set[str]:
    return {m for t, v in (device.connections or []) if t == "mac" and (m := _norm(str(v)))}


def working(hub: Any, entry: Any) -> bool:
    """Loaded, and at least one of its entities answers."""
    if entry.state != "loaded" or entry.disabled_by:
        return False
    states = [hub.states.get(e.entity_id) for e in hub.entities.values()
              if e.config_entry_id == entry.entry_id and not e.disabled_by]
    return any(s is not None and s.state not in ("unavailable", "unknown") for s in states)


@dataclass
class Move:
    entry_id: str
    title: str
    old_ip: str
    new_ip: str
    mac: str


def plan(hub: Any, neighbours: dict[str, str], proxied: set[str]) -> tuple[list[tuple[Any, str]], list[Move]]:
    """What to learn (``(device, mac)``) and which entries moved, from the neighbour table (ip -> mac)."""
    proxied = {m for p in proxied if (m := _norm(p))}
    neighbours = {ip: m for ip, mac in neighbours.items() if (m := _norm(mac))}
    by_mac: dict[str, set[str]] = {}
    for ip, mac in neighbours.items():
        if mac not in proxied:
            by_mac.setdefault(mac, set()).add(ip)
    learn: list[tuple[Any, str]] = []
    moves: list[Move] = []
    for entry in hub.config_entries.values():
        if entry.disabled_by or entry.source == "ignore":
            continue
        ips = {ip for _where, ip in addresses(entry).values()}
        if len(ips) != 1:
            continue  # no IP, or several different ones: nothing to follow
        ip = ips.pop()
        devices = _devices(hub, entry)
        if len(devices) != 1:
            continue  # a hub or bridge entry with many devices has no one address to follow
        device = devices[0]
        macs = _macs(device)
        if not macs:
            mac = neighbours.get(ip)
            if mac and mac not in proxied and working(hub, entry):
                learn.append((device, mac))
            continue
        seen = set().union(*(by_mac.get(m, set()) for m in macs))
        if ip in seen or len(seen) != 1:
            continue  # still where the entry says, not seen, or ambiguous
        new_ip = seen.pop()
        mac = next(m for m in macs if new_ip in by_mac.get(m, ()))
        moves.append(Move(entry.entry_id, entry.title, ip, new_ip, _colons(mac)))
    return learn, moves


def lost(hub: Any, neighbours: dict[str, str]) -> bool:
    """Whether a device the hub follows by MAC stopped answering and is nowhere in the table."""
    seen = {m for v in neighbours.values() if (m := _norm(v))}
    for entry in hub.config_entries.values():
        if entry.disabled_by or entry.source == "ignore" or not addresses(entry):
            continue
        devices = _devices(hub, entry)
        if len(devices) == 1 and (macs := _macs(devices[0])) and not macs & seen and not working(hub, entry):
            return True
    return False


def learn_mac(hub: Any, device: Any, mac: str) -> None:
    mac = _colons(_norm(mac) or mac)  # the registry's own spelling, as HA's devices have it
    device.connections = [*(device.connections or []), ["mac", mac]]
    if hub.store is not None:
        hub.store.save_device(device)
    log.info("learned %s's MAC %s while it answers at its address", device.display_name, mac)


def apply_move(hub: Any, move: Move) -> Any:
    """Rewrite every address the entry keeps to the new IP and save it; returns the entry (reload it after)."""
    entry = hub.config_entries[move.entry_id]
    data, options = dict(entry.data or {}), dict(entry.options or {})
    for key, (where, ip) in addresses(entry).items():
        if ip != move.old_ip:
            continue
        target = options if where == "options" else data
        target[key] = with_new_ip(str(target[key]), move.new_ip)
    entry.data, entry.options = data, options
    if hub.store is not None:
        hub.store.save_config_entry(entry)
    log.info("%s moved %s -> %s (MAC %s); its address is updated", entry.title, move.old_ip, move.new_ip, move.mac)
    return entry
