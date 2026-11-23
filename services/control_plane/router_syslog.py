"""The router's own log, forwarded to Jarvis as it happens (docs/NETWORK.md).

The router's logd sends every line it logs (system, Wi-Fi, firewall, DHCP and,
with dnsmasq's ``logqueries`` on, every DNS lookup) to this receiver over TCP.
Polling the router's log once a minute loses lines whenever more than its small
buffer arrives in a minute, which DNS lookups always do; forwarding loses none.

Two things are kept, in one SQLite file under ``NETWORK_DATA_DIR``:

* **Sites per device:** each DNS lookup becomes a count per hour, device and
  domain. The device is the MAC the router monitor last saw at the asking
  address (``network:status``), so a phone that changes address is still one
  device. Only ``query[...]`` lines count; dnsmasq's ``forwarded``/``reply``/
  ``cached`` lines say nothing new about who asked.
* **The router log:** every other line, searchable, for Admin > Network.

Both are kept ``ROUTER_LOG_RETENTION_DAYS`` (30). Lines are accepted only from
the router's address (from its login in Identity, or ``ROUTER_SYSLOG_ALLOW``).
Settings are read once at startup; ``ROUTER_SYSLOG_PORT=0`` turns this off.
"""
from __future__ import annotations

import contextlib
import ipaddress
import json
import logging
import os
import queue
import re
import socket
import socketserver
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

log = logging.getLogger("control_plane.router_syslog")

# Settings come from the environment once, when the service starts; a change needs a restart.
PORT = int(os.getenv("ROUTER_SYSLOG_PORT", "5514") or 0)
DATA_DIR = Path(os.getenv("NETWORK_DATA_DIR", "/data/network"))
RETENTION_DAYS = int(os.getenv("ROUTER_LOG_RETENTION_DAYS", "30") or 30)
ALLOW = {a.strip() for a in os.getenv("ROUTER_SYSLOG_ALLOW", "").split(",") if a.strip()}

FLUSH_SECONDS = 2.0
MAX_LINE = 4096
LEVELS = ("emerg", "alert", "crit", "err", "warning", "notice", "info", "debug")

SCHEMA = """
CREATE TABLE IF NOT EXISTS dns_hourly (
    hour TEXT NOT NULL,          -- local time, %Y-%m-%dT%H
    device TEXT NOT NULL,        -- MAC when known, else the asking address
    ip TEXT NOT NULL,
    domain TEXT NOT NULL,
    count INTEGER NOT NULL,
    last REAL NOT NULL,
    PRIMARY KEY (hour, device, domain)
);
CREATE INDEX IF NOT EXISTS dns_hourly_device ON dns_hourly(device, hour);
CREATE TABLE IF NOT EXISTS router_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    time REAL NOT NULL,
    level TEXT NOT NULL,
    source TEXT NOT NULL,
    msg TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS router_log_time ON router_log(time);
CREATE TABLE IF NOT EXISTS dhcp_fingerprint (
    mac TEXT PRIMARY KEY,
    vendor TEXT NOT NULL DEFAULT '',
    options TEXT NOT NULL DEFAULT '',
    last REAL NOT NULL
);
"""

#: How long the parts of one DHCP exchange are waited for.
DHCP_EXCHANGE_SECONDS = 30

# "<30>Nov 12 20:51:02 router dnsmasq[1234]: query[A] example.com from 192.0.2.7"
# logd sends RFC 3164; the hostname is optional and some senders put a version after the priority.
_LINE = re.compile(r"^<(?P<pri>\d{1,3})>(?:1 )?(?:(?:\w{3} +\d{1,2} [\d:]{8}|\S+T\S+) )?"
                   r"(?:(?P<host>[\w.-]+) )?(?P<tag>[^\s:\[]+)(?:\[\d+\])?: ?(?P<msg>.*)$")
# With log-queries=extra (OpenWrt's default when logqueries is on) each line starts with the
# query's serial and the asker's address/port: "40 192.0.2.7/52240 query[A] example.com from 192.0.2.7".
_EXTRA = re.compile(r"^\d+ [0-9a-fA-F.:]+/\d+ ")
_QUERY = re.compile(r"^query\[(?P<type>[A-Z0-9]+)\] (?P<domain>\S+) from (?P<ip>[0-9a-fA-F.:]+)$")
# Answers and their sources, one or more per lookup; "/tmp/hosts/dhcp... name is 192.0.2.20" is an
# answer from a hosts file (any path).
_DNS_CHATTER = re.compile(r"^(forwarded|reply|cached|cached-stale|config|validation|/\S+ \S+ is |DHCP|"
                          r"query\[|dnssec|NODATA|NXDOMAIN)")


# With dnsmasq's ``logdhcp`` on, every line of one DHCP exchange starts with its transaction
# number: "3612345678 vendor class: android-dhcp-14", "3612345678 requested options: 1:netmask,
# 3:router, ...", "3612345678 DHCPREQUEST(br-lan) 192.0.2.50 02:00:00:00:00:01". The vendor class
# and the list of options a client asks for identify its OS family without any app on it.
_DHCP_XID = re.compile(r"^(?P<xid>\d{3,}) (?P<rest>.*)$")
_DHCP_MAC = re.compile(r"^DHCP[A-Z]+\([^)]*\) (?:[0-9.]+ )?(?P<mac>[0-9a-fA-F]{2}(?::[0-9a-fA-F]{2}){5})\b")
_DHCP_VENDOR = re.compile(r"^vendor class: (?P<v>.+)$")
_DHCP_OPTIONS = re.compile(r"^requested options: (?P<o>.+)$")
_DHCP_DETAIL = re.compile(r"^(vendor class|user class|requested options|client provides name|tags|"
                          r"available DHCP|broadcast response|sent size|next server|bootfile|"
                          r"server name|client MAC|\s*\d+ option)")


def _parse_dhcp(msg: str) -> tuple[str, dict[str, Any] | None]:
    """Strip a ``logdhcp`` transaction number; returns (message, fingerprint part or None)."""
    m = _DHCP_XID.match(msg)
    if not m:
        return msg, None
    xid, rest = m.group("xid"), m.group("rest").strip()
    part: dict[str, Any] = {"xid": xid}
    if mm := _DHCP_MAC.match(rest):
        part["mac"] = mm.group("mac").lower()
    elif mv := _DHCP_VENDOR.match(rest):
        part["vendor"] = mv.group("v").strip()
    elif mo := _DHCP_OPTIONS.match(rest):
        part["options"] = [o.split(":", 1)[0].strip() for o in mo.group("o").split(",") if o.strip()]
    if _DHCP_DETAIL.match(rest):
        part["skip"] = True
    return rest, part


def parse(line: str) -> dict[str, Any] | None:
    """One syslog line as ``{"level", "source", "msg"}`` plus ``dns`` for a lookup; None if unreadable."""
    m = _LINE.match(line.strip())
    if not m:
        return None
    level = LEVELS[int(m.group("pri")) & 7]
    tag, msg = m.group("tag"), m.group("msg").strip()
    out: dict[str, Any] = {"level": level, "source": tag, "msg": msg}
    if tag == "dnsmasq":
        msg = _EXTRA.sub("", msg)
        q = _QUERY.match(msg)
        if q:
            out["dns"] = {"type": q.group("type"), "domain": q.group("domain").rstrip(".").lower(),
                          "ip": q.group("ip")}
        elif _DNS_CHATTER.match(msg):
            out["skip"] = True
    elif tag == "dnsmasq-dhcp":
        msg, part = _parse_dhcp(msg)
        out["msg"] = msg
        if part is not None:
            out["dhcp"] = part
            if part.pop("skip", False):
                out["skip"] = True
    return out


def _local_hour(t: float) -> str:
    return time.strftime("%Y-%m-%dT%H", time.localtime(t))


class Store:
    """The SQLite file; one writer thread, readers open their own connections."""

    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.executescript(SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=10)
        db.execute("PRAGMA journal_mode=WAL")
        return db

    def write(self, lookups: dict[tuple[str, str, str], list], lines: list[tuple[float, str, str, str]]) -> None:
        with self._connect() as db:
            db.executemany(
                "INSERT INTO dns_hourly(hour, device, ip, domain, count, last) VALUES (?,?,?,?,?,?) "
                "ON CONFLICT(hour, device, domain) DO UPDATE SET count = count + excluded.count, "
                "last = max(last, excluded.last), ip = excluded.ip",
                [(h, dev, ip, dom, n, last) for (h, dev, dom), (ip, n, last) in lookups.items()])
            db.executemany("INSERT INTO router_log(time, level, source, msg) VALUES (?,?,?,?)", lines)

    def write_fingerprints(self, rows: list[tuple[str, str, str, float]]) -> None:
        """(mac, vendor, options "1,3,6", time); an empty field keeps what is stored."""
        with self._connect() as db:
            db.executemany(
                "INSERT INTO dhcp_fingerprint(mac, vendor, options, last) VALUES (?,?,?,?) "
                "ON CONFLICT(mac) DO UPDATE SET "
                "vendor = CASE WHEN excluded.vendor != '' THEN excluded.vendor ELSE vendor END, "
                "options = CASE WHEN excluded.options != '' THEN excluded.options ELSE options END, "
                "last = excluded.last", rows)

    def fingerprints(self) -> dict[str, dict[str, Any]]:
        with self._connect() as db:
            return {mac: {"vendor": v, "options": o, "last": last}
                    for mac, v, o, last in db.execute("SELECT mac, vendor, options, last FROM dhcp_fingerprint")}

    def prune(self, now: float) -> None:
        cutoff = now - RETENTION_DAYS * 86400
        with self._connect() as db:
            db.execute("DELETE FROM dns_hourly WHERE hour < ?", (_local_hour(cutoff),))
            db.execute("DELETE FROM dhcp_fingerprint WHERE last < ?", (cutoff,))
            db.execute("DELETE FROM router_log WHERE time < ?", (cutoff,))

    # -- reads (Admin > Network) ---------------------------------------------------------------
    def sites(self, since: float, device: str | None, limit: int) -> list[dict[str, Any]]:
        sql = ("SELECT device, max(ip), domain, sum(count), max(last) FROM dns_hourly WHERE hour >= ?"
               + (" AND device = ?" if device else "") + " GROUP BY device, domain ORDER BY sum(count) DESC LIMIT ?")
        args: tuple = (_local_hour(since), device, limit) if device else (_local_hour(since), limit)
        with self._connect() as db:
            return [{"device": d, "ip": ip, "domain": dom, "lookups": n, "last": last}
                    for d, ip, dom, n, last in db.execute(sql, args)]

    def device_totals(self, since: float) -> list[dict[str, Any]]:
        """Each device's lookups since ``since``: how many, of how many domains, the last one; most first."""
        sql = ("SELECT device, max(ip), sum(count), count(DISTINCT domain), max(last) FROM dns_hourly"
               " WHERE hour >= ? GROUP BY device ORDER BY sum(count) DESC")
        with self._connect() as db:
            return [{"device": d, "ip": ip, "lookups": n, "domains": k, "last": last}
                    for d, ip, n, k, last in db.execute(sql, (_local_hour(since),))]

    def log(self, limit: int, worst: str | None, needle: str | None) -> list[dict[str, Any]]:
        levels = LEVELS[: LEVELS.index(worst) + 1] if worst in LEVELS else LEVELS
        sql = (f"SELECT id, time, level, source, msg FROM router_log WHERE level IN ({','.join('?' * len(levels))})"
               + (" AND (lower(msg) LIKE ? OR lower(source) LIKE ?)" if needle else "") + " ORDER BY id DESC LIMIT ?")
        args = [*levels, *([f"%{needle.lower()}%"] * 2 if needle else []), limit]
        with self._connect() as db:
            return [{"id": i, "time": int(t), "level": lv, "msg": f"{src}: {msg}"}
                    for i, t, lv, src, msg in db.execute(sql, args)]

    def latest(self) -> float | None:
        with self._connect() as db:
            row = db.execute("SELECT max(time) FROM router_log").fetchone()
        return row[0] if row else None


class Receiver:
    """Accepts the router's lines, resolves devices, and batches writes to the store."""

    def __init__(self, store: Store, devices, allowed) -> None:
        self.store = store
        self.devices = devices  # () -> {ip: mac}, from the router monitor's last status
        self.allowed = allowed  # () -> set of addresses that may send
        self.q: queue.Queue[tuple[float, str]] = queue.Queue(maxsize=100_000)
        self.dropped = 0
        self.refused: set[str] = set()
        # logdhcp transaction number -> {"mac", "vendor", "options", "t"}; the parts of one
        # exchange can arrive in either order and across drains.
        self.dhcp: dict[str, dict[str, Any]] = {}

    def accept(self, sender: str, line: str, now: float | None = None) -> None:
        allowed = self.allowed()
        if sender not in allowed:
            if sender not in self.refused:
                self.refused.add(sender)
                log.warning(f"[router-syslog] lines from {sender} refused; only the router ({', '.join(sorted(allowed)) or 'unknown'}) may send")
            return
        try:
            self.q.put_nowait((now or time.time(), line[:MAX_LINE]))
        except queue.Full:
            self.dropped += 1

    def drain(self) -> int:
        """Write what has arrived; returns how many lines."""
        lookups: dict[tuple[str, str, str], list] = {}
        lines: list[tuple[float, str, str, str]] = []
        ip_to_mac = None
        n = 0
        while True:
            try:
                t, raw = self.q.get_nowait()
            except queue.Empty:
                break
            n += 1
            e = parse(raw)
            if e is not None and e.get("dhcp"):
                part = e["dhcp"]
                cur = self.dhcp.setdefault(part["xid"], {"t": t})
                cur["t"] = t
                if "options" in part:
                    cur.setdefault("options", []).extend(part["options"])
                for key in ("mac", "vendor"):
                    if key in part:
                        cur[key] = part[key]
            if e is None or e.get("skip"):
                continue
            dns = e.get("dns")
            if dns is None:
                lines.append((t, e["level"], e["source"], e["msg"]))
                continue
            if ip_to_mac is None:
                ip_to_mac = self.devices()
            device = ip_to_mac.get(dns["ip"], dns["ip"])
            k = (_local_hour(t), device, dns["domain"])
            cur = lookups.get(k)
            lookups[k] = [dns["ip"], (cur[1] if cur else 0) + 1, t]
        if lookups or lines:
            self.store.write(lookups, lines)
        self._flush_dhcp(time.time())
        return n

    def _flush_dhcp(self, now: float) -> None:
        """Store each exchange's fingerprint once its MAC is known; forget stale exchanges."""
        rows = []
        for xid, cur in list(self.dhcp.items()):
            if cur.get("mac") and (cur.get("vendor") or cur.get("options")):
                rows.append((cur["mac"], cur.get("vendor", ""), ",".join(cur.get("options", [])), cur["t"]))
            if now - cur["t"] > DHCP_EXCHANGE_SECONDS:
                self.dhcp.pop(xid, None)
        if len(self.dhcp) > 2000:
            self.dhcp.clear()
        if rows:
            self.store.write_fingerprints(rows)


class _Handler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        receiver: Receiver = self.server.receiver  # type: ignore[attr-defined]
        sender = self.client_address[0]
        for raw in self.rfile:
            receiver.accept(sender, raw.decode("utf-8", errors="replace").rstrip("\r\n"))


class _TCP(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True


def devices_from_status(r) -> dict[str, str]:
    """{ip: mac} from the router monitor's last status."""
    try:
        status = json.loads(r.get("network:status") or "{}")
    except (ValueError, TypeError):
        return {}
    return {d["ip"]: mac for mac, d in (status.get("devices") or {}).items() if isinstance(d, dict) and d.get("ip")}


def router_addresses(load_config) -> set[str]:
    """The router's address from its login in Identity (and ROUTER_SYSLOG_ALLOW)."""
    out = set(ALLOW)
    try:
        cfg = load_config() or {}
    except Exception as e:  # Identity starting: keep only the explicit list for now
        log.debug(f"[router-syslog] router address unavailable: {e}")
        return out
    host = urlparse(cfg.get("url") or "").hostname
    if host:
        try:
            out.add(str(ipaddress.ip_address(host)))
        except ValueError:
            with contextlib.suppress(OSError):  # an unresolvable name: the explicit list still applies
                out.add(socket.gethostbyname(host))
    return out


_receiver: Receiver | None = None
_store: Store | None = None


def store() -> Store | None:
    return _store


def start(r, load_config) -> None:
    """Listen for the router's log, if ``ROUTER_SYSLOG_PORT`` is set (it is, by default)."""
    global _receiver, _store
    if not PORT:
        log.info("[router-syslog] off (ROUTER_SYSLOG_PORT=0)")
        return
    try:
        _store = Store(DATA_DIR / "router.db")
    except (OSError, sqlite3.Error) as e:
        log.error(f"[router-syslog] cannot open {DATA_DIR / 'router.db'} ({e}); the router's log is not being kept")
        return
    cache: dict[str, Any] = {"at": 0.0, "allowed": set()}

    def allowed() -> set[str]:
        if time.time() - cache["at"] > 300:
            cache["allowed"], cache["at"] = router_addresses(load_config), time.time()
        return cache["allowed"]

    _receiver = Receiver(_store, lambda: devices_from_status(r), allowed)
    server = _TCP(("0.0.0.0", PORT), _Handler)
    server.receiver = _receiver  # type: ignore[attr-defined]
    threading.Thread(target=server.serve_forever, name="router-syslog", daemon=True).start()

    def writer() -> None:
        last_prune = 0.0
        while True:
            time.sleep(FLUSH_SECONDS)
            try:
                _receiver.drain()
                if time.time() - last_prune > 3600:
                    _store.prune(time.time())
                    last_prune = time.time()
            except Exception as e:  # never let the writer die
                log.error(f"[router-syslog] write failed: {type(e).__name__}: {e}")

    threading.Thread(target=writer, name="router-syslog-writer", daemon=True).start()
    log.info(f"[router-syslog] listening on TCP {PORT}; keeping {RETENTION_DAYS} days in {DATA_DIR / 'router.db'}")
