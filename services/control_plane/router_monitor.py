"""Home network monitoring through the OpenWrt router (docs/NETWORK.md).

Every minute the control plane reads the router's status over its read-only
ubus account (router_client.py) and checks the internet itself. It keeps the
latest picture and the router's log in Redis for Admin > Network, and tells
admins (category ``admin``) about:

- the internet going down, and coming back with how long it was out;
- the router becoming unreachable, or restarting;
- one device using most of a Wi-Fi channel's airtime (on 2026-10-15 a server
  downloading over slow 2.4 GHz Wi-Fi left the TVs on that channel offline),
  or a channel busy for long with no one device to blame;
- a device dropping off Wi-Fi and reconnecting over and over;
- DHCP address pools, the connection table or router memory nearly full.

Each alert is sent once when its condition starts and re-arms only after the
condition has clearly ended, so a problem that persists is one notice.

The functions that turn snapshots into notices take plain dicts and the
current time, so tests drive them without a router.
"""
from __future__ import annotations

import ipaddress
import json
import logging
import os
import re
import socket
import time
from typing import Any

from services.control_plane.admin_notice import notify_admins
from services.control_plane.router_client import CertificateChanged, RouterClient, RouterError

log = logging.getLogger("control_plane.router_monitor")

CHECK_SECONDS = int(os.getenv("ROUTER_CHECK_INTERVAL", "60"))
NETWORK_URL = "/admin/network"

#: Hosts the internet check connects to (TCP 443); the internet counts as down
#: only when none answers on two checks in a row.
INTERNET_PROBES = (("1.1.1.1", 443), ("9.9.9.9", 443), ("208.67.222.222", 443))
INTERNET_DOWN_AFTER = 2
ROUTER_DOWN_AFTER = 3

#: Airtime: the window (checks) and the limits that make a channel "hogged".
AIRTIME_WINDOW = 5
HOG_BUSY_PCT = 60.0
HOG_SHARE_PCT = 35.0
CONGESTED_WINDOW = 10
CONGESTED_BUSY_PCT = 80.0
#: A channel counts as calm again below this, which re-arms its alerts.
CALM_BUSY_PCT = 40.0

FLAP_WINDOW_S = 30 * 60
FLAP_CONNECTS = 6
FLAP_REPEAT_S = 24 * 3600

POOL_ALERT_PCT, POOL_CLEAR_PCT = 90.0, 80.0
CONNTRACK_ALERT_PCT, CONNTRACK_CLEAR_PCT = 85.0, 70.0
MEMORY_ALERT_PCT, MEMORY_CLEAR_PCT = 10.0, 15.0

#: Redis keys. The log keeps about a week of a typical home router's lines.
STATUS_KEY = "network:status"
STATE_KEY = "network:state"
EVENTS_KEY = "network:events"
LOG_KEY = "network:log"
USAGE_PREFIX = "network:usage:"
LOG_MAX = 25000
EVENTS_MAX = 200
USAGE_TTL_S = 35 * 86400

_CONNECTED = re.compile(r"AP-STA-CONNECTED ((?:[0-9a-f]{2}:){5}[0-9a-f]{2})", re.I)
_SYSLOG_LEVELS = {0: "emerg", 1: "alert", 2: "crit", 3: "err", 4: "warning", 5: "notice", 6: "info", 7: "debug"}


# ---------------------------------------------------------------- collecting


def band_of(mhz: int) -> str:
    if mhz >= 5925:
        return "6 GHz"
    return "5 GHz" if mhz >= 5000 else "2.4 GHz"


def collect(client: RouterClient, last_log_id: int) -> dict[str, Any]:
    """One snapshot of the router: system, interfaces, Wi-Fi, leases, pools, log."""
    system = client.call("system", "info")
    board = client.call("system", "board")
    interfaces = client.call("network.interface", "dump").get("interface", [])
    leases = client.call("luci-rpc", "getDHCPLeases").get("dhcp_leases", [])
    dhcp = client.call("uci", "get", {"config": "dhcp"}).get("values", {})

    radios: list[dict[str, Any]] = []
    for dev in client.call("iwinfo", "devices").get("devices", []):
        info = client.call("iwinfo", "info", {"device": dev})
        if info.get("mode") not in ("Master", "ap", "AP"):
            continue
        survey = client.call("iwinfo", "survey", {"device": dev}).get("results", [])
        mine = next((s for s in survey if s.get("mhz") == info.get("frequency")), {})
        clients = []
        for c in client.call("iwinfo", "assoclist", {"device": dev}).get("results", []):
            rx, tx = c.get("rx") or {}, c.get("tx") or {}
            clients.append({
                "mac": (c.get("mac") or "").lower(),
                "signal": c.get("signal"),
                "inactive_ms": c.get("inactive"),
                "connected_s": c.get("connected_time"),
                "rx_bytes": rx.get("bytes", 0), "tx_bytes": tx.get("bytes", 0),
                "rx_kbps": rx.get("rate", 0), "tx_kbps": tx.get("rate", 0),
            })
        radios.append({
            "device": dev, "phy": info.get("phy") or dev, "ssid": info.get("ssid"),
            "channel": info.get("channel"), "mhz": info.get("frequency") or 0,
            "band": band_of(info.get("frequency") or 0),
            "active_ms": mine.get("active_time", 0), "busy_ms": mine.get("busy_time", 0),
            "clients": clients,
        })

    conntrack = {}
    for name in ("count", "max"):
        try:
            data = client.call("file", "read", {"path": f"/proc/sys/net/netfilter/nf_conntrack_{name}"})
            conntrack[name] = int((data.get("data") or "0").strip() or 0)
        except (RouterError, ValueError):
            pass

    lines = client.call("log", "read", {"lines": 1000, "stream": False}).get("log", [])
    # Line ids restart when the router reboots; then every line is new.
    if lines and max(int(ln.get("id", 0)) for ln in lines) < last_log_id:
        last_log_id = 0
    # logd reports times in milliseconds; everything here works in seconds.
    new_lines = [{**ln, "time": _seconds(ln.get("time", 0))} for ln in lines if int(ln.get("id", 0)) > last_log_id]

    return {
        "system": {
            "uptime": system.get("uptime", 0),
            "load1": (system.get("load") or [0])[0] / 65536.0,
            "mem_total": (system.get("memory") or {}).get("total", 0),
            "mem_available": (system.get("memory") or {}).get("available", 0),
        },
        "board": {"model": board.get("model"), "hostname": board.get("hostname"),
                  "version": (board.get("release") or {}).get("version")},
        "interfaces": [_interface(i) for i in interfaces],
        "leases": [{"mac": (le.get("macaddr") or "").lower(), "ip": le.get("ipaddr"),
                    "name": le.get("hostname") or "", "expires": le.get("expires")} for le in leases],
        "hosts": _static_hosts(dhcp),
        "pools": _pools(dhcp, interfaces, leases),
        "radios": radios,
        "conntrack": conntrack,
        "log": new_lines,
    }


def _seconds(t: Any) -> float:
    """A Unix time in seconds, from seconds or logd's milliseconds."""
    t = float(t or 0)
    return t / 1000.0 if t > 1e11 else t


def _interface(i: dict[str, Any]) -> dict[str, Any]:
    addrs = i.get("ipv4-address") or []
    return {"name": i.get("interface"), "up": bool(i.get("up")), "uptime": i.get("uptime", 0),
            "proto": i.get("proto"), "device": i.get("l3_device") or i.get("device"),
            "ipv4": [f"{a.get('address')}/{a.get('mask')}" for a in addrs if a.get("address")]}


def _static_hosts(dhcp: dict[str, Any]) -> dict[str, str]:
    """MAC -> name for the router's fixed DHCP leases (a host entry may list several MACs)."""
    names: dict[str, str] = {}
    for section in dhcp.values():
        if section.get(".type") != "host" or not section.get("name"):
            continue
        macs = section.get("mac") or []
        for mac in (macs if isinstance(macs, list) else str(macs).split()):
            names[mac.lower()] = section["name"]
    return names


def _pools(dhcp: dict[str, Any], interfaces: list[dict[str, Any]], leases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Each DHCP pool's size and how many of its addresses are leased."""
    nets = {}
    for i in interfaces:
        for a in i.get("ipv4-address") or []:
            try:
                nets[i.get("interface")] = ipaddress.ip_interface(f"{a['address']}/{a['mask']}").network
            except (KeyError, ValueError):
                continue
    pools = []
    for section in dhcp.values():
        if section.get(".type") != "dhcp" or section.get("ignore") == "1":
            continue
        name = section.get("interface") or section.get(".name")
        net = nets.get(name)
        if net is None:
            continue
        start, size = int(section.get("start", 100)), int(section.get("limit", 150))
        first = int(net.network_address) + start
        used = 0
        for le in leases:
            try:
                ip = int(ipaddress.ip_address(le.get("ipaddr") or ""))
            except ValueError:
                continue
            used += first <= ip < first + size
        pools.append({"network": name, "size": size, "used": used})
    return pools


def internet_up(probes=INTERNET_PROBES, timeout: float = 4) -> bool:
    for host, port in probes:
        try:
            with socket.create_connection((host, port), timeout=timeout):
                return True
        except OSError:
            continue
    return False


# ---------------------------------------------------------------- analysing


def device_name(mac: str, snap: dict[str, Any]) -> tuple[str, str | None]:
    """The router's name for a device and its address, else its MAC."""
    mac = mac.lower()
    lease = next((le for le in snap.get("leases", []) if le["mac"] == mac), None)
    name = snap.get("hosts", {}).get(mac) or (lease or {}).get("name") or mac
    return name, (lease or {}).get("ip")


def _label(mac: str, snap: dict[str, Any]) -> str:
    name, ip = device_name(mac, snap)
    return f"{name} ({ip})" if ip and ip not in name else name


def _notice(title: str, body: str, tag: str, priority: int = 3) -> dict[str, Any]:
    return {"title": title, "body": body, "tag": tag, "priority": priority, "data": {"url": NETWORK_URL}}


def _minutes(seconds: float) -> str:
    m = max(1, round(seconds / 60))
    return f"{m} minute{'s' if m != 1 else ''}" if m < 120 else f"{m / 60:.1f} hours"


def channel_load(prev: dict[str, Any] | None, snap: dict[str, Any], elapsed_s: float) -> list[dict[str, Any]]:
    """Per radio (phy): how busy the channel was since the last check, and each
    client's share of that time, estimated as bytes moved over its link rate."""
    if not prev or elapsed_s <= 0:
        return []
    before = {(r["device"], c["mac"]): c for r in prev.get("radios", []) for c in r["clients"]}
    by_phy: dict[str, dict[str, Any]] = {}
    for r in snap.get("radios", []):
        p = next((x for x in prev.get("radios", []) if x["device"] == r["device"]), None)
        entry = by_phy.setdefault(r["phy"], {"phy": r["phy"], "band": r["band"], "ssids": [], "busy_pct": None, "clients": []})
        if r["ssid"] and r["ssid"] not in entry["ssids"]:
            entry["ssids"].append(r["ssid"])
        if p and entry["busy_pct"] is None:
            active = r["active_ms"] - p["active_ms"]
            busy = r["busy_ms"] - p["busy_ms"]
            if active > 0 and busy >= 0:
                entry["busy_pct"] = round(100.0 * busy / active, 1)
        for c in r["clients"]:
            old = before.get((r["device"], c["mac"]))
            if not old:
                continue
            air = 0.0
            for key, rate in (("rx_bytes", "rx_kbps"), ("tx_bytes", "tx_kbps")):
                moved = c[key] - old[key]
                if moved > 0 and c[rate] > 0:
                    air += moved * 8 / (c[rate] * 1000)
            entry["clients"].append({"mac": c["mac"], "ssid": r["ssid"],
                                     "share_pct": round(min(100.0, 100.0 * air / elapsed_s), 1),
                                     "bytes": max(0, c["rx_bytes"] - old["rx_bytes"]) + max(0, c["tx_bytes"] - old["tx_bytes"])})
    return list(by_phy.values())


def airtime_notices(state: dict[str, Any], loads: list[dict[str, Any]], snap: dict[str, Any]) -> list[dict[str, Any]]:
    notices = []
    windows = state.setdefault("airtime", {})
    for load in loads:
        if load["busy_pct"] is None:
            continue
        w = windows.setdefault(load["phy"], {"busy": [], "top": [], "hog": None, "congested": False})
        top = max(load["clients"], key=lambda c: c["share_pct"], default=None)
        w["busy"] = (w["busy"] + [load["busy_pct"]])[-CONGESTED_WINDOW:]
        w["top"] = (w["top"] + [[top["mac"], top["share_pct"], top["ssid"]] if top else [None, 0.0, None]])[-AIRTIME_WINDOW:]
        recent = w["busy"][-AIRTIME_WINDOW:]
        if len(recent) >= AIRTIME_WINDOW and sum(recent) / len(recent) < CALM_BUSY_PCT:
            w["hog"], w["congested"] = None, False
        where = f"{load['band']} {' / '.join(load['ssids'])}".strip()
        macs = {t[0] for t in w["top"]}
        if len(w["top"]) >= AIRTIME_WINDOW and len(macs) == 1 and None not in macs:
            mac = w["top"][-1][0]
            share = sum(t[1] for t in w["top"]) / len(w["top"])
            busy = sum(recent) / len(recent)
            if busy >= HOG_BUSY_PCT and share >= HOG_SHARE_PCT and w["hog"] != mac:
                w["hog"] = mac
                notices.append(_notice(
                    f"{device_name(mac, snap)[0]} is crowding the {load['band']} Wi-Fi",
                    f"{_label(mac, snap)} used about {share:.0f}% of the airtime on {where} over the last "
                    f"{AIRTIME_WINDOW} minutes, and the channel was {busy:.0f}% busy. Other devices on it may "
                    f"lose their connection. A network cable, or moving it to 5 GHz, would fix it.",
                    f"admin:network:airtime:{load['phy']}", priority=4))
                continue
        if (len(w["busy"]) >= CONGESTED_WINDOW and not w["congested"] and w["hog"] is None
                and sum(w["busy"]) / len(w["busy"]) >= CONGESTED_BUSY_PCT):
            w["congested"] = True
            notices.append(_notice(
                f"{load['band']} Wi-Fi is congested",
                f"The channel for {where} has been {sum(w['busy']) / len(w['busy']):.0f}% busy for "
                f"{CONGESTED_WINDOW} minutes with no single device to blame; nearby networks may be on "
                f"the same channel. Devices on it may be slow or drop off.",
                f"admin:network:congested:{load['phy']}", priority=3))
    return notices


def flap_notices(state: dict[str, Any], snap: dict[str, Any], now: float) -> list[dict[str, Any]]:
    """A device that reconnects to Wi-Fi again and again (from hostapd's log)."""
    seen = state.setdefault("connects", {})
    told = state.setdefault("flap_told", {})
    for line in snap.get("log", []):
        m = _CONNECTED.search(line.get("msg", ""))
        if m:
            seen.setdefault(m.group(1).lower(), []).append(float(line.get("time", now)))
    notices = []
    for mac in list(seen):
        times = [t for t in map(_seconds, seen[mac]) if 0 <= now - t <= FLAP_WINDOW_S]
        if not times:
            del seen[mac]
            continue
        seen[mac] = times
        if len(times) >= FLAP_CONNECTS and (mac not in told or now - told[mac] >= FLAP_REPEAT_S):
            told[mac] = now
            notices.append(_notice(
                f"{device_name(mac, snap)[0]} keeps dropping off Wi-Fi",
                f"{_label(mac, snap)} reconnected {len(times)} times in the last {FLAP_WINDOW_S // 60} "
                f"minutes. Weak signal or Wi-Fi power saving on the device are the usual causes; if it "
                f"also has a cable, its Wi-Fi can be turned off.",
                f"admin:network:flap:{mac}", priority=2))
    return notices


def _threshold(state: dict[str, Any], key: str, pct: float, alert: float, clear: float, high_is_bad: bool = True) -> bool | None:
    """True when ``pct`` just crossed into alert, False when it just cleared, else None."""
    flags = state.setdefault("flags", {})
    bad = pct >= alert if high_is_bad else pct <= alert
    good = pct < clear if high_is_bad else pct > clear
    if bad and not flags.get(key):
        flags[key] = True
        return True
    if good and flags.get(key):
        flags[key] = False
        return False
    return None


def capacity_notices(state: dict[str, Any], snap: dict[str, Any]) -> list[dict[str, Any]]:
    notices = []
    for pool in snap.get("pools", []):
        if not pool["size"]:
            continue
        pct = 100.0 * pool["used"] / pool["size"]
        if _threshold(state, f"pool:{pool['network']}", pct, POOL_ALERT_PCT, POOL_CLEAR_PCT):
            notices.append(_notice(
                f"Network \"{pool['network']}\" is nearly out of addresses",
                f"{pool['used']} of its {pool['size']} DHCP addresses are in use; new devices may not be "
                f"able to join.", f"admin:network:pool:{pool['network']}", priority=4))
    ct = snap.get("conntrack") or {}
    if ct.get("max"):
        if _threshold(state, "conntrack", 100.0 * ct["count"] / ct["max"], CONNTRACK_ALERT_PCT, CONNTRACK_CLEAR_PCT):
            notices.append(_notice(
                "The router's connection table is nearly full",
                f"{ct['count']} of {ct['max']} connections are tracked; new connections will start "
                f"failing when it fills.", "admin:network:conntrack", priority=4))
    sysinfo = snap.get("system") or {}
    if sysinfo.get("mem_total"):
        pct = 100.0 * sysinfo["mem_available"] / sysinfo["mem_total"]
        if _threshold(state, "memory", pct, MEMORY_ALERT_PCT, MEMORY_CLEAR_PCT, high_is_bad=False):
            notices.append(_notice(
                "The router is low on memory",
                f"{pct:.0f}% of its memory is free; it may slow down or restart.",
                "admin:network:memory", priority=3))
    return notices


def internet_notices(state: dict[str, Any], internet: bool, now: float) -> list[dict[str, Any]]:
    """The internet going down (after two failed checks) and coming back."""
    s = state.setdefault("internet", {"fails": 0, "down_since": None})
    if internet:
        notices = []
        if s["down_since"] is not None:
            notices.append(_notice(
                "The internet is back",
                f"It was down for {_minutes(now - s['down_since'])}, from "
                f"{time.strftime('%H:%M', time.localtime(s['down_since']))}.",
                "admin:network:internet", priority=3))
        s["fails"], s["down_since"] = 0, None
        return notices
    s["fails"] += 1
    if s["fails"] != INTERNET_DOWN_AFTER:
        return []
    s["down_since"] = now - (INTERNET_DOWN_AFTER - 1) * CHECK_SECONDS
    return [_notice(
        "The internet is down",
        "Jarvis can't reach the internet from home. Phones away from home won't reach Jarvis until "
        "it's back; this notice may arrive late for the same reason.",
        "admin:network:internet", priority=4)]


def router_notices(state: dict[str, Any], snap: dict[str, Any] | None, now: float,
                   error: str | None = None) -> list[dict[str, Any]]:
    """The router unreachable (after three failed checks) or back, and router restarts."""
    s = state.setdefault("router", {"fails": 0, "down_since": None, "uptime": None})
    if snap is None:
        s["fails"] += 1
        if s["fails"] != ROUTER_DOWN_AFTER:
            return []
        s["down_since"] = now - (ROUTER_DOWN_AFTER - 1) * CHECK_SECONDS
        return [_notice("Jarvis can't reach the router",
                        f"Router monitoring stopped: {error or 'no answer'}.", "admin:network:router", priority=3)]
    notices = []
    if s["down_since"] is not None:
        notices.append(_notice("Jarvis can reach the router again",
                               f"After {_minutes(now - s['down_since'])}.", "admin:network:router", priority=2))
    s["fails"], s["down_since"] = 0, None
    uptime = (snap.get("system") or {}).get("uptime")
    if s["uptime"] is not None and uptime is not None and uptime < s["uptime"]:
        notices.append(_notice("The router restarted", f"It has been up {_minutes(uptime)}.",
                               "admin:network:restart", priority=3))
    s["uptime"] = uptime
    return notices


def status_view(snap: dict[str, Any], loads: list[dict[str, Any]], internet: bool, now: float) -> dict[str, Any]:
    """What Admin > Network shows: names resolved, numbers ready to display."""
    share = {(c["mac"], c["ssid"]): c["share_pct"] for load in loads for c in load["clients"]}
    busy = {load["phy"]: load["busy_pct"] for load in loads}
    radios = []
    for r in snap.get("radios", []):
        clients = []
        for c in r["clients"]:
            name, ip = device_name(c["mac"], snap)
            clients.append({"mac": c["mac"], "name": name, "ip": ip, "signal": c["signal"],
                            "rx_mbps": round(c["rx_kbps"] / 1000, 1), "tx_mbps": round(c["tx_kbps"] / 1000, 1),
                            "airtime_pct": share.get((c["mac"], r["ssid"])),
                            "connected_s": c["connected_s"]})
        clients.sort(key=lambda c: (-(c["airtime_pct"] or 0), c["name"]))
        radios.append({"device": r["device"], "ssid": r["ssid"], "band": r["band"], "channel": r["channel"],
                       "busy_pct": busy.get(r["phy"]), "clients": clients})
    wans = [i for i in snap.get("interfaces", []) if (i["name"] or "").startswith("wan") and i["proto"] != "dhcpv6"]
    return {
        "checked_at": now,
        "internet": internet,
        "router": {**snap.get("board", {}), "uptime": snap["system"]["uptime"],
                   "memory_free_pct": round(100.0 * snap["system"]["mem_available"] / snap["system"]["mem_total"], 1)
                   if snap["system"].get("mem_total") else None,
                   "load1": round(snap["system"]["load1"], 2)},
        "wan": {"up": any(w["up"] for w in wans), "uptime": max((w["uptime"] for w in wans if w["up"]), default=0)},
        "radios": radios,
        "pools": snap.get("pools", []),
        "conntrack": snap.get("conntrack", {}),
    }


# ---------------------------------------------------------------- the loop


def log_entries(lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Router log lines as stored for Admin > Network (level from the syslog priority)."""
    return [{"id": int(ln.get("id", 0)), "time": int(_seconds(ln.get("time", 0))),
             "level": _SYSLOG_LEVELS.get(int(ln.get("priority", 6)) & 7, "info"),
             "msg": ln.get("msg", "")} for ln in lines]


def usage_add(r, loads: list[dict[str, Any]], now: float) -> None:
    """Bytes each Wi-Fi device moved, summed per hour (kept 35 days)."""
    key = USAGE_PREFIX + time.strftime("%Y-%m-%dT%H", time.localtime(now))
    pipe = r.pipeline()
    for load in loads:
        for c in load["clients"]:
            if c["bytes"]:
                pipe.hincrby(key, c["mac"], int(c["bytes"]))
    pipe.expire(key, USAGE_TTL_S)
    pipe.execute()


class Monitor:
    """One router being watched: the client, the last snapshot and alert state."""

    def __init__(self, redis_client, load_config, save_pin, notify=notify_admins):
        self.r = redis_client
        self.load_config = load_config
        self.save_pin = save_pin
        self.notify = notify
        self.client: RouterClient | None = None
        self.config_key: tuple | None = None
        self.prev: dict[str, Any] | None = None
        self.prev_at = 0.0
        try:
            self.state = json.loads(self.r.get(STATE_KEY) or "{}")
        except (TypeError, ValueError):
            self.state = {}

    def _client(self) -> RouterClient | None:
        cfg = self.load_config()
        if not cfg or not cfg.get("url") or not cfg.get("user") or not cfg.get("password"):
            self.client = None
            return None
        key = (cfg["url"], cfg["user"], cfg["password"], cfg.get("cert_sha256"))
        if key != self.config_key:
            self.client, self.config_key, self.prev = RouterClient(cfg["url"], cfg["user"], cfg["password"],
                                                                   cfg.get("cert_sha256")), key, None
        return self.client

    def _record(self, notices: list[dict[str, Any]], now: float) -> None:
        for n in notices:
            self.r.lpush(EVENTS_KEY, json.dumps({"time": now, "title": n["title"], "body": n["body"], "tag": n["tag"]}))
            if not self.notify(n):
                log.warning(f"[network] notice not delivered: {n['tag']}")
        if notices:
            self.r.ltrim(EVENTS_KEY, 0, EVENTS_MAX - 1)

    def tick(self, now: float | None = None) -> dict[str, Any]:
        now = now or time.time()
        internet = internet_up()
        notices = internet_notices(self.state, internet, now)
        client = self._client()
        if client is None:
            status = {"configured": False, "checked_at": now, "internet": internet}
            self._record(notices, now)
            self._save(status)
            return status
        snap, error = None, None
        try:
            if not client.pin:
                fingerprint = client.verify_certificate()
                self.save_pin(fingerprint)
                client.pin = fingerprint
            snap = collect(client, int(self.state.get("last_log_id", 0)))
        except CertificateChanged as e:
            error = str(e)
        except RouterError as e:
            error = str(e)
        except Exception as e:  # a reply in an unexpected shape must not stop monitoring
            log.error(f"[network] collect failed: {type(e).__name__}: {e}")
            error = f"unexpected reply ({type(e).__name__})"
        notices += router_notices(self.state, snap, now, error)
        if snap is None:
            status = {**json.loads(self.r.get(STATUS_KEY) or "{}"), "configured": True, "connected": False,
                      "error": error, "internet": internet, "checked_at": now}
            self._record(notices, now)
            self._save(status)
            return status

        loads = channel_load(self.prev, snap, now - self.prev_at)
        notices += airtime_notices(self.state, loads, snap)
        notices += flap_notices(self.state, snap, now)
        notices += capacity_notices(self.state, snap)
        self._record(notices, now)

        entries = log_entries(snap["log"])
        if entries:
            pipe = self.r.pipeline()
            for e in entries:
                pipe.lpush(LOG_KEY, json.dumps(e))
            pipe.ltrim(LOG_KEY, 0, LOG_MAX - 1)
            pipe.execute()
            self.state["last_log_id"] = max(e["id"] for e in entries)
        if loads:
            usage_add(self.r, loads, now)

        status = {"configured": True, "connected": True, "error": None, **status_view(snap, loads, internet, now)}
        self.prev, self.prev_at = snap, now
        self._save(status)
        return status

    def _save(self, status: dict[str, Any]) -> None:
        self.r.set(STATUS_KEY, json.dumps(status))
        self.r.set(STATE_KEY, json.dumps(self.state))
