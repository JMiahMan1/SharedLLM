"""Vehicles told apart by the Bluetooth hardware they carry.

A vehicle in Wander can be linked to one or more Bluetooth devices: the car's
head unit (classic Bluetooth, bonded for calls and music), and sometimes the
same unit's Bluetooth LE side, which has an address of its own. The key is
always the hardware address, never the name: a head unit's name is whatever
the car or a person set it to ("My Car", "Uconnect 1234") and it can change at
any time, while its address does not. The name is kept for display only.

Only stable addresses can be linked:

* a classic (BR/EDR) address is the device's public address;
* an LE address is stable when it is public, or a *random static* address
  (top two bits ``11``), which a device keeps until it is reset;
* an LE *resolvable private* address (top bits ``01``) or a *non-resolvable*
  one (``00``) changes every few minutes, so linking one would work for a
  quarter of an hour and then never again. Android reports the identity
  address of an LE device once it is bonded (the pairing hands over its
  identity key), so the phone offers bonded devices only.

The phone decides what it can offer (it knows the address type); this module
checks again on the way in, so a link can never be keyed on an address that
will not be seen again.

On a trip the phone sends, with each fix, the linked addresses it is connected
to at that moment (``bt``). Geo maps them to a vehicle (``vehicle_for``) and
counts the fixes each vehicle was seen on (``vote``); the trip's vehicle is the
one seen most (``winner``), unless the trip's owner picked one by hand, which
is never overridden.
"""
from __future__ import annotations

import re
import time
from typing import Any

#: How a vehicle came to be on a trip.
SOURCE_DEFAULT = "default"      # the person's designated vehicle (or the household's first)
SOURCE_BLUETOOTH = "bluetooth"  # a linked device was connected during the trip
SOURCE_MANUAL = "manual"        # the trip's owner picked it

#: The kinds a link may be.
KIND_CLASSIC = "classic"
KIND_LE_PUBLIC = "le_public"
KIND_LE_STATIC = "le_static"
KINDS = (KIND_CLASSIC, KIND_LE_PUBLIC, KIND_LE_STATIC)

#: Most devices one vehicle can be linked to (a head unit's classic and LE
#: sides, a dash cam, an OBD dongle...). More is a mistake, not a car.
MAX_LINKS_PER_VEHICLE = 8
#: Longest display name kept.
NAME_MAX = 64

_HEX12 = re.compile(r"[0-9A-F]{12}")
#: Placeholder Android returns for an address the app may not read.
_PLACEHOLDERS = {"020000000000", "000000000000", "FFFFFFFFFFFF"}


class LinkError(ValueError):
    """A link that cannot be made, with the reason in words a person can act on."""


def normalise_address(value: Any) -> str:
    """``aa-bb-cc-dd-ee-ff`` / ``AABBCCDDEEFF`` / ``aa:bb:...`` to ``AA:BB:CC:DD:EE:FF``.

    Raises LinkError for anything that is not a usable hardware address."""
    raw = re.sub(r"[\s:.\-]", "", str(value or "")).upper()
    if not _HEX12.fullmatch(raw):
        raise LinkError("That is not a Bluetooth hardware address")
    if raw in _PLACEHOLDERS:
        raise LinkError("Android hid this device's address; allow Nearby devices for Jarvis and try again")
    return ":".join(raw[i:i + 2] for i in range(0, 12, 2))


def le_random_kind(address: str) -> str:
    """For an LE *random* address: ``static``, ``resolvable`` or ``non_resolvable`` (by its top two bits)."""
    top = int(normalise_address(address)[:2], 16) >> 6
    return {0b11: "static", 0b01: "resolvable", 0b00: "non_resolvable"}.get(top, "reserved")


def check_linkable(address: Any, kind: Any) -> tuple[str, str]:
    """The normalised address and kind of a link, or LinkError saying why it cannot be one."""
    addr = normalise_address(address)
    k = str(kind or KIND_CLASSIC).strip().lower()
    if k not in KINDS:
        raise LinkError(
            "Only a paired device with a fixed address can be linked; this one changes its address")
    if k == KIND_LE_STATIC and le_random_kind(addr) != "static":
        raise LinkError("This device hides behind a changing address; pair it with the phone first")
    return addr, k


def clean_name(name: Any) -> str:
    """The device's name for display: trimmed, one line, bounded. May be empty."""
    text = " ".join(str(name or "").split())
    return text[:NAME_MAX]


def links_of(vehicle: dict) -> list[dict]:
    """A stored vehicle's links (older vehicles have none)."""
    links = vehicle.get("bluetooth")
    return [link for link in links if isinstance(link, dict) and link.get("address")] if isinstance(links, list) else []


def owner_of(vehicles: list[dict], address: str) -> dict | None:
    """The vehicle an address is linked to, if any."""
    for v in vehicles:
        if any(link.get("address") == address for link in links_of(v)):
            return v
    return None


def add_link(vehicle: dict, vehicles: list[dict], address: Any, kind: Any, name: Any,
             linked_by: str, now: float | None = None) -> dict:
    """``vehicle`` with one more link (or the same link's name refreshed).

    An address belongs to one vehicle: linking it to a second is refused, so
    a trip can never be claimed by two cars."""
    addr, k = check_linkable(address, kind)
    other = owner_of([v for v in vehicles if v.get("id") != vehicle.get("id")], addr)
    if other is not None:
        raise LinkError(f"That device is already linked to {other.get('name') or 'another vehicle'}")
    links = links_of(vehicle)
    entry = {
        "address": addr,
        "kind": k,
        "name": clean_name(name),
        "linked_by": str(linked_by or "")[:64],
        "linked_at": float(now if now is not None else time.time()),
    }
    kept = [link for link in links if link["address"] != addr]
    if len(kept) >= MAX_LINKS_PER_VEHICLE:
        raise LinkError(f"A vehicle can have at most {MAX_LINKS_PER_VEHICLE} linked devices")
    out = dict(vehicle)
    out["bluetooth"] = [*kept, entry]
    return out


def remove_link(vehicle: dict, address: Any) -> tuple[dict, bool]:
    """``vehicle`` without the link, and whether there was one."""
    addr = normalise_address(address)
    links = links_of(vehicle)
    kept = [link for link in links if link["address"] != addr]
    out = dict(vehicle)
    out["bluetooth"] = kept
    return out, len(kept) != len(links)


def address_index(vehicles: list[dict]) -> dict[str, str]:
    """Address -> vehicle id, for every link in the household."""
    out: dict[str, str] = {}
    for v in vehicles:
        vid = str(v.get("id") or "")
        if not vid:
            continue
        for link in links_of(v):
            out.setdefault(str(link["address"]), vid)
    return out


def clean_reported(values: Any, limit: int = 8) -> list[str]:
    """The addresses a phone reported with a fix, normalised; junk is dropped, not fatal."""
    if not isinstance(values, list):
        return []
    out: list[str] = []
    for value in values[: limit * 2]:
        try:
            addr = normalise_address(value)
        except LinkError:
            continue
        if addr not in out:
            out.append(addr)
    return out[:limit]


def vehicle_for(index: dict[str, str], addresses: list[str]) -> str | None:
    """The vehicle a fix's connected addresses point to (the first linked one), or None."""
    for addr in addresses:
        vid = index.get(addr)
        if vid:
            return vid
    return None


def vote(trip: dict, vehicle_id: str | None) -> dict:
    """Count one fix for ``vehicle_id`` on the trip (a fix with no linked device counts for nothing)."""
    if not vehicle_id:
        return trip
    votes = dict(trip.get("bt_votes") or {})
    votes[vehicle_id] = int(votes.get(vehicle_id, 0)) + 1
    trip["bt_votes"] = votes
    return trip


def winner(votes: dict | None) -> str | None:
    """The vehicle seen on the most fixes; a tie goes to the id that sorts first (stable)."""
    if not votes:
        return None
    best = sorted(votes.items(), key=lambda kv: (-int(kv[1]), str(kv[0])))
    return str(best[0][0]) if best and int(best[0][1]) > 0 else None


def apply_vehicle(trip: dict, vehicle: dict, source: str) -> dict:
    """Put ``vehicle`` (and its fuel figures) on the trip, saying where it came from."""
    trip["vehicle_id"] = vehicle.get("id")
    trip["vehicle_name"] = vehicle.get("name") or trip.get("vehicle_name")
    trip["fuel_type"] = vehicle.get("fuel_type", trip.get("fuel_type", "gasoline"))
    if vehicle.get("mpg") is not None:
        trip["mpg"] = float(vehicle["mpg"])
    if vehicle.get("cost_per_gallon") is not None:
        trip["cost_per_gallon"] = float(vehicle["cost_per_gallon"])
    trip["vehicle_source"] = source
    return trip


def settle(trip: dict, vehicles_by_id: dict[str, dict]) -> dict:
    """Make the trip's vehicle the Bluetooth winner, unless its owner chose one by hand.

    The detected vehicle is also kept as ``bt_vehicle_id`` so the trip can say
    "detected" and a manual choice can be undone back to it."""
    best = winner(trip.get("bt_votes"))
    if not best or best not in vehicles_by_id:
        return trip
    trip["bt_vehicle_id"] = best
    if trip.get("vehicle_source") == SOURCE_MANUAL:
        return trip
    if trip.get("vehicle_id") == best and trip.get("vehicle_source") == SOURCE_BLUETOOTH:
        return trip
    return apply_vehicle(trip, vehicles_by_id[best], SOURCE_BLUETOOTH)
