"""Echo OTA: set Online updates channel on adopted Echos and roll out firmware updates safely.

Echos (hassmic) expose an ESPHome ``update`` entity plus a ``select.*_online_updates`` entity.
The device only offers new versions after ``Online updates`` is set to ``release`` (or ``beta``);
the device downloads signed GitHub releases itself and falls back on a failed self-test.
HomeHub sets the channel on adoption, then installs one update at a time during the configured
window, stopping the rollout for the night after any failure/timeout.
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import UTC, datetime
from typing import Any

log = logging.getLogger("homehub.echo_ota")

ONLINE_UPDATES_CHANNEL = (os.getenv("HOMEHUB_ECHO_UPDATE_CHANNEL", "release") or "release").strip().lower()
if ONLINE_UPDATES_CHANNEL not in ("release", "beta", "off"):
    ONLINE_UPDATES_CHANNEL = "release"

HOMEHUB_ECHO_AUTO_UPDATE = (os.getenv("HOMEHUB_ECHO_AUTO_UPDATE", "1") or "1").strip().lower() not in ("0", "false", "no", "off")
try:
    HOMEHUB_ECHO_UPDATE_HOUR = int(os.getenv("HOMEHUB_ECHO_UPDATE_HOUR", "3") or "3")
except ValueError:
    HOMEHUB_ECHO_UPDATE_HOUR = 3

ECHO_OTA_KV_KEY = "echo_ota"
ECHO_OTA_ROLLOUT_TIMEOUT_S = 20 * 60  # 20 minutes


def _kv_get(hub: Any, key: str, default: Any) -> Any:
    store = getattr(hub, "store", None)
    if store is None:
        return default
    try:
        return store.get_kv(key, default)
    except Exception:
        log.exception("echo_ota: failed reading kv %s", key)
        return default


def _kv_set(hub: Any, key: str, value: Any) -> None:
    store = getattr(hub, "store", None)
    if store is None:
        return
    try:
        store.set_kv(key, value)
    except Exception:
        log.exception("echo_ota: failed writing kv %s", key)


def echo_nodes(hub: Any) -> list[str]:
    """Nodes that are Echos: ESPHome device with an assist_satellite.* entity and a select.*_online_updates entity."""
    nodes: list[str] = []
    for entry in getattr(hub, "config_entries", {}).values():
        if getattr(entry, "domain", None) != "esphome":
            continue
        ents = [e for e in getattr(hub, "entities", {}).values() if getattr(e, "config_entry_id", None) == entry.entry_id]
        has_sat = any(getattr(e, "entity_id", "").startswith("assist_satellite.") for e in ents)
        updates = [e for e in ents if getattr(e, "domain", None) == "select" and str(getattr(e, "entity_id", "")).endswith("_online_updates")]
        if has_sat and updates:
            # stable node id: prefer entry_id
            nodes.append(entry.entry_id)
    return sorted(nodes)


def _node_entities(hub: Any, node_id: str) -> list[Any]:
    return [e for e in getattr(hub, "entities", {}).values() if getattr(e, "config_entry_id", None) == node_id]


def _find_online_updates_select(hub: Any, node_id: str) -> Any | None:
    for e in _node_entities(hub, node_id):
        if getattr(e, "domain", None) == "select" and str(getattr(e, "entity_id", "")).endswith("_online_updates"):
            return e
    return None


def _find_firmware_update_entity(hub: Any, node_id: str) -> Any | None:
    for e in _node_entities(hub, node_id):
        if getattr(e, "domain", None) == "update":
            return e
    return None


def _hub_now(hub: Any) -> datetime:
    """Now in the household's own time zone (hub.time_zone), so 3 means 3 in the morning here."""
    from zoneinfo import ZoneInfo

    try:
        return datetime.now(ZoneInfo(str(getattr(hub, "time_zone", "") or "UTC")))
    except Exception:
        return datetime.now(UTC)


async def ensure_channel(hub: Any) -> None:
    """Set Online updates select to HOMEHUB_ECHO_UPDATE_CHANNEL for Echos still at off.

    Never change a select the household set to something other than off. We remember nodes
    HomeHub has set in kv key 'echo_ota' under homehub_set.
    """
    if ONLINE_UPDATES_CHANNEL == "off":
        return
    data = _kv_get(hub, ECHO_OTA_KV_KEY, {}) or {}
    if not isinstance(data, dict):
        data = {}
    homehub_set = set(data.get("homehub_set") or [])
    changed = False
    for node_id in echo_nodes(hub):
        sel = _find_online_updates_select(hub, node_id)
        if sel is None:
            continue
        st = hub.states.get(getattr(sel, "entity_id", ""))
        cur = (st.state if st is not None else "unknown") or "unknown"
        cur = str(cur).strip().lower()
        # household set something other than off -> never touch
        if cur not in ("off", "unknown", ""):
            # Set by HomeHub earlier or by someone in the household: either way, leave it.
            continue
        # cur is off or unknown
        if node_id in homehub_set:
            continue
        try:
            await hub.call_service("select", "select_option", {"entity_id": sel.entity_id, "option": ONLINE_UPDATES_CHANNEL})
            homehub_set.add(node_id)
            changed = True
            log.info("echo_ota: set online_updates for %s to %s", node_id, ONLINE_UPDATES_CHANNEL)
        except Exception:
            log.exception("echo_ota: failed to set online_updates for %s", node_id)
    if changed:
        data["homehub_set"] = sorted(homehub_set)
        _kv_set(hub, ECHO_OTA_KV_KEY, data)


async def rollout_once(hub: Any, now: datetime | None = None) -> None:
    """Roll out firmware updates to Echos one at a time during the update hour."""
    if not HOMEHUB_ECHO_AUTO_UPDATE:
        return
    if now is None:
        now = _hub_now(hub)
    data = _kv_get(hub, ECHO_OTA_KV_KEY, {}) or {}
    if not isinstance(data, dict):
        data = {}
    today = now.date().isoformat()
    if data.get("rollout_failed_day") == today:
        return
    rollout = data.get("rollout") or {}
    if not isinstance(rollout, dict):
        rollout = {}

    active_node = rollout.get("active_node")
    if active_node:
        upd = _find_firmware_update_entity(hub, active_node)
        if upd is None:
            log.warning("echo_ota: active rollout node %s has no update entity, failing night", active_node)
            data["rollout_failed_day"] = today
            data["rollout"] = {}
            lr = data.get("last_results") or {}
            if not isinstance(lr, dict):
                lr = {}
            lr[active_node] = "failed"
            data["last_results"] = lr
            _kv_set(hub, ECHO_OTA_KV_KEY, data)
            return
        st = hub.states.get(getattr(upd, "entity_id", ""))
        installed = None
        if st is not None:
            installed = (st.attributes.get("installed_version") if st.attributes else None) or st.state
        from_version = rollout.get("from_version")
        started_at_s = rollout.get("started_at")
        try:
            started_at = datetime.fromisoformat(started_at_s) if started_at_s else None
        except Exception:
            started_at = None
        if installed and from_version and str(installed) != str(from_version):
            log.info("echo_ota: rollout done for %s (%s -> %s)", active_node, from_version, installed)
            data["rollout"] = {}
            lr = data.get("last_results") or {}
            if not isinstance(lr, dict):
                lr = {}
            lr[active_node] = "ok"
            data["last_results"] = lr
            _kv_set(hub, ECHO_OTA_KV_KEY, data)
            return
        if started_at is not None:
            elapsed = (now - started_at).total_seconds()
            if elapsed >= ECHO_OTA_ROLLOUT_TIMEOUT_S:
                log.warning("echo_ota: rollout timeout for %s after %ds, stopping for night", active_node, int(elapsed))
                data["rollout_failed_day"] = today
                data["rollout"] = {}
                lr = data.get("last_results") or {}
                if not isinstance(lr, dict):
                    lr = {}
                lr[active_node] = "failed"
                data["last_results"] = lr
                _kv_set(hub, ECHO_OTA_KV_KEY, data)
                return
        return

    # A new install starts only in the update hour; one already running is followed above at any hour.
    if now.hour != HOMEHUB_ECHO_UPDATE_HOUR:
        return
    for node_id in echo_nodes(hub):
        upd = _find_firmware_update_entity(hub, node_id)
        if upd is None:
            continue
        st = hub.states.get(getattr(upd, "entity_id", ""))
        if st is None:
            continue
        if str(st.state).lower() != "on":
            continue
        installed_version = (st.attributes.get("installed_version") if st and st.attributes else None) or "unknown"
        try:
            await hub.call_service("update", "install", {"entity_id": upd.entity_id})
            data["rollout"] = {
                "active_node": node_id,
                "from_version": str(installed_version),
                "started_at": now.isoformat(),
            }
            _kv_set(hub, ECHO_OTA_KV_KEY, data)
            log.info("echo_ota: started rollout for %s from %s", node_id, installed_version)
        except Exception:
            log.exception("echo_ota: failed to start update install for %s", node_id)
        return


async def run_loop(hub: Any) -> None:
    """Background loop: every 300s ensure_channel then rollout_once."""
    while True:
        try:
            await ensure_channel(hub)
        except Exception:
            log.exception("echo_ota: ensure_channel failed")
        try:
            await rollout_once(hub)
        except Exception:
            log.exception("echo_ota: rollout_once failed")
        await asyncio.sleep(300)

