"""Pieces the media drivers share: HA feature bits, HTTP, UPnP SOAP, Wake-on-LAN.

Kept apart from any one driver so a TV integration that needs UPnP volume
(Samsung) and the DLNA renderer driver speak the same, tested SOAP code, and
so tests can swap the one HTTP seam (:class:`Http`) for a fake.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import socket
from collections.abc import Awaitable, Callable, Iterable
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urljoin
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape

import aiohttp

from services.homehub.core import DeviceUnavailable, Driver, EntityEntry, HubError

log = logging.getLogger("homehub.media")


class F:
    """HA ``MediaPlayerEntityFeature`` bit values (Jarvis reads ``supported_features``)."""

    PAUSE = 1
    SEEK = 2
    VOLUME_SET = 4
    VOLUME_MUTE = 8
    PREVIOUS_TRACK = 16
    NEXT_TRACK = 32
    TURN_ON = 128
    TURN_OFF = 256
    PLAY_MEDIA = 512
    VOLUME_STEP = 1024
    SELECT_SOURCE = 2048
    STOP = 4096
    CLEAR_PLAYLIST = 8192
    PLAY = 16384
    SHUFFLE_SET = 32768
    SELECT_SOUND_MODE = 65536
    BROWSE_MEDIA = 131072
    REPEAT_SET = 262144
    GROUPING = 524288
    MEDIA_ANNOUNCE = 1048576
    MEDIA_ENQUEUE = 2097152
    SEARCH_MEDIA = 4194304


#: HA ``RemoteEntityFeature.ACTIVITY``.
REMOTE_ACTIVITY = 4

#: HA media_content_type -> the MIME type a renderer needs, when the caller gave a generic type.
GENERIC_MIME = {
    "music": "audio/mpeg", "audio": "audio/mpeg", "podcast": "audio/mpeg", "track": "audio/mpeg",
    "video": "video/mp4", "movie": "video/mp4", "tvshow": "video/mp4", "episode": "video/mp4",
    "image": "image/jpeg",
}
_EXT_MIME = {
    "mp3": "audio/mpeg", "aac": "audio/aac", "m4a": "audio/mp4", "flac": "audio/flac", "wav": "audio/wav",
    "ogg": "audio/ogg", "opus": "audio/ogg", "mp4": "video/mp4", "m4v": "video/mp4", "mkv": "video/x-matroska",
    "webm": "video/webm", "mov": "video/quicktime", "m3u8": "application/x-mpegURL", "ts": "video/mp2t",
    "jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "gif": "image/gif",
}


def mime_for(content_type: str | None, url: str = "") -> str:
    """A concrete MIME type for a URL: an explicit one wins, then the extension, then the HA kind."""
    ct = (content_type or "").strip().lower()
    if "/" in ct:
        return ct
    m = re.search(r"\.([a-z0-9]{2,5})(?:$|[?#])", url.lower())
    if m and m.group(1) in _EXT_MIME:
        return _EXT_MIME[m.group(1)]
    return GENERIC_MIME.get(ct, "video/mp4" if ct in ("url", "") else "audio/mpeg")


def clean(attrs: dict[str, Any]) -> dict[str, Any]:
    """HA leaves an attribute out rather than publishing None for it."""
    return {k: v for k, v in attrs.items() if v is not None}


def as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


def utc_iso(ts: float | None = None) -> str:
    return (datetime.fromtimestamp(ts, UTC) if ts else datetime.now(UTC)).isoformat()


def clamp01(value: Any) -> float:
    return max(0.0, min(1.0, float(value)))


# -- HTTP ---------------------------------------------------------------------


class Http:
    """The one HTTP seam: drivers call :meth:`request`, tests replace the object.

    Any transport failure becomes :class:`DeviceUnavailable`, so a driver can
    treat "the TV is off" and "the TV refused" differently without catching
    aiohttp's whole exception tree.
    """

    def __init__(self, timeout: float = 8.0) -> None:
        self.timeout = timeout
        self._session: aiohttp.ClientSession | None = None

    async def request(self, method: str, url: str, *, data: Any = None, headers: dict[str, str] | None = None,
                      params: dict[str, str] | None = None, timeout: float | None = None) -> tuple[int, str]:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        try:
            async with self._session.request(
                method, url, data=data, headers=headers, params=params,
                timeout=aiohttp.ClientTimeout(total=timeout or self.timeout),
            ) as resp:
                return resp.status, await resp.text(errors="replace")
        except (aiohttp.ClientError, TimeoutError, OSError) as exc:
            raise DeviceUnavailable(f"{method} {url}: {type(exc).__name__}: {exc}") from exc

    async def close(self) -> None:
        if self._session is not None:
            with contextlib.suppress(Exception):
                await self._session.close()
            self._session = None


# -- UPnP SOAP ----------------------------------------------------------------

_DEVICE_NS = "urn:schemas-upnp-org:device-1-0"
_SOAP_ENV = ('<?xml version="1.0" encoding="utf-8"?>'
             '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
             's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/"><s:Body>'
             '<u:{action} xmlns:u="{stype}">{args}</u:{action}></s:Body></s:Envelope>')


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


class UpnpDevice:
    """A UPnP device reached by its description URL, spoken to with raw SOAP.

    Only the few calls the hub needs (AVTransport and RenderingControl); the
    description is fetched once and refetched after a failure, because a TV
    that rebooted may have moved its control URLs.
    """

    def __init__(self, http: Http, location: str) -> None:
        self.http = http
        self.location = location
        self.services: dict[str, tuple[str, str]] = {}  # kind -> (serviceType, absolute controlURL)
        self.friendly_name = ""
        self.udn = ""

    async def load(self) -> None:
        status, text = await self.http.request("GET", self.location, timeout=5)
        if status >= 400:
            raise DeviceUnavailable(f"{self.location} answered HTTP {status}")
        try:
            root = ET.fromstring(text)
        except ET.ParseError as exc:
            raise DeviceUnavailable(f"{self.location} sent an unreadable description") from exc
        base = root.findtext(f"{{{_DEVICE_NS}}}URLBase") or self.location
        self.friendly_name = root.findtext(f".//{{{_DEVICE_NS}}}friendlyName") or ""
        self.udn = root.findtext(f".//{{{_DEVICE_NS}}}UDN") or ""
        services: dict[str, tuple[str, str]] = {}
        for svc in root.iter(f"{{{_DEVICE_NS}}}service"):
            stype = (svc.findtext(f"{{{_DEVICE_NS}}}serviceType") or "").strip()
            ctrl = (svc.findtext(f"{{{_DEVICE_NS}}}controlURL") or "").strip()
            parts = stype.split(":")
            if len(parts) >= 4 and ctrl:
                services.setdefault(parts[3], (stype, urljoin(base, ctrl)))
        self.services = services

    def has(self, kind: str) -> bool:
        return kind in self.services

    async def call(self, kind: str, action: str, **args: Any) -> dict[str, str]:
        if not self.services:
            await self.load()
        if kind not in self.services:
            raise HubError(f"{self.friendly_name or self.location} has no {kind} service")
        stype, url = self.services[kind]
        body = _SOAP_ENV.format(
            action=action, stype=stype,
            args="".join(f"<{k}>{escape(str(v))}</{k}>" for k, v in args.items()),
        )
        headers = {"Content-Type": 'text/xml; charset="utf-8"', "SOAPAction": f'"{stype}#{action}"'}
        try:
            status, text = await self.http.request("POST", url, data=body.encode(), headers=headers, timeout=8)
        except DeviceUnavailable:
            self.services = {}  # refetch the description next time
            raise
        return self._parse(action, status, text)

    @staticmethod
    def _parse(action: str, status: int, text: str) -> dict[str, str]:
        try:
            root = ET.fromstring(text) if text.strip() else None
        except ET.ParseError:
            root = None
        if root is not None:
            for el in root.iter():
                if _local(el.tag) == "UPnPError":
                    code = next((c.text for c in el if _local(c.tag) == "errorCode"), "")
                    desc = next((c.text for c in el if _local(c.tag) == "errorDescription"), "")
                    raise HubError(f"UPnP {action} failed: {code} {desc}".strip())
                if _local(el.tag) == f"{action}Response":
                    return {_local(c.tag): (c.text or "") for c in el}
        if status >= 400:
            raise HubError(f"UPnP {action} failed with HTTP {status}")
        return {}

    # RenderingControl, used by both the DLNA and Samsung drivers.
    async def get_volume(self) -> float | None:
        out = await self.call("RenderingControl", "GetVolume", InstanceID=0, Channel="Master")
        try:
            return clamp01(int(out.get("CurrentVolume", "")) / 100)
        except ValueError:
            return None

    async def set_volume(self, level: float) -> None:
        await self.call("RenderingControl", "SetVolume", InstanceID=0, Channel="Master",
                        DesiredVolume=round(clamp01(level) * 100))

    async def get_mute(self) -> bool | None:
        out = await self.call("RenderingControl", "GetMute", InstanceID=0, Channel="Master")
        val = out.get("CurrentMute", "")
        return None if val == "" else val.strip().lower() in ("1", "true", "yes")

    async def set_mute(self, muted: bool) -> None:
        await self.call("RenderingControl", "SetMute", InstanceID=0, Channel="Master",
                        DesiredMute=1 if muted else 0)

    async def set_uri_and_play(self, url: str, mime: str, title: str = "") -> None:
        await self.call("AVTransport", "SetAVTransportURI", InstanceID=0, CurrentURI=url,
                        CurrentURIMetaData=didl(url, mime, title))
        await self.call("AVTransport", "Play", InstanceID=0, Speed="1")


def didl(url: str, mime: str, title: str = "") -> str:
    """DIDL-Lite metadata for one item. Samsung renderers refuse a URI that comes without it."""
    kind = mime.split("/", 1)[0]
    upnp_class = {"audio": "object.item.audioItem.musicTrack", "image": "object.item.imageItem.photo"}.get(
        kind, "object.item.videoItem")
    return (
        '<DIDL-Lite xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:upnp="urn:schemas-upnp-org:metadata-1-0/upnp/">'
        f'<item id="0" parentID="-1" restricted="1"><dc:title>{escape(title or "Jarvis")}</dc:title>'
        f'<upnp:class>{upnp_class}</upnp:class>'
        f'<res protocolInfo="http-get:*:{escape(mime)}:*">{escape(url)}</res></item></DIDL-Lite>'
    )


def hms_to_seconds(value: str | None) -> float | None:
    """'0:03:25.120' -> 205.12; None for the 'NOT_IMPLEMENTED' renderers send."""
    if not value or ":" not in value:
        return None
    try:
        parts = [float(p) for p in value.split(":")]
    except ValueError:
        return None
    total = 0.0
    for p in parts:
        total = total * 60 + p
    return total


def seconds_to_hms(seconds: float) -> str:
    s = max(0, int(seconds))
    return f"{s // 3600}:{s // 60 % 60:02d}:{s % 60:02d}"


# -- Wake-on-LAN and MAC lookup -------------------------------------------------

_MAC_RE = re.compile(r"^[0-9a-f]{2}([:-]?[0-9a-f]{2}){5}$", re.I)


def normalize_mac(mac: str | None) -> str | None:
    if not mac or not _MAC_RE.match(mac.strip()):
        return None
    hexes = re.sub(r"[^0-9a-f]", "", mac.lower())
    return ":".join(hexes[i:i + 2] for i in range(0, 12, 2))


def _send_magic(mac: str, broadcast: str) -> None:
    packet = b"\xff" * 6 + bytes.fromhex(mac.replace(":", "")) * 16
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        for port in (9, 7):
            s.sendto(packet, (broadcast, port))


async def wake_on_lan(macs: Iterable[str], broadcast: str = "255.255.255.255") -> int:
    """Send the magic packet to every MAC (a TV has wired, Wi-Fi and P2P ones). Returns how many went out."""
    sent = 0
    for mac in macs:
        norm = normalize_mac(mac)
        if norm is None:
            continue
        try:
            await asyncio.to_thread(_send_magic, norm, broadcast)
            sent += 1
        except OSError as exc:
            log.warning("Wake-on-LAN to %s failed: %s", norm, exc)
    return sent


def arp_mac(ip: str, path: str = "/proc/net/arp") -> str | None:
    """The MAC the host's ARP cache holds for ``ip`` (Linux, host network), or None."""
    try:
        with open(path, encoding="ascii", errors="ignore") as fh:
            lines = fh.read().splitlines()[1:]
    except OSError:
        return None
    for line in lines:
        cols = line.split()
        if len(cols) >= 4 and cols[0] == ip and cols[2] != "0x0":
            mac = normalize_mac(cols[3])
            if mac and mac != "00:00:00:00:00:00":
                return mac
    return None


def known_macs(driver: Driver, entity: EntityEntry | None = None, host: str | None = None) -> list[str]:
    """Every MAC the registry knows for this TV, best first.

    Options win (an operator fix), then the config entry, then the device
    registry's connections, then the host's ARP cache: the LG entry has no MAC
    anywhere in the export, but the cache learns it whenever the TV is on.
    """
    found: list[str] = []

    def add(mac: Any) -> None:
        norm = normalize_mac(str(mac)) if mac else None
        if norm and norm not in found:
            found.append(norm)

    add(driver.entry.options.get("mac"))
    add(driver.entry.data.get("mac"))
    devices = []
    if entity is not None and entity.device_id:
        devices.append(driver.hub.devices.get(entity.device_id))
    for dev in devices:
        for conn in (dev.connections if dev else []):
            if len(conn) == 2 and conn[0] == "mac":
                add(conn[1])
    if host:
        add(arp_mac(host))
    return found


# -- remote helpers -------------------------------------------------------------


async def send_command_list(commands: Any, data: dict[str, Any], send: Callable[[str], Awaitable[None]]) -> int:
    """HA ``remote.send_command``: each command, ``num_repeats`` times, ``delay_secs`` apart."""
    cmds = [str(c) for c in as_list(commands) if str(c).strip()]
    if not cmds:
        raise HubError("remote.send_command needs a command")
    repeats = max(1, int(data.get("num_repeats") or 1))
    delay = float(data.get("delay_secs") if data.get("delay_secs") is not None else 0.4)
    sent = 0
    for _ in range(repeats):
        for cmd in cmds:
            if sent and delay > 0:
                await asyncio.sleep(delay)
            await send(cmd)
            sent += 1
    return sent


def entities_of(driver: Driver, domain: str) -> list[EntityEntry]:
    return [e for e in driver.entities if e.domain == domain]
