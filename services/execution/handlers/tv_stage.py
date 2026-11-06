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
import ipaddress
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
#: TVs whose Cast entity in HA is their own built-in receiver, not a dongle.
BUILT_IN_CAST_PLATFORMS = {"android_tv", "google_tv"}
#: Cast devices on another subnet never answer mDNS here (multicast does not
#: cross routers), so they are found by unicast. Where to look, best first:
#: the router monitor's own view (every LAN network and every device with an
#: address, ``network:status``), then the /24s beside this host's, /24s where a
#: Cast device was found before, and, as a hint only, Home Assistant's Cast
#: "known hosts". Each candidate is asked who it is (``/setup/eureka_info``),
#: which gives its name and Cast id.
CAST_SWEEP_TTL_S = 600.0
CAST_PORT_PROBE_S = 0.8
CAST_INFO_TIMEOUT_S = 3.0
#: How many /24s either side of this host's own are swept.
CAST_NEIGHBOUR_NETS = int(os.getenv("CAST_NEIGHBOUR_NETS", "1"))
TOKEN_DIR = Path(os.getenv("TV_STAGE_TOKEN_DIR", "/data/tv_stage"))

#: Methods that run the real page on the screen, in preference order.
WEB_METHODS = ("cast_web", "webos_direct", "webos_browser", "samsung_browser", "android_link")
#: Methods that send the screen a rendered video of the page.
VIDEO_METHODS = ("roku_stream", "media_stream")

METHOD_LABELS = {
    "cast_web": "Google Cast (live page)",
    "webos_direct": "LG web browser (live page)",
    "webos_browser": "LG web browser via Home Assistant (live page)",
    "samsung_browser": "Samsung web browser (live page)",
    "android_link": "Android TV browser (live page)",
    "roku_stream": "Live video of the game (Roku has no browser)",
    "media_stream": "Live video of the game",
}

_cache: dict[str, Any] = {"at": 0.0, "key": "", "screens": []}
_sweep_cache: dict[str, Any] = {"at": 0.0, "key": "", "devices": []}


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


def _uuid_key(value: str) -> str:
    """Cast ids appear dashed (mDNS, eureka) and bare (HA identifiers)."""
    return re.sub(r"[-{}\s]", "", (value or "").lower())


def known_hosts_from_flow(flow: dict[str, Any]) -> list[str]:
    """The ``known_hosts`` an HA Cast options form is pre-filled with (pure)."""
    hosts: list[str] = []
    for field in flow.get("data_schema") or []:
        if field.get("name") != "known_hosts":
            continue
        for src in ((field.get("description") or {}).get("suggested_value"), field.get("default")):
            if isinstance(src, list):
                hosts.extend(str(h).strip() for h in src)
    out: list[str] = []
    for h in hosts:
        with contextlib.suppress(ValueError):
            if ipaddress.ip_address(h).version == 4 and h not in out:
                out.append(h)
    return out


async def ha_cast_known_hosts(ha_url: str, ha_token: str) -> list[str]:
    """Addresses HA's Cast integration was told about (devices on other subnets).

    HA only shows them in the integration's options form, so the form is
    opened, read and cancelled; nothing is saved.
    """
    if not (ha_url and ha_token):
        return []
    import aiohttp

    hosts: list[str] = []
    headers = {"Authorization": f"Bearer {ha_token}"}
    base = f"{ha_url.rstrip('/')}/api/config/config_entries/options/flow"
    timeout = aiohttp.ClientTimeout(total=8)
    for entry in await ha_client.get_config_entries(ha_url, ha_token, "cast"):
        entry_id = entry.get("entry_id")
        if not entry_id:
            continue
        flow_id = ""
        try:
            async with aiohttp.ClientSession(timeout=timeout) as client:
                async with client.post(base, headers=headers, json={"handler": entry_id}) as resp:
                    if resp.status >= 400:
                        continue
                    flow = await resp.json()
                flow_id = str(flow.get("flow_id") or "")
                hosts.extend(h for h in known_hosts_from_flow(flow) if h not in hosts)
                if flow_id:
                    async with client.delete(f"{base}/{flow_id}", headers=headers):
                        flow_id = ""
        except Exception as exc:
            log.debug("[tv_stage] reading HA cast known hosts failed: %s", exc)
    return hosts


def cast_from_eureka(ip: str, info: dict[str, Any]) -> dict[str, Any] | None:
    """A Cast device record from a device's own ``eureka_info`` answer (pure)."""
    dev = info.get("device_info") or {}
    uuid = str(info.get("ssdp_udn") or dev.get("ssdp_udn") or "")
    name = str(info.get("name") or "")
    if not (uuid or name):
        return None
    caps = dev.get("capabilities") or {}
    display = caps.get("display_supported")
    return {
        "uuid": uuid,
        "name": name,
        "host": ip,
        "port": 8009,
        "model": str(dev.get("model_name") or ""),
        "cast_type": "audio" if display is False else "cast",
        "manufacturer": str(dev.get("manufacturer") or ""),
        "found_by": "unicast",
    }


async def _port_open(ip: str, port: int = 8009) -> bool:
    try:
        _, writer = await asyncio.wait_for(asyncio.open_connection(ip, port), CAST_PORT_PROBE_S)
    except Exception:
        return False
    writer.close()
    return True


async def _cast_info(ip: str) -> dict[str, Any] | None:
    """Ask a Cast device who it is (https on newer firmware, http on older)."""
    import aiohttp

    timeout = aiohttp.ClientTimeout(total=CAST_INFO_TIMEOUT_S)
    q = "params=name,device_info,ssdp_udn"
    for url in (f"https://{ip}:8443/setup/eureka_info?{q}", f"http://{ip}:8008/setup/eureka_info?{q}"):
        with contextlib.suppress(Exception):
            async with aiohttp.ClientSession(timeout=timeout) as client, client.get(url, ssl=False) as resp:
                if resp.status == 200:
                    found = cast_from_eureka(ip, await resp.json(content_type=None))
                    if found:
                        return found
    return None


def _local_networks() -> set[ipaddress.IPv4Network]:
    nets: set[ipaddress.IPv4Network] = set()
    with contextlib.suppress(Exception):
        ip = lan_host_for()
        nets.add(ipaddress.ip_network(f"{ip}/24", strict=False))
    return nets


def neighbour_networks(nets: set[ipaddress.IPv4Network], span: int = 1) -> set[ipaddress.IPv4Network]:
    """The private /24s beside each of ``nets`` (pure). 192.168.2.0/24 -> .1 and .3."""
    out: set[ipaddress.IPv4Network] = set()
    for net in nets:
        a, b, c, _ = net.network_address.packed
        for d in range(-span, span + 1):
            if d and 0 <= c + d <= 255:
                cand = ipaddress.ip_network(f"{a}.{b}.{c + d}.0/24")
                if cand.is_private:
                    out.add(cand)
    return out - nets


ROUTER_STATUS_KEY = "network:status"


def router_view(status: dict[str, Any]) -> tuple[set[ipaddress.IPv4Network], list[str]]:
    """LAN networks and device addresses from the router monitor's status (pure)."""
    nets: set[ipaddress.IPv4Network] = set()
    for n in status.get("networks") or []:
        with contextlib.suppress(Exception):
            net = ipaddress.ip_interface(str(n.get("cidr") or "")).network
            if net.version == 4 and net.is_private and net.prefixlen >= 16:
                nets.add(net)
    ips: list[str] = []
    for dev in (status.get("devices") or {}).values():
        ip = str((dev or {}).get("ip") or "")
        with contextlib.suppress(ValueError):
            if ipaddress.ip_address(ip).version == 4 and ip not in ips:
                ips.append(ip)
    return nets, ips


async def _router_status() -> dict[str, Any]:
    """The router monitor's latest snapshot (control plane writes it to Redis)."""
    with contextlib.suppress(Exception):
        import json

        import redis.asyncio as aioredis

        from services.config import REDIS_URL

        r = aioredis.from_url(REDIS_URL, decode_responses=True)
        try:
            raw = await asyncio.wait_for(r.get(ROUTER_STATUS_KEY), 3)
        finally:
            await r.aclose()
        data = json.loads(raw or "{}")
        if data.get("configured") and data.get("connected", True):
            return data
    return {}


_LEARNED_FILE = "cast_hosts.json"


def _learned_hosts() -> list[str]:
    with contextlib.suppress(Exception):
        import json

        data = json.loads((TOKEN_DIR / _LEARNED_FILE).read_text())
        return [h for h in data if isinstance(h, str)]
    return []


def _remember_hosts(hosts: list[str]) -> None:
    with contextlib.suppress(Exception):
        import json

        TOKEN_DIR.mkdir(parents=True, exist_ok=True)
        (TOKEN_DIR / _LEARNED_FILE).write_text(json.dumps(sorted(set(hosts))))


async def discover_remote_cast_devices(ha_url: str, ha_token: str, *, force: bool = False) -> list[dict[str, Any]]:
    """Cast devices on subnets mDNS cannot reach (our own discovery).

    Sweeps the Cast port on the /24s beside this host's, on /24s where a Cast
    device was found before, and on those of Home Assistant's Cast "known
    hosts" when HA is configured. Works without HA; HA only adds hints.
    """
    key = f"{ha_url}|{bool(ha_token)}"
    now = time.time()
    if not force and _sweep_cache["key"] == key and now - float(_sweep_cache["at"]) < CAST_SWEEP_TTL_S:
        return list(_sweep_cache["devices"])
    hints: list[str] = []
    with contextlib.suppress(Exception):
        hints = await asyncio.wait_for(ha_cast_known_hosts(ha_url, ha_token), 10)
    local = _local_networks()
    router_nets, router_ips = router_view(await _router_status())
    hosts = list(dict.fromkeys(hints + _learned_hosts()))
    if router_ips:
        # The router knows every device: ask those, no sweep needed.
        hosts = list(dict.fromkeys(hosts + [ip for ip in router_ips
                                            if not any(ipaddress.ip_address(ip) in n for n in local)]))
        nets: set[ipaddress.IPv4Network] = set()
    else:
        nets = (router_nets | {ipaddress.ip_network(f"{h}/24", strict=False) for h in hosts}
                | neighbour_networks(local, CAST_NEIGHBOUR_NETS)) - local
    candidates: list[str] = []
    sem0 = asyncio.Semaphore(128)

    async def known(ip: str) -> str | None:
        async with sem0:
            return ip if await _port_open(ip) else None

    candidates += [ip for ip in await asyncio.gather(*(known(h) for h in hosts)) if ip]
    sem = asyncio.Semaphore(128)

    async def probe(ip: str) -> str | None:
        async with sem:
            return ip if await _port_open(ip) else None

    for net in nets:
        if net.num_addresses > 1024:
            continue
        ips = [str(a) for a in net.hosts() if str(a) not in hosts]
        candidates += [ip for ip in await asyncio.gather(*(probe(i) for i in ips)) if ip]
    infos = await asyncio.gather(*(_cast_info(ip) for ip in candidates))
    devices = [d for d in infos if d]
    if devices:
        _remember_hosts(_learned_hosts() + [d["host"] for d in devices])
    log.info("[tv_stage] cast beyond mDNS: %d hint(s), %d subnet(s) swept, %d device(s)",
             len(hosts), len(nets), len(devices))
    _sweep_cache.update({"at": now, "key": key, "devices": devices})
    return devices


def merge_cast_devices(*groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One record per Cast device; the first group to report it wins (pure)."""
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for group in groups:
        for d in group:
            k = _uuid_key(d.get("uuid") or "") or f"host:{d.get('host')}"
            if k in seen:
                continue
            seen.add(k)
            out.append(d)
    return out


async def ha_cast_uuids(ha_url: str, ha_token: str) -> dict[str, str]:
    """``{cast entity_id: Cast uuid}`` from HA's device registry."""
    if not (ha_url and ha_token):
        return {}
    tpl = ("{% for e in integration_entities('cast') %}{% for i in device_attr(device_id(e), 'identifiers') or [] %}"
           "{% if i[0] == 'cast' %}{{ e }}|{{ i[1] }};{% endif %}{% endfor %}{% endfor %}")
    out: dict[str, str] = {}
    with contextlib.suppress(Exception):
        text = await ha_client._render_template(ha_url, ha_token, tpl) or ""
        for part in text.split(";"):
            eid, _, uid = part.strip().partition("|")
            if eid and uid:
                out[eid] = _uuid_key(uid)
    return out


#: HA integration -> the platform name tv_stage uses. The integration that owns an
#: entity is authoritative: a name can say "webOS" on a Music Assistant AirPlay
#: player, which cannot open a browser (it took a cast that never showed).
INTEGRATION_PLATFORMS = {
    "webostv": "webos",
    "samsungtv": "samsung",
    "roku": "roku",
    "androidtv_remote": "android_tv",
    "androidtv": "android_tv",
    "cast": "cast",
    "music_assistant": "music_assistant",
    "apple_tv": "apple_tv",
    "dlna_dmr": "dlna",
}


async def ha_entity_platforms(ha_url: str, ha_token: str) -> dict[str, str]:
    """``{media_player entity: platform}`` from the HA integration that owns each one."""
    if not (ha_url and ha_token):
        return {}
    parts = "".join(
        f"{{% for e in integration_entities('{integ}') %}}{{% if e.startswith('media_player.') %}}"
        f"{{{{ e }}}}|{integ};{{% endif %}}{{% endfor %}}"
        for integ in INTEGRATION_PLATFORMS
    )
    out: dict[str, str] = {}
    with contextlib.suppress(Exception):
        text = await ha_client._render_template(ha_url, ha_token, parts) or ""
        for part in text.split(";"):
            eid, _, integ = part.strip().partition("|")
            if eid and integ in INTEGRATION_PLATFORMS:
                out.setdefault(eid, INTEGRATION_PLATFORMS[integ])
    return out


#: Platforms reached through the TV's own browser, which a Music Assistant player cannot open.
BROWSER_PLATFORMS = {"webos", "samsung", "android_tv", "google_tv"}


def _owned_platform(guessed: str, owner: str | None) -> str:
    """The platform to use, given the name-based guess and the integration that owns the entity.

    A TV integration is authoritative. A Music Assistant player cannot open a TV
    browser, so a browser guess for one is wrong; any other guess (a Roku, whose
    stream goes to the Roku itself) stands.
    """
    if owner and owner != "music_assistant":
        return owner
    if owner == "music_assistant" and guessed in BROWSER_PLATFORMS:
        return "music_assistant"
    return guessed


def _methods_for(platform: str, *, has_cast: bool, companions: dict[str, Any], entity_id: str,
                 os_version: str = "") -> list[str]:
    methods: list[str] = []
    if has_cast:
        methods.append("cast_web")
    if platform == "webos" and entity_id:
        # Straight to the TV first: Home Assistant's link to an LG goes stale when
        # the TV changes address (it kept one from three days and a subnet ago).
        methods += ["webos_direct", "webos_browser"]
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
                  ha_states: list[dict[str, Any]] | None = None,
                  cast_uuids: dict[str, str] | None = None,
                  platforms: dict[str, str] | None = None) -> list[dict[str, Any]]:
    """Merge HA TVs and mDNS Cast devices into one list of physical screens.

    Pure (no I/O) so the merge rules are testable:
    - an Android/Google TV's ``cast_sibling`` is its built-in Cast receiver,
      the same screen; any other TV's Cast sibling is a plugged-in Chromecast,
      listed on its own *and* used to reach that TV;
    - a discovered Cast device supplies a screen's Cast address, matched by
      Cast id (``cast_uuids``: HA entity -> uuid) first, then by name;
    - a Cast device that is audio-only or a speaker group is not a screen;
    - a Cast device HA does not know about is still a screen;
    - an entity's platform is the integration that owns it (``platforms``),
      not what its name suggests.
    """
    ha_devices = [{**d, "platform": _owned_platform(str(d.get("platform") or ""),
                                                    (platforms or {}).get(str(d.get("entity_id") or "")))}
                  for d in ha_devices]
    names_by_entity = {
        str(s.get("entity_id") or ""): str((s.get("attributes") or {}).get("friendly_name") or "")
        for s in ha_states or []
    }
    cast_uuids = cast_uuids or {}
    cast_by_uuid = {_uuid_key(cd.get("uuid") or ""): cd for cd in cast_devices if cd.get("uuid")}
    entity_by_uuid = {u: e for e, u in cast_uuids.items() if u}
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
    # A TV with Cast built in shows up in HA twice (its own integration and
    # Cast); that is one screen. A Chromecast plugged into a TV is a device of
    # its own and stays listed.
    for d in tvs:
        sib = (d.get("companions") or {}).get("cast_sibling")
        if sib and sib != d.get("entity_id") and d.get("platform") in BUILT_IN_CAST_PLATFORMS:
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
        # A TV's Music Assistant player is usually named for the Chromecast
        # that drives it ("Master Bedroom TV" for the Samsung's dongle).
        if companions.get("ma_sibling") and companions["ma_sibling"] != eid:
            candidate_names.append(names_by_entity.get(companions["ma_sibling"], ""))
        cast_dev = cast_by_uuid.get(cast_uuids.get(cast_entity, "")) if cast_entity else None
        for cand in candidate_names if cast_dev is None else []:
            for key in _name_keys(cand):
                if key in cast_by_key:
                    cast_dev = cast_by_key[key]
                    break
            if cast_dev:
                break
        if cast_dev and cast_dev.get("uuid"):
            used_cast.add(_uuid_key(cast_dev["uuid"]))
            cast_entity = cast_entity or entity_by_uuid.get(_uuid_key(cast_dev["uuid"]), "")
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
                    "cast_uuid": (cast_uuids.get(cast_entity, "") if cast_entity else "")
                    or _uuid_key((cast_dev or {}).get("uuid") or ""),
                    "cast": cast_dev,
                    "companions": companions,
                    "methods": _methods_for(platform, has_cast=has_cast, companions=companions, entity_id=eid,
                                            os_version=str(d.get("os_version") or "")),
                    "source": "home_assistant" + (("+network" if cast_dev.get("found_by") == "unicast" else "+mdns")
                                                  if cast_dev else ""),
                }
            )
        )

    # Every Cast device HA knows that discovery found with a screen is a
    # screen of its own (a Chromecast plugged into a TV, a Google TV), even
    # when HA's classifier did not list it.
    listed = {sc["entity_id"] for sc in screens}
    for ent, uid in cast_uuids.items():
        cd = cast_by_uuid.get(uid)
        if ent in listed or ent in folded or not cd or (cd.get("cast_type") or "cast") != "cast":
            continue
        used_cast.add(uid)
        screens.append(
            _screen_summary(
                {
                    "id": f"ha:{ent}",
                    "name": names_by_entity.get(ent) or cd.get("name") or ent,
                    "platform": "cast",
                    "entity_id": ent,
                    "state": "on",
                    "cast_entity": ent,
                    "cast_uuid": uid,
                    "cast": cd,
                    "companions": {},
                    "methods": ["cast_web"],
                    "source": "home_assistant+" + ("network" if cd.get("found_by") == "unicast" else "mdns"),
                }
            )
        )

    seen_uuid: set[str] = set()
    for cd in cast_devices:
        uid = _uuid_key(cd.get("uuid") or "")
        if (cd.get("cast_type") or "cast") != "cast" or not uid or uid in used_cast or uid in seen_uuid:
            continue
        seen_uuid.add(uid)
        screens.append(
            _screen_summary(
                {
                    "id": f"cast:{cd.get('uuid')}",
                    "name": cd.get("name") or "Cast device",
                    "platform": "cast",
                    "entity_id": "",
                    "state": "on",
                    "cast_entity": "",
                    "cast_uuid": uid,
                    "cast": cd,
                    "companions": {},
                    "methods": ["cast_web"],
                    "source": "mdns" if cd.get("found_by") != "unicast" else "network",
                }
            )
        )

    # A Music Assistant wrapper around a Cast device is not another screen
    # when the device (or the TV it drives) is already listed. Of several
    # wrappers around one device with nothing else, keep one.
    def cast_key(sc: dict[str, Any]) -> str:
        return sc.get("cast_uuid") or _uuid_key((sc.get("cast") or {}).get("uuid") or "") or sc.get("cast_entity") or ""

    real_cast = {cast_key(sc) for sc in screens if cast_key(sc) and sc["platform"] != "music_assistant"}
    unique: list[dict[str, Any]] = []
    wrapped: set[str] = set()
    for sc in screens:
        key = cast_key(sc)
        if key and sc["platform"] == "music_assistant":
            if key in real_cast or key in wrapped:
                continue
            wrapped.add(key)
        unique.append(sc)
    screens = [sc for sc in unique if sc["methods"]]

    order = {"web": 0, "video": 1, "none": 2}
    screens.sort(key=lambda s: (order.get(s["display"], 3), s["state"] in ("off", "unavailable"), s["name"].lower()))
    return screens


async def _safe_remote_casts(ha_url: str, ha_token: str, *, force: bool = False) -> list[dict[str, Any]]:
    try:
        return await asyncio.wait_for(discover_remote_cast_devices(ha_url, ha_token, force=force), 20)
    except Exception as exc:
        log.debug("[tv_stage] cross-subnet cast discovery failed: %s", exc)
        return []


async def list_screens(ha_url: str, ha_token: str, *, refresh: bool = False) -> list[dict[str, Any]]:
    """Every screen in the house that can show the stage, best first."""
    key = f"{ha_url}|{bool(ha_token)}"
    now = time.time()
    if not refresh and _cache["key"] == key and now - float(_cache["at"]) < SCREEN_CACHE_TTL_S:
        return list(_cache["screens"])

    states: list[dict[str, Any]] = []
    ha_devices: list[dict[str, Any]] = []
    cast_task = asyncio.create_task(discover_cast_devices())
    remote_task = asyncio.create_task(_safe_remote_casts(ha_url, ha_token))
    uuids: dict[str, str] = {}
    platforms: dict[str, str] = {}
    if ha_url and ha_token:
        with contextlib.suppress(Exception):
            states = await ha_client.get_states(ha_url, ha_token) or []
        with contextlib.suppress(Exception):
            ha_devices = await classify_communication_devices(ha_url, ha_token, all_states=states)
        uuids = await ha_cast_uuids(ha_url, ha_token)
        platforms = await ha_entity_platforms(ha_url, ha_token)
    cast_devices = merge_cast_devices(await cast_task, await remote_task)
    screens = build_screens(ha_devices, cast_devices, states, uuids, platforms)
    await _webos_live_state(screens)
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
    want = screen.get("cast_uuid") or ""

    def match(devices: list[dict[str, Any]]) -> dict[str, Any] | None:
        for cd in devices:
            if want and _uuid_key(cd.get("uuid") or "") == want:
                return cd
        for cd in devices:
            if (cd.get("cast_type") or "cast") == "cast" and _name_keys(cd.get("name") or "") & keys:
                return cd
        return None

    found = match(await _safe_remote_casts(ha_url, ha_token, force=True))
    deadline = time.time() + CAST_WAKE_WAIT_S
    while found is None and time.time() < deadline:
        found = match(await discover_cast_devices(3.0))
        if found is None:
            await asyncio.sleep(1.0)
    return found


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


#: How long a woken LG gets to reconnect to Home Assistant.
WEBOS_WAKE_WAIT_S = 15


async def show_via_webos(screen: dict[str, Any], url: str, ha_url: str, ha_token: str) -> dict[str, Any]:
    eid = screen["entity_id"]
    # An LG that is off shows as "off" or "unavailable" in HA, and HA accepts a
    # command to it without error while doing nothing, so wake it and wait for
    # it to come back before saying the browser opened.
    st = await ha_client.get_state(ha_url, ha_token, eid) or {}
    if st.get("state") in ("off", "standby", "unavailable", "unknown", None):
        with contextlib.suppress(Exception):
            await ha_client.call_service(ha_url, ha_token, "media_player", "turn_on", eid)
        for _ in range(WEBOS_WAKE_WAIT_S):
            await asyncio.sleep(1)
            st = await ha_client.get_state(ha_url, ha_token, eid) or {}
            if st.get("state") not in ("off", "standby", "unavailable", "unknown", None):
                break
        else:
            return {"ok": False, "detail": f"{screen.get('name') or 'The LG TV'} is not connected to Home Assistant "
                                           "(it is off or asleep). Turn it on with its remote, or turn on "
                                           "\"Turn on via Wi-Fi\" in the TV's settings so it can be woken, then try again."}
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


# --------------------------------------------------------------------------- LG webOS, directly

WEBOS_PORTS = (3001, 3000)
#: How long a first pairing waits for someone to press "Allow" on the TV.
WEBOS_PAIR_WAIT_S = 40
_WEBOS_KEYS = "webos_keys.json"


def _webos_store() -> dict[str, Any]:
    with contextlib.suppress(Exception):
        import json

        return json.loads((TOKEN_DIR / _WEBOS_KEYS).read_text())
    return {}


def _webos_remember(entity_id: str, **fields: Any) -> None:
    import json

    data = _webos_store()
    data[entity_id] = {**(data.get(entity_id) or {}), **{k: v for k, v in fields.items() if v}}
    TOKEN_DIR.mkdir(parents=True, exist_ok=True)
    tmp = TOKEN_DIR / (_WEBOS_KEYS + ".tmp")
    tmp.write_text(json.dumps(data))
    tmp.replace(TOKEN_DIR / _WEBOS_KEYS)


def webos_host_candidates(screen: dict[str, Any], router_devices: Any, remembered: str = "",
                          ha_ip: str = "") -> list[str]:
    """Where the LG might be, best first: the router's device of the same name (it
    follows the TV's own network name, so it survives a new address), the address
    that worked last time, then whatever Home Assistant believes."""
    items = router_devices.values() if isinstance(router_devices, dict) else (router_devices or [])
    want = _name_keys(str(screen.get("name") or ""))
    named = [str(d.get("ip")) for d in items
             if isinstance(d, dict) and d.get("ip") and _name_keys(str(d.get("name") or "")) & want]
    out: list[str] = []
    for ip in [*named, remembered, ha_ip]:
        if ip and ip not in out:
            out.append(ip)
    return out


async def _webos_find_host(screen: dict[str, Any], ha_url: str, ha_token: str) -> str:
    eid = screen.get("entity_id") or ""
    status = await _router_status()
    remembered = str((_webos_store().get(eid) or {}).get("host") or "")
    ha_ip = await _entity_ip(ha_url, ha_token, eid, None) if eid else ""
    for ip in webos_host_candidates(screen, status.get("devices") or {}, remembered, ha_ip):
        for port in WEBOS_PORTS:
            if await _port_open(ip, port):
                return ip
    return ""


async def _webos_live_state(screens: list[dict[str, Any]]) -> None:
    """An LG we talk to directly is "on" when it answers, whatever Home Assistant's stale link says."""
    store = _webos_store()
    for sc in screens:
        if sc.get("platform") != "webos" or sc.get("state") not in ("unavailable", "unknown", "off"):
            continue
        host = str((store.get(sc.get("entity_id") or "") or {}).get("host") or "")
        if host and await _port_open(host, WEBOS_PORTS[0]):
            sc["state"] = "on"


async def show_via_webos_direct(screen: dict[str, Any], url: str, ha_url: str, ha_token: str) -> dict[str, Any]:
    """Open the LG's browser by talking to the TV itself (webOS's own API).

    The first time, the TV asks to allow "Jarvis"; the key it grants is kept, so
    later casts need nothing from anyone.
    """
    from aiowebostv import WebOsClient  # pyright: ignore[reportMissingImports]

    eid = screen.get("entity_id") or ""
    host = await _webos_find_host(screen, ha_url, ha_token)
    if not host:
        return {"ok": False, "detail": f"{screen.get('name') or 'The LG TV'} did not answer on the network. Is it on?"}
    key = str((_webos_store().get(eid) or {}).get("client_key") or "") or None
    client = WebOsClient(host, client_key=key, connect_timeout=5)
    try:
        try:
            await asyncio.wait_for(client.connect(), WEBOS_PAIR_WAIT_S if key is None else 15)
        except Exception as exc:
            if key is None:
                return {"ok": False, "detail": f"Press \"Allow\" on {screen.get('name') or 'the TV'} when it asks "
                                               f"about Jarvis, then cast again ({exc.__class__.__name__})."}
            raise
        _webos_remember(eid, host=host, client_key=client.client_key)
        # "open" takes the page; "launch" with a target opened the browser on its
        # home page instead (seen on a 2020 LG), so it is only the fallback.
        try:
            await client.request("system.launcher/open", {"target": url})
        except Exception:
            await client.request("system.launcher/launch", {"id": WEBOS_BROWSER_APP, "contentTarget": url})
        verified = False
        for _ in range(8):
            await asyncio.sleep(1)
            with contextlib.suppress(Exception):
                app = await client.get_current_app()
                if "browser" in str(app or "").lower():
                    verified = True
                    break
        return {"ok": True, "verified": verified,
                "detail": "LG browser opened" + ("" if verified else " (the TV did not confirm the browser)")}
    finally:
        with contextlib.suppress(Exception):
            await client.disconnect()


async def _webos_close_direct(screen: dict[str, Any], ha_url: str, ha_token: str) -> bool:
    from aiowebostv import WebOsClient  # pyright: ignore[reportMissingImports]

    eid = screen.get("entity_id") or ""
    stored = _webos_store().get(eid) or {}
    if not stored.get("client_key"):
        return False
    host = await _webos_find_host(screen, ha_url, ha_token)
    if not host:
        return False
    client = WebOsClient(host, client_key=stored["client_key"], connect_timeout=5)
    try:
        await asyncio.wait_for(client.connect(), 10)
        await client.request("system.launcher/close", {"id": WEBOS_BROWSER_APP})
        return True
    except Exception:
        return False
    finally:
        with contextlib.suppress(Exception):
            await client.disconnect()


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
    "webos_direct": show_via_webos_direct,
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
    if eid and screen["platform"] == "webos" and await _webos_close_direct(screen, ha_url, ha_token):
        actions.append("webos_close_browser")
    elif eid and screen["platform"] == "webos":
        res = await ha_client.call_service(ha_url, ha_token, "webostv", "command", eid,
                                           {"command": "system.launcher/close", "payload": {"id": WEBOS_BROWSER_APP}})
        if res.get("ok"):
            actions.append("webos_close_browser")
    if eid and (screen["platform"] == "roku" or "media_stream" in screen["methods"]):
        res = await ha_client.call_service(ha_url, ha_token, "media_player", "media_stop", eid)
        if res.get("ok"):
            actions.append("media_stop")
    return {"success": True, "screen": public_screen(screen), "actions": actions, "streams_stopped": stopped_streams}
