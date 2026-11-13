"""Cast watcher: record everything around a Cast device's session changes.

A diagnostic, read-only. It watches one Cast device (a TV box), and every time
the app or song it reports changes it saves, in one JSON line:

- what the device itself reports over Cast (app, standby, state, song),
- its Spotify Connect answer (``getInfo``, which names an active account when
  there is one; keys and ids are left out),
- the router's view for the window before it (``data/network/router.db``,
  forwarded by the router): every device that looked up a Spotify server, and
  the Wi-Fi joins/leaves and DHCP lines.

When a session from one of ``CAST_WATCH_ALERT_APPS`` (Android TV's own apps by
default) shows a song, admins get a notice too, at most once per
``CAST_WATCH_ALERT_EVERY_S``. It never sends anything to the device.

Configuration (environment, read once at start; no defaults for what is
specific to a household):
  CAST_WATCH_HOST           the device's address (required)
  CAST_WATCH_NETWORK_DB     the router store (required)
  CAST_WATCH_OUT            where to append events (required)
  CAST_WATCH_WINDOW_S       how far back the router view goes (180)
  CAST_WATCH_ALERT_APPS     comma-separated Cast app ids that alert (AndroidNativeApp)
  CAST_WATCH_ALERT_EVERY_S  minimum seconds between notices (600)
  TELEMETRY_SVC_URL, INTERNAL_SECRET  for notices (required)

Run it with scripts/cast_watch.sh, which starts it in the hub's image.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
from typing import Any

SPOTIFY_HINTS = ("spotify", "scdn.co", "spotifycdn")
ROUTER_SOURCES = ("hostapd", "dnsmasq-dhcp", "netifd")
HIDDEN_CONNECT_KEYS = ("publicKey", "deviceID", "clientID", "groupStatus")


def env(name: str, default: str | None = None) -> str:
    value = os.environ.get(name, default)
    if value is None or value == "":
        sys.exit(f"cast_watch: {name} must be set")
    return value


def cast_snapshot(cs: Any, ms: Any) -> dict[str, Any]:
    """What the device reports over Cast, as plain values."""
    return {
        "app_id": getattr(cs, "app_id", None),
        "app": getattr(cs, "display_name", None),
        "standby": getattr(cs, "is_stand_by", None),
        "state": getattr(ms, "player_state", None) if ms is not None else None,
        "title": getattr(ms, "title", None) if ms is not None else None,
        "artist": getattr(ms, "artist", None) if ms is not None else None,
        "content_id": getattr(ms, "content_id", None) if ms is not None else None,
        "position": getattr(ms, "current_time", None) if ms is not None else None,
    }


def change_key(snap: dict[str, Any]) -> tuple:
    return snap["app_id"], snap["app"], snap["state"], snap["title"], snap["standby"]


def should_alert(snap: dict[str, Any], alert_apps: set[str]) -> bool:
    return bool(snap.get("title")) and snap.get("app_id") in alert_apps


def public_connect_info(info: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in info.items() if k not in HIDDEN_CONNECT_KEYS}


def router_view(db_path: str, since: float) -> dict[str, Any]:
    """Spotify lookups per device and the Wi-Fi/DHCP lines since ``since``."""
    db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5)
    try:
        like = " or ".join("domain like ?" for _ in SPOTIFY_HINTS)
        lookups = [
            {"device": d, "ip": ip, "domain": dom, "count": c, "last": round(last, 1)}
            for d, ip, dom, c, last in db.execute(
                f"select device, ip, domain, count, last from dns_hourly where last >= ? and ({like}) order by last",
                (since, *[f"%{h}%" for h in SPOTIFY_HINTS]))
        ]
        src = " or ".join("source like ?" for _ in ROUTER_SOURCES)
        lines = [
            {"time": round(t, 1), "source": s, "msg": m}
            for t, s, m in db.execute(
                f"select time, source, msg from router_log where time >= ? and ({src}) order by time",
                (since, *[f"{x}%" for x in ROUTER_SOURCES]))
        ]
    finally:
        db.close()
    return {"spotify_lookups": lookups, "wifi_and_dhcp": lines}


def notice_for(event: dict[str, Any]) -> dict[str, Any]:
    cast = event["cast"]
    devices = sorted({x["device"] for x in event["router"].get("spotify_lookups", [])})
    who = (event.get("spotify_connect") or {}).get("activeUser") or "no account named"
    body = (f"{cast['app']} reports {cast['state']} \"{cast['title']}\" (standby {cast['standby']}). "
            f"Spotify Connect: {who}. Devices that looked up Spotify in the last few minutes: "
            f"{', '.join(devices) or 'none'}. Full record saved by the Cast watcher.")
    return {"title": "Cast watcher: a session appeared on the TV box", "body": body,
            "tag": "admin:cast-watch", "priority": 3}


def spotify_connect_info(host: str) -> dict[str, Any] | None:
    import urllib.request

    from zeroconf import IPVersion, ServiceBrowser, Zeroconf

    kind = "_spotify-connect._tcp.local."
    names: set[str] = set()

    class Listener:
        def add_service(self, zc, t, n):
            names.add(n)

        def update_service(self, zc, t, n):
            names.add(n)

        def remove_service(self, zc, t, n):
            pass

    zc = Zeroconf(ip_version=IPVersion.V4Only)
    try:
        ServiceBrowser(zc, kind, Listener())
        time.sleep(4)
        for name in names:
            info = zc.get_service_info(kind, name)
            if info is None or host not in info.parsed_addresses():
                continue
            path = (info.properties.get(b"CPath") or b"/").decode()
            with urllib.request.urlopen(f"http://{host}:{info.port}{path}?action=getInfo", timeout=5) as resp:
                return public_connect_info(json.load(resp))
    except Exception as exc:  # a diagnostic never stops on one failed probe
        return {"error": f"{type(exc).__name__}: {exc}"}
    finally:
        zc.close()
    return None


def send_notice(url: str, secret: str, notice: dict[str, Any]) -> None:
    import requests

    try:
        requests.post(f"{url.rstrip('/')}/api/telemetry/notify",
                      json={"kind": "admin", **notice, "user": "@admins", "category": "admin"},
                      headers={"X-Internal-Secret": secret}, timeout=15)
    except Exception as exc:
        print(f"cast_watch: notice not sent: {exc}", flush=True)


def main() -> None:
    import pychromecast

    host = env("CAST_WATCH_HOST")
    db_path = env("CAST_WATCH_NETWORK_DB")
    out = env("CAST_WATCH_OUT")
    window = float(env("CAST_WATCH_WINDOW_S", "180"))
    alert_apps = {a.strip() for a in env("CAST_WATCH_ALERT_APPS", "AndroidNativeApp").split(",") if a.strip()}
    alert_every = float(env("CAST_WATCH_ALERT_EVERY_S", "600"))
    telemetry, secret = env("TELEMETRY_SVC_URL"), env("INTERNAL_SECRET")

    casts, browser = pychromecast.get_chromecasts(known_hosts=[host], timeout=15)
    cc = next((c for c in casts if c.cast_info.host == host), None)
    if cc is None:
        sys.exit(f"cast_watch: no Cast device answered at {host}")
    cc.wait(timeout=15)
    print(f"cast_watch: watching {cc.cast_info.friendly_name} at {host}", flush=True)
    last_key, last_alert = None, 0.0
    try:
        while True:
            snap = cast_snapshot(cc.status, cc.media_controller.status) if cc.status else None
            if snap is not None and change_key(snap) != last_key:
                last_key = change_key(snap)
                now = time.time()
                event: dict[str, Any] = {"at": now, "when": time.strftime("%Y-%m-%d %H:%M:%S %Z"), "cast": snap}
                if snap["title"]:
                    event["spotify_connect"] = spotify_connect_info(host)
                try:
                    event["router"] = router_view(db_path, now - window)
                except Exception as exc:
                    event["router"] = {"error": f"{type(exc).__name__}: {exc}"}
                with open(out, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps(event) + "\n")
                print(f"cast_watch: {event['when']} {snap['app']} {snap['state']} {snap['title']!r}", flush=True)
                if should_alert(snap, alert_apps) and now - last_alert >= alert_every:
                    last_alert = now
                    send_notice(telemetry, secret, notice_for(event))
            time.sleep(1)
    finally:
        browser.stop_discovery()


if __name__ == "__main__":
    main()
