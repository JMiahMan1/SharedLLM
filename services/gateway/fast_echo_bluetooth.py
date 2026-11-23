"""Fast Echo Bluetooth command matcher — matches natural phrases without a model.

Pure matcher: match_echo_bluetooth(text: str) -> EchoBluetoothCommand | None
with action in ('pair_on', 'pair_off', 'disconnect', 'status') and optional
target_room: str | None.

Matches natural phrases:
  'pair bluetooth', 'turn on bluetooth pairing', 'bluetooth speaker mode',
  'stop bluetooth pairing', 'disconnect bluetooth', 'disconnect my phone',
  'bluetooth status', 'what is connected to bluetooth'
  (with optional 'in the garage', 'on master bedroom echo', 'in the kids room', etc.).
"""

from __future__ import annotations

import re
from typing import Any


class EchoBluetoothCommand:
    """Result of matching a Bluetooth voice command."""

    __slots__ = ("action", "raw_text", "target_room")

    def __init__(self, action: str, target_room: str | None, raw_text: str):
        self.action = action
        self.target_room = target_room
        self.raw_text = raw_text

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, EchoBluetoothCommand):
            return NotImplemented
        return (self.action == other.action
                and self.target_room == other.target_room
                and self.raw_text == other.raw_text)

    def __repr__(self) -> str:
        return f"EchoBluetoothCommand(action={self.action!r}, target_room={self.target_room!r})"


# -- Phrase matching patterns --

_PAIR_ON_PHRASES = frozenset({
    "pair bluetooth",
    "turn on bluetooth pairing",
    "bluetooth pairing on",
    "enable bluetooth pairing",
    "start bluetooth pairing",
})

_PAIR_OFF_PHRASES = frozenset({
    "stop bluetooth pairing",
    "disable bluetooth pairing",
    "turn off bluetooth pairing",
})

_DISCONNECT_PHRASES = frozenset({
    "disconnect bluetooth",
    "disconnect my phone",
    "bluetooth disconnect",
    "forget bluetooth device",
})

_STATUS_PHRASES = frozenset({
    "bluetooth status",
    "what is connected to bluetooth",
    "bluetooth device status",
    "is bluetooth connected",
    "bluetooth connected",
})

_SPEAKER_MODE_PHRASES = frozenset({
    "bluetooth speaker mode",
    "bluetooth mode on",
    "bluetooth mode off",
})


def _extract_target_room(text: str) -> str | None:
    """Extract optional room qualifier like 'in the kitchen' or 'on kitchen echo'.

    Accepts ANY room name, not just a hardcoded list.
    Patterns: 'in the <room>', 'on <room> echo', 'on the <room>', 'at <room>'
    """
    # Strip known command prefixes so the room regex only sees the room part.
    _PREFIXES = (
        "turn on bluetooth pairing ",
        "pair bluetooth ",
        "stop bluetooth pairing ",
        "disable bluetooth pairing ",
        "turn off bluetooth pairing ",
        "disconnect bluetooth ",
        "disconnect my phone ",
        "bluetooth disconnect ",
        "forget bluetooth device ",
        "bluetooth status ",
        "what is connected to bluetooth ",
        "bluetooth speaker mode on ",
        "bluetooth speaker mode off ",
    )
    t = text.lower()
    for p in _PREFIXES:
        if t.startswith(p):
            t = t[len(p):]
            break
    # Match patterns: 'in the kitchen', 'on kitchen echo', 'on the kitchen', 'at garage', etc.
    # Capture the room name (can contain spaces, hyphens, apostrophes)
    m = re.search(
        r"\b(?:in|on|at)\s+(?:the\s+)?([a-z0-9][a-z0-9\s\-']*(?:\s+echo)?)\b",
        t,
        re.IGNORECASE,
    )
    if m:
        room = m.group(1).strip().lower()
        # Remove trailing ' echo' if present
        room = re.sub(r"\s+echo$", "", room)
        return room.strip()
    return None


def match_echo_bluetooth(text: str) -> EchoBluetoothCommand | None:
    """Match a Bluetooth voice command against natural language phrases.

    Returns an EchoBluetoothCommand with an action in
    ('pair_on', 'pair_off', 'disconnect', 'status'), or None if no match.
    """
    if not text:
        return None

    normalized = re.sub(r"\s+", " ", text.strip().lower())

    # --- Speaker mode ---
    if any(normalized.startswith(p) or normalized == p for p in _SPEAKER_MODE_PHRASES):
        # "bluetooth speaker mode on" -> pair_on, "bluetooth speaker mode off" -> pair_off
        if "on" in normalized:
            return EchoBluetoothCommand("pair_on", _extract_target_room(normalized), normalized)
        if "off" in normalized:
            return EchoBluetoothCommand("pair_off", _extract_target_room(normalized), normalized)
        return EchoBluetoothCommand("pair_on", None, normalized)

    # --- Pair on ---
    if any(normalized == p or normalized.startswith(p + " ") for p in _PAIR_ON_PHRASES):
        return EchoBluetoothCommand("pair_on", _extract_target_room(normalized), normalized)

    # --- Pair off ---
    if any(normalized == p or normalized.startswith(p + " ") for p in _PAIR_OFF_PHRASES):
        return EchoBluetoothCommand("pair_off", _extract_target_room(normalized), normalized)

    # --- Disconnect ---
    if any(normalized == p or normalized.startswith(p + " ") for p in _DISCONNECT_PHRASES):
        return EchoBluetoothCommand("disconnect", _extract_target_room(normalized), normalized)

    # --- Status ---
    if any(normalized == p or normalized.startswith(p + " ") for p in _STATUS_PHRASES):
        return EchoBluetoothCommand("status", _extract_target_room(normalized), normalized)

    return None


# --- Entity resolution helpers ---

def _normalize_states(states: dict[str, str] | list[dict[str, Any]]) -> dict[str, str]:
    """Normalize HomeHub states to a flat dict[str, str].

    Accepts either:
    - dict[str, str]: entity_id -> state
    - list[dict]: list of entity dicts with 'entity_id' and 'state' keys
    """
    if isinstance(states, dict):
        return states
    result = {}
    for entry in states:
        if isinstance(entry, dict):
            eid = entry.get("entity_id")
            state = entry.get("state")
            if eid is not None and state is not None:
                result[str(eid)] = str(state)
    return result


def resolve_bt_entities(
    states: dict[str, str] | list[dict[str, Any]], node: str
) -> dict[str, str | None]:
    """Locate Bluetooth-related entity ids for a given Echo node.

    Looks up entities in the Home Hub state dict by entity_id pattern.
    Returns a dict with keys: pairing_switch, disconnect_button,
    connected_device, connected_sensor — each set to the entity_id string
    (or None when the entity is absent).

    Entity patterns (hassmic/echo-dot-assist conventions):
      switch.<node>_bluetooth_pairing   (or switch.<node>_pairing)
      button.<node>_bluetooth_disconnect
      sensor.<node>_bluetooth_device
      binary_sensor.<node>_bluetooth_connected

    Accepts either a dict[str, str] OR list[dict] HomeHub state list.
    Preserves underscores in node names.
    """
    states_dict = _normalize_states(states)

    result: dict[str, str | None] = {
        "pairing_switch": None,
        "disconnect_button": None,
        "connected_device": None,
        "connected_sensor": None,
    }

    # Build entity ID variations - preserve underscores
    node_safe = re.sub(r"[^a-z0-9_]", "_", node.lower()).strip("_")

    # switch.<node>_bluetooth_pairing or switch.<node>_pairing
    for pattern in [f"switch.{node_safe}_bluetooth_pairing", f"switch.{node_safe}_pairing"]:
        if pattern in states_dict:
            result["pairing_switch"] = pattern
            break

    # button.<node>_bluetooth_disconnect
    disconnect_pattern = f"button.{node_safe}_bluetooth_disconnect"
    if disconnect_pattern in states_dict:
        result["disconnect_button"] = disconnect_pattern

    # sensor.<node>_bluetooth_device
    device_pattern = f"sensor.{node_safe}_bluetooth_device"
    if device_pattern in states_dict:
        result["connected_device"] = device_pattern

    # binary_sensor.<node>_bluetooth_connected
    connected_pattern = f"binary_sensor.{node_safe}_bluetooth_connected"
    if connected_pattern in states_dict:
        result["connected_sensor"] = connected_pattern

    return result
