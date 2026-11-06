# services/execution/handlers/tv_stage.py
"""TV stage: put a live, interactive web page on a household screen.

A party game's big screen is a web page (the arcade's ``/tv/<CODE>``) that
updates itself while players play on their phones. "Casting" it therefore
means getting that *page* running on the TV, not sending the TV a sound file
or a picture of the page. What that takes depends on what the screen is:

==================  =========================================================
Screen              How the page gets there
==================  =========================================================
Google Cast         DashCast receiver (Cast app ``84912283``) navigated to the
(Chromecast,        page. Covers Chromecast, Google TV, most Android TVs, Nest
Google/Android TV,  Hubs and TVs with Chromecast built in. The device is found
Cast built-in)      by mDNS, or by IP from the HA cast entity.
LG webOS            The TV's own web browser, launched at the page through
                    HA's ``webostv.command`` (``system.launcher/launch``).
Samsung Tizen       The TV's own web browser, via the TV's websocket API
                    (``samsungtvws.open_browser``); the TV asks once to allow.
Android TV          An app link through HA's ``androidtv_remote`` when there is
(no Cast)           no Cast receiver; opens in the TV's default browser.
Roku, other         No web engine exists on a Roku, so the page is rendered
video-only          here and sent as a live HLS stream (``stage_stream``) to
                    Media Assistant, or to HA ``play_media`` for other players.
==================  =========================================================

Nothing here names a device. Screens come from Home Assistant (classified by
``classify_communication_devices``) and from Google Cast mDNS; a screen that
appears in both is one screen.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import re
import socket
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

try:
    from services.execution import device_discovery, ha_client, stage_stream
    from services.execution.announce_handlers import classify_communication_devices
except ImportError:  # pragma: no cover - flat import layout used by some scripts
    import device_discovery  # type: ignore[no-redef]
    import ha_client  # type: ignore[no-redef]
    import stage_stream  # type: ignore[no-redef]
    from announce_handlers import classify_communication_devices  # type: ignore[no-redef]

log = logging.getLogger("execution.tv_stage")

DASHCAST_APP_ID = "84912283"
WEBOS_BROWSER_APP = "com.webos.app.browser"
MEDIA_ASSISTANT_CHANNEL_ID = "782875"
MEDIA_SERVER_PORT = int(os.getenv("MEDIA_SERVER_PORT", "8888"))
CAST_DISCOVERY_TIMEOUT_S = 4.0
CAST_CONNECT_TIMEOUT_S = 10.0
SCREEN_CACHE_TTL_S = 30.0
#: Whole show() call, every method included. The gateway gives a request 90 s;
#: an honest "it didn't work" inside that beats a timeout with no reason.
SHOW_BUDGET_S = 55.0
#: Resolving an address must never fall through to a subnet scan here.
IP_LOOKUP_TIMEOUT_S = 8.0
#: A Chromecast woken through Home Assistant takes a while to answer mDNS.
CAST_WAKE_WAIT_S = 18.0
#: Players that can actually show a video stream. Music Assistant wrappers and
#: speakers cannot, and a TV with its own browser is better served by that.
STREAM_PLATFORMS = {"dlna", "generic_tv", "bravia"}
TOKEN_DIR = Path(os.getenv("TV_STAGE_TOKEN_DIR", "/data/tv_stage"))

#: Methods that run the real page on the screen, in preference order.
WEB_METHODS = ("cast_web", "webos_browser", "samsung_browser", "android_link")
#: Methods that send the screen a rendered video of the page.
VIDEO_METHODS = ("roku_stream", "media_stream")

METHOD_LABELS = {
    "cast_web": "Google Cast (live page)",
    "webos_browser": "LG web browser (live page)",
    "samsung_browser": "Samsung web browser (live page)",
    "android_link": "Android TV browser (live page)",
    "roku_stream": "Live video of the game (Roku has no browser)",
    "media_stream": "Live video of the game",
}

_cache: dict[str, Any] = {"at": 0.0, "key": "", "screens": []}


# --------------------------------------------------------------------------- names

def _norm(name: str) -> str:
    """Compare device names across integrations ("Office TV" == "office_tv")."""
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def _name_keys(name: str) -> set[str]:
    """A name and its variant without a trailing Cast word ("Office TV Chromecast")."""
    base = _norm(name)
    keys = {base} if base else set()
    stripped = re.sub(r"(chromecast|googlecast|chrome|cast)$", "", base)
    if stripped:
        keys.add(stripped)
    return keys


# --------------------------------------------------------------------------- discovery

def _discover_cast_devices_sync(timeout: float) -> list[dict[str, Any]]:
    """Every Google Cast device answering mDNS (needs host networking)."""
    try:
        from pychromecast.discovery import discover_chromecasts  # pyright: ignore[reportMissingImports]
    except Exception:
        return []
    browser = None
    try:
        # mDNS records only: connecting to every device just to list it would
        # be slow and would wake sleeping speakers.
        infos, browser = discover_chromecasts(timeout=timeout)
        return [
            {
                "uuid": str(getattr(info, "uuid", "") or ""),
                "name": str(getattr(info, "friendly_name", "") or ""),
                "host": str(getattr(info, "host", "") or ""),
                "port": int(getattr(info, "port", 8009) or 8009),
                "model": str(getattr(info, "model_name", "") or ""),
                "cast_type": str(getattr(info, "cast_type", "") or "cast"),
                "manufacturer": str(getattr(info, "manufacturer", "") or ""),
            }
            for info in infos
        ]
    except Exception as exc:
        log.debug("[tv_stage] cast mDNS discovery failed: %s", exc)
        return []
    finally:
        if browser is not None:
            with contextlib.suppress(Exception):
                browser.stop_discovery()


async def discover_cast_devices(timeout: float = CAST_DISCOVERY_TIMEOUT_S) -> list[dict[str, Any]]:
    try:
        return await asyncio.wait_for(asyncio.to_thread(_discover_cast_devices_sync, timeout), timeout + 3)
    except Exception:
        return []


def _methods_for(platform: str, *, has_cast: bool, companions: dict[str, Any], entity_id: str,
                 os_version: str = "") -> list[str]:
    methods: list[str] = []
    if has_cast:
        methods.append("cast_web")
    if platform == "webos" and entity_id:
        methods.append("webos_browser")
    # Pre-2016 (Orsay) Samsungs have no remote browser control at all.
    if platform == "samsung" and entity_id and "orsay" not in os_version.lower():
        methods.append("samsung_browser")
    if platform == "android_tv" and companions.get("remote_sibling"):
        methods.append("android_link")
    if platform == "roku" and entity_id:
        methods.append("roku_stream")
    elif entity_id and platform in STREAM_PLATFORMS and not methods:
        methods.append("media_stream")
    return methods


def _screen_summary(screen: dict[str, Any]) -> dict[str, Any]:
    methods = screen.get("methods") or []
    web = [m for m in methods if m in WEB_METHODS]
    screen["display"] = "web" if web else ("video" if methods else "none")
    screen["method_labels"] = [METHOD_LABELS.get(m, m) for m in methods]
    return screen


def build_screens(ha_devices: list[dict[str, Any]], cast_devices: list[dict[str, Any]],
                  ha_states: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Merge HA TVs and mDNS Cast devices into one list of physical screens.

    Pure (no I/O) so the merge rules are testable:
    - an HA TV's ``cast_sibling`` is the same screen, not a second one;
    - an mDNS Cast device whose name matches an HA screen (or its Cast
      sibling's name) supplies that screen's Cast address;
    - a Cast device that is audio-only or a speaker group is not a screen;
    - a Cast device HA does not know about is still a screen.
    """
    names_by_entity = {
        str(s.get("entity_id") or ""): str((s.get("attributes") or {}).get("friendly_name") or "")
        for s in ha_states or []
    }
    cast_by_key: dict[str, dict[str, Any]] = {}
    for cd in cast_devices:
        if (cd.get("cast_type") or "cast") != "cast":
            continue
        for key in _name_keys(cd.get("name") or ""):
            cast_by_key.setdefault(key, cd)

    screens: list[dict[str, Any]] = []
    used_cast: set[str] = set()
    folded: set[str] = set()

    tvs = [d for d in ha_devices if (d.get("kind") == "tv")]
    # Fold Cast siblings into their TV first so a TV's Cast entity is never listed twice.
    for d in tvs:
        sib = (d.get("companions") or {}).get("cast_sibling")
        if sib and sib != d.get("entity_id"):
            folded.add(sib)

    for d in tvs:
        eid = str(d.get("entity_id") or "")
        if not eid or eid in folded:
            continue
        platform = str(d.get("platform") or "unknown")
        companions = dict(d.get("companions") or {})
        name = str(d.get("name") or eid)
        cast_entity = companions.get("cast_sibling") or (eid if platform == "cast" else "")
        candidate_names = [name]
        if cast_entity:
            candidate_names.append(names_by_entity.get(cast_entity, ""))
        cast_dev = None
        for cand in candidate_names:
            for key in _name_keys(cand):
                if key in cast_by_key:
                    cast_dev = cast_by_key[key]
                    break
            if cast_dev:
                break
        if cast_dev and cast_dev.get("uuid"):
            used_cast.add(cast_dev["uuid"])
        has_cast = bool(cast_dev or cast_entity)
        screens.append(
            _screen_summary(
                {
                    "id": f"ha:{eid}",
                    "name": name,
                    "platform": platform,
                    "entity_id": eid,
                    "state": str(d.get("state") or "unknown"),
                    "cast_entity": cast_entity or "",
                    "cast": cast_dev,
                    "companions": companions,
                    "methods": _methods_for(platform, has_cast=has_cast, companions=companions, entity_id=eid,
                                            os_version=str(d.get("os_version") or "")),
                    "source": "home_assistant" + ("+mdns" if cast_dev else ""),
                }
            )
        )

    seen_uuid: set[str] = set()
    for cd in cast_devices:
        uid = cd.get("uuid") or ""
        if (cd.get("cast_type") or "cast") != "cast" or not uid or uid in used_cast or uid in seen_uuid:
            continue
        seen_uuid.add(uid)
        screens.append(
            _screen_summary(
                {
                    "id": f"cast:{uid}",
                    "name": cd.get("name") or "Cast device",
                    "platform": "cast",
                    "entity_id": "",
                    "state": "on",
                    "cast_entity": "",
                    "cast": cd,
                    "companions": {},
                    "methods": ["cast_web"],
                    "source": "mdns",
                }
            )
        )

    # One entry per physical TV: a TV and its Music Assistant wrapper often
    # share a Cast entity. Keep the real TV over the wrapper.
    by_cast: dict[str, dict[str, Any]] = {}
    unique: list[dict[str, Any]] = []
    for sc in screens:
        key = sc.get("cast_entity") or ""
        if not key:
            unique.append(sc)
            continue
        kept = by_cast.get(key)
        if kept is None:
            by_cast[key] = sc
            unique.append(sc)
        elif kept["platform"] == "music_assistant" and sc["platform"] != "music_assistant":
            unique[unique.index(kept)] = sc
            by_cast[key] = sc
    screens = [sc for sc in unique if sc["methods"]]

    order = {"web": 0, "video": 1, "none": 2}
    screens.sort(key=lambda s: (order.get(s["display"], 3), s["state"] in ("off", "unavailable"), s["name"].lower()))
    return screens


async def list_screens(ha_url: str, ha_token: str, *, refresh: bool = False) -> list[dict[str, Any]]:
    """Every screen in the house that can show the stage, best first."""
    key = f"{ha_url}|{bool(ha_token)}"
    now = time.time()
    if not refresh and _cache["key"] == key and now - float(_cache["at"]) < SCREEN_CACHE_TTL_S:
        return list(_cache["screens"])

    states: list[dict[str, Any]] = []
    ha_devices: list[dict[str, Any]] = []
    cast_task = asyncio.create_task(discover_cast_devices())
    if ha_url and ha_token:
        with contextlib.suppress(Exception):
            states = await ha_client.get_states(ha_url, ha_token) or []
        with contextlib.suppress(Exception):
            ha_devices = await classify_communication_devices(ha_url, ha_token, all_states=states)
    cast_devices = await cast_task
    screens = build_screens(ha_devices, cast_devices, states)
    _cache.update({"at": now, "key": key, "screens": screens})
    return list(screens)


def public_screen(screen: dict[str, Any]) -> dict[str, Any]:
    """What a phone needs to show a screen picker (no addresses beyond the LAN name)."""
    return {
        "id": screen["id"],
        "name": screen["name"],
        "platform": screen["platform"],
        "state": screen["state"],
        "display": screen["display"],
        "methods": screen["methods"],
        "method_labels": screen["method_labels"],
        "source": screen.get("source", ""),
    }


# --------------------------------------------------------------------------- helpers

def validate_page_url(url: str) -> str:
    parsed = urlparse((url or "").strip())
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("url must be an absolute http(s) URL")
    return parsed.geturl()


def _host_of(url: str) -> str:
    return urlparse(url).hostname or ""


def lan_host_for(peer_ip: str = "") -> str:
    """The address of this host that ``peer_ip`` can reach.

    ``EXECUTION_EXTERNAL_HOST`` wins when set. Otherwise ask the kernel which
    interface routes to the TV (a UDP ``connect`` sends nothing), so the
    answer is right on a multi-homed host and needs no configuration.
    """
    configured = (os.getenv("EXECUTION_EXTERNAL_HOST") or "").strip()
    if configured:
        return configured
    target = peer_ip or "192.0.2.1"  # TEST-NET-1: only used to pick the default route
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect((target, 9))
        return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        sock.close()


async def _entity_ip(ha_url: str, ha_token: str, entity_id: str, device_type: str | None) -> str:
    if not entity_id:
        return ""
    with contextlib.suppress(Exception):
        found = await asyncio.wait_for(
            device_discovery.discover_device(entity_id, ha_url, ha_token, device_type=device_type),
            IP_LOOKUP_TIMEOUT_S,
        )
        if found and found.get("ip"):
            return str(found["ip"])
    return ""


async def _wake(ha_url: str, ha_token: str, entity_id: str) -> None:
    if not (ha_url and ha_token and entity_id):
        return
    with contextlib.suppress(Exception):
        state = await ha_client.get_state(ha_url, ha_token, entity_id)
        if state and state.get("state") in ("off", "standby"):
            await ha_client.call_service(ha_url, ha_token, "media_player", "turn_on", entity_id)
            await asyncio.sleep(3)


# --------------------------------------------------------------------------- methods

def _cast_show_sync(cast_dev: dict[str, Any] | None, host: str, url: str) -> tuple[bool, str]:
    import pychromecast  # pyright: ignore[reportMissingImports]
    from pychromecast.controllers.dashcast import DashCastController  # pyright: ignore[reportMissingImports]

    cc = None
    try:
        h = (cast_dev or {}).get("host") or host
        if not h:
            return False, "No Cast address"
        port = int((cast_dev or {}).get("port") or 8009)
        cc = pychromecast.get_chromecast_from_host((h, port, None, None, None), timeout=CAST_CONNECT_TIMEOUT_S)
        cc.wait(timeout=CAST_CONNECT_TIMEOUT_S)
        dash = DashCastController()
        cc.register_handler(dash)
        # force=True navigates the receiver itself to the page instead of an
        # iframe: an http:// LAN page in the https receiver would be blocked as
        # mixed content, and only a top-level page may autoplay the announcer.
        dash.load_url(url, force=True)
        deadline = time.time() + CAST_CONNECT_TIMEOUT_S
        while time.time() < deadline:
            if (cc.status and cc.status.app_id) == DASHCAST_APP_ID:
                return True, f"Showing on {cc.name or 'the TV'}"
            time.sleep(0.4)
        return False, "Cast device did not start the web receiver"
    finally:
        if cc is not None:
            with contextlib.suppress(Exception):
                cc.disconnect(timeout=2)


async def _find_cast_after_wake(screen: dict[str, Any], ha_url: str, ha_token: str) -> dict[str, Any] | None:
    """Wake a sleeping Cast device through Home Assistant and wait for it on mDNS.

    ``media_player.turn_on`` on a Cast entity starts its idle screen, and over
    HDMI-CEC that also switches the TV on and to its input. A Chromecast that
    was asleep does not answer mDNS until then.
    """
    cast_entity = screen.get("cast_entity") or ""
    if not (ha_url and ha_token and cast_entity):
        return None
    with contextlib.suppress(Exception):
        await ha_client.call_service(ha_url, ha_token, "media_player", "turn_on", cast_entity)
    names = [screen.get("name") or ""]
    with contextlib.suppress(Exception):
        st = await ha_client.get_state(ha_url, ha_token, cast_entity)
        names.append(str(((st or {}).get("attributes") or {}).get("friendly_name") or ""))
    keys = set().union(*[_name_keys(n) for n in names if n])
    deadline = time.time() + CAST_WAKE_WAIT_S
    while time.time() < deadline:
        for cd in await discover_cast_devices(3.0):
            if (cd.get("cast_type") or "cast") == "cast" and _name_keys(cd.get("name") or "") & keys:
                return cd
        await asyncio.sleep(1.0)
    return None


async def show_via_cast(screen: dict[str, Any], url: str, ha_url: str, ha_token: str) -> dict[str, Any]:
    await _wake(ha_url, ha_token, screen.get("entity_id") or "")
    cast_dev = screen.get("cast")
    host = ""
    if not (cast_dev or {}).get("host"):
        cast_dev = await _find_cast_after_wake(screen, ha_url, ha_token)
    if not (cast_dev or {}).get("host") and screen.get("cast_entity"):
        host = await _entity_ip(ha_url, ha_token, screen["cast_entity"], "cast")
    if not (cast_dev or {}).get("host") and not host:
        return {"ok": False, "detail": f"{screen.get('name') or 'The TV'}'s Chromecast did not come online. "
                                       "Check it is powered and on the home Wi-Fi, then try again."}
    screen = {**screen, "cast": cast_dev}
    try:
        ok, detail = await asyncio.wait_for(
            asyncio.to_thread(_cast_show_sync, screen.get("cast"), host, url), CAST_CONNECT_TIMEOUT_S * 3,
        )
    except Exception as exc:
        return {"ok": False, "detail": f"Cast failed: {exc}"}
    return {"ok": ok, "verified": ok, "detail": detail}


async def show_via_webos(screen: dict[str, Any], url: str, ha_url: str, ha_token: str) -> dict[str, Any]:
    eid = screen["entity_id"]
    await _wake(ha_url, ha_token, eid)
    res = await ha_client.call_service(
        ha_url, ha_token, "webostv", "command", eid,
        {"command": "system.launcher/launch", "payload": {"id": WEBOS_BROWSER_APP, "target": url}},
    )
    if not res.get("ok"):
        return {"ok": False, "detail": f"webOS refused the browser launch: {res.get('error')}"}
    verified = False
    for _ in range(10):
        await asyncio.sleep(1)
        st = await ha_client.get_state(ha_url, ha_token, eid) or {}
        src = str((st.get("attributes") or {}).get("source") or "").lower()
        if "browser" in src or "web" in src:
            verified = True
            break
    return {"ok": True, "verified": verified,
            "detail": "LG browser opened" + ("" if verified else " (TV did not report its source)")}


def _samsung_show_sync(ip: str, url: str) -> None:
    from samsungtvws import SamsungTVWS  # pyright: ignore[reportMissingImports]

    TOKEN_DIR.mkdir(parents=True, exist_ok=True)
    token_file = str(TOKEN_DIR / f"samsung_{re.sub(r'[^0-9A-Za-z.]', '_', ip)}.token")
    tv = SamsungTVWS(host=ip, port=8002, token_file=token_file, name="Jarvis", timeout=30)
    try:
        tv.open_browser(url)
    finally:
        with contextlib.suppress(Exception):
            tv.close()


async def show_via_samsung(screen: dict[str, Any], url: str, ha_url: str, ha_token: str) -> dict[str, Any]:
    eid = screen["entity_id"]
    ip = await _entity_ip(ha_url, ha_token, eid, "samsung")
    if not ip:
        return {"ok": False, "detail": "Samsung TV IP not found"}
    await _wake(ha_url, ha_token, eid)
    try:
        await asyncio.wait_for(asyncio.to_thread(_samsung_show_sync, ip, url), 40)
    except Exception as exc:
        return {"ok": False, "detail": f"Samsung browser launch failed (allow 'Jarvis' on the TV the first time): {exc}"}
    return {"ok": True, "verified": False, "detail": "Samsung browser opened"}


async def show_via_android_link(screen: dict[str, Any], url: str, ha_url: str, ha_token: str) -> dict[str, Any]:
    remote = (screen.get("companions") or {}).get("remote_sibling") or ""
    await _wake(ha_url, ha_token, screen.get("entity_id") or "")
    res = await ha_client.call_service(ha_url, ha_token, "remote", "turn_on", remote, {"activity": url})
    if not res.get("ok"):
        return {"ok": False, "detail": f"Android TV refused the link: {res.get('error')}"}
    return {"ok": True, "verified": False, "detail": "Opened the page link (needs a browser app on the TV)"}


async def _start_stream(url: str, peer_ip: str) -> str:
    stream = await stage_stream.start(url)
    return f"http://{lan_host_for(peer_ip)}:{MEDIA_SERVER_PORT}/stage/{stream.sid}/index.m3u8"


async def show_via_roku_stream(screen: dict[str, Any], url: str, ha_url: str, ha_token: str) -> dict[str, Any]:
    import aiohttp

    eid = screen["entity_id"]
    ip = await _entity_ip(ha_url, ha_token, eid, "roku")
    if not ip:
        return {"ok": False, "detail": "Roku IP not found"}
    try:
        hls = await _start_stream(url, ip)
    except Exception as exc:
        return {"ok": False, "detail": str(exc)}
    params = {"t": "v", "u": hls, "videoName": "Family Game", "videoFormat": "hls"}
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as client:
            await client.post(f"http://{ip}:8060/keypress/PowerOn", data=b"")
            async with client.post(f"http://{ip}:8060/launch/{MEDIA_ASSISTANT_CHANNEL_ID}", params=params, data=b"") as resp:
                if resp.status not in (200, 204):
                    return {"ok": False, "detail": f"Roku Media Assistant returned {resp.status}", "stream_url": hls}
    except Exception as exc:
        return {"ok": False, "detail": f"Roku unreachable: {exc}", "stream_url": hls}
    return {"ok": True, "verified": False, "detail": "Live video sent to Roku Media Assistant", "stream_url": hls}


async def show_via_media_stream(screen: dict[str, Any], url: str, ha_url: str, ha_token: str) -> dict[str, Any]:
    eid = screen["entity_id"]
    ip = await _entity_ip(ha_url, ha_token, eid, None)
    try:
        hls = await _start_stream(url, ip)
    except Exception as exc:
        return {"ok": False, "detail": str(exc)}
    await _wake(ha_url, ha_token, eid)
    res = await ha_client.call_service(
        ha_url, ha_token, "media_player", "play_media", eid,
        {"media_content_id": hls, "media_content_type": "video"},
    )
    if not res.get("ok"):
        return {"ok": False, "detail": f"Player refused the stream: {res.get('error')}", "stream_url": hls}
    return {"ok": True, "verified": False, "detail": "Live video sent to the player", "stream_url": hls}


_HANDLERS = {
    "cast_web": show_via_cast,
    "webos_browser": show_via_webos,
    "samsung_browser": show_via_samsung,
    "android_link": show_via_android_link,
    "roku_stream": show_via_roku_stream,
    "media_stream": show_via_media_stream,
}


# --------------------------------------------------------------------------- entry points

async def find_screen(screen_id: str, ha_url: str, ha_token: str) -> dict[str, Any] | None:
    for refresh in (False, True):
        for s in await list_screens(ha_url, ha_token, refresh=refresh):
            if s["id"] == screen_id or (s.get("entity_id") and f"ha:{s['entity_id']}" == screen_id) \
                    or s.get("entity_id") == screen_id:
                return s
    return None


async def show(screen_id: str, url: str, ha_url: str, ha_token: str, *, method: str = "") -> dict[str, Any]:
    """Show ``url`` on a screen, trying its methods best-first until one works."""
    page = validate_page_url(url)
    screen = await find_screen(screen_id, ha_url, ha_token)
    if screen is None:
        return {"success": False, "error": f"Screen '{screen_id}' was not found", "attempts": []}
    methods = [method] if method and method in screen["methods"] else list(screen["methods"])
    if not methods:
        return {"success": False, "error": f"{screen['name']} cannot show a page", "attempts": [],
                "screen": public_screen(screen)}
    attempts: list[dict[str, Any]] = []
    deadline = time.time() + SHOW_BUDGET_S
    for m in methods:
        left = deadline - time.time()
        if left < 5:
            attempts.append({"method": m, "ok": False, "detail": "Skipped: out of time"})
            continue
        handler = _HANDLERS[m]
        try:
            res = await asyncio.wait_for(handler(screen, page, ha_url, ha_token), left)
        except TimeoutError:
            res = {"ok": False, "detail": f"{METHOD_LABELS.get(m, m)} did not respond in time"}
        except Exception as exc:
            res = {"ok": False, "detail": f"{exc.__class__.__name__}: {exc}"}
        attempts.append({"method": m, **res})
        log.warning("[tv_stage] %s via %s -> %s (%s)", screen["name"], m, res.get("ok"), res.get("detail"))
        if res.get("ok"):
            return {
                "success": True,
                "screen": public_screen(screen),
                "method": m,
                "method_label": METHOD_LABELS.get(m, m),
                "display": "web" if m in WEB_METHODS else "video",
                "verified": bool(res.get("verified")),
                "detail": res.get("detail", ""),
                "url": page,
                "attempts": attempts,
            }
    return {"success": False, "screen": public_screen(screen), "url": page, "attempts": attempts,
            "error": attempts[-1].get("detail") if attempts else "No method worked"}


def _cast_stop_sync(cast_dev: dict[str, Any] | None, host: str) -> bool:
    import pychromecast  # pyright: ignore[reportMissingImports]

    h = host or (cast_dev or {}).get("host") or ""
    if not h:
        return False
    cc = pychromecast.get_chromecast_from_host((h, int((cast_dev or {}).get("port") or 8009), None, None, None),
                                               timeout=CAST_CONNECT_TIMEOUT_S)
    try:
        cc.wait(timeout=CAST_CONNECT_TIMEOUT_S)
        if cc.status and cc.status.app_id == DASHCAST_APP_ID:
            cc.quit_app()
            return True
        return False
    finally:
        with contextlib.suppress(Exception):
            cc.disconnect(timeout=2)


async def stop(screen_id: str, ha_url: str, ha_token: str, *, url: str = "") -> dict[str, Any]:
    """Take the stage off a screen and end any stream rendered for it."""
    stopped_streams = await stage_stream.stop_url(url) if url else 0
    screen = await find_screen(screen_id, ha_url, ha_token)
    if screen is None:
        return {"success": stopped_streams > 0, "streams_stopped": stopped_streams}
    actions: list[str] = []
    if "cast_web" in screen["methods"]:
        host = ""
        if not (screen.get("cast") or {}).get("host") and screen.get("cast_entity"):
            host = await _entity_ip(ha_url, ha_token, screen["cast_entity"], "cast")
        with contextlib.suppress(Exception):
            if await asyncio.to_thread(_cast_stop_sync, screen.get("cast"), host):
                actions.append("cast_quit")
    eid = screen.get("entity_id") or ""
    if eid and screen["platform"] == "webos":
        res = await ha_client.call_service(ha_url, ha_token, "webostv", "command", eid,
                                           {"command": "system.launcher/close", "payload": {"id": WEBOS_BROWSER_APP}})
        if res.get("ok"):
            actions.append("webos_close_browser")
    if eid and (screen["platform"] == "roku" or "media_stream" in screen["methods"]):
        res = await ha_client.call_service(ha_url, ha_token, "media_player", "media_stop", eid)
        if res.get("ok"):
            actions.append("media_stop")
    return {"success": True, "screen": public_screen(screen), "actions": actions, "streams_stopped": stopped_streams}
