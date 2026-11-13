"""Real-time camera video through go2rtc (the ``go2rtc`` integration).

Home Assistant gave the cameras real-time video through go2rtc; this does the
same. go2rtc (``alexxit/go2rtc``, its own container) turns a camera's RTSP or
RTMP stream into WebRTC, MSE (fragmented MP4 over a WebSocket) or a plain MP4
stream for a browser. The hub tells it which cameras exist and is the only
thing that talks to its API: browsers reach it through the gateway and the
hub (``live_video_api.py``), never directly. Only the WebRTC media itself
flows between the browser and go2rtc, on the WebRTC port.

Everything is an integration, read at runtime:

``entry.data``
    ``url`` - go2rtc's API (default ``http://127.0.0.1:1984``), plus
    ``username``/``password`` when its API has basic auth.

``entry.options``
    ``webrtc_host`` - the address browsers reach go2rtc's WebRTC port at
    (empty: go2rtc offers every address of its host). ``webrtc_port`` - the
    WebRTC port (default 8555). ``default_quality`` - ``sub`` or ``main``.
    ``cameras`` - per camera entity id: ``quality`` (``sub``/``main``),
    ``on_demand`` (default true: the camera streams only while someone
    watches), ``disabled`` (keep it off go2rtc), ``transcode`` (an MJPEG
    camera through go2rtc's ffmpeg, which costs CPU; off by default).

Streams come from whichever camera entities exist, through the camera
contract (``drivers/mjpeg.py``): a driver's ``async_camera_stream_sources``
(``{"main": url, "sub": url}``, Reolink) or its ``async_camera_stream_url``
(an RTSP/RTMP URL is used as is; an MJPEG URL only with ``transcode``).
They are registered with ``PATCH /api/streams``, which keeps them in
go2rtc's memory only (``PUT`` would also write the URL, password and all, to
its config file), and re-registered when go2rtc restarts.

On demand: go2rtc opens a camera's stream when the first viewer arrives and
closes it after the last one leaves, which is what lets a battery camera
sleep. ``on_demand: false`` asks go2rtc to keep the stream open (its
"preload"), and is refused for battery cameras.

Source URLs carry camera passwords. They are kept in memory, sent only to
go2rtc, never logged, never stored and never in an API answer; any text from
go2rtc is passed through :func:`redact` first.
"""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import ipaddress
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

import aiohttp
import yaml

from services.homehub.core import DeviceUnavailable, Driver, EntityEntry, Hub

log = logging.getLogger("homehub.go2rtc")

DOMAIN = "go2rtc"
DEFAULT_URL = "http://127.0.0.1:1984"
DEFAULT_WEBRTC_PORT = 8555
QUALITIES = ("sub", "main")
#: Browser playback modes go2rtc offers for a registered stream, best first.
MODES = ("webrtc", "mse", "mp4")
_API_TIMEOUT = aiohttp.ClientTimeout(total=10, connect=3)
#: go2rtc re-executes itself on ``/api/restart``; how long to wait for it to answer again.
RESTART_WAIT_S = 15.0
_STREAM_NAME = re.compile(r"^(camera\.[a-z0-9_]+):(sub|main)$")
_DIRECT_SCHEMES = ("rtsp", "rtsps", "rtmp", "rtmps")
_HTTP_SCHEMES = ("http", "https")


# -- secrets ----------------------------------------------------------------------

_USERINFO = re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://)[^/@\s\"']+@")
_SECRET_PARAMS = re.compile(r"(?i)\b(password|pass|pwd|token|user|username|key)=[^&\s\"']*")


def redact(text: Any, secrets: list[str] | tuple[str, ...] = ()) -> str:
    """``text`` with URLs' user info, credential query parameters and every known secret removed."""
    out = str(text or "")
    for secret in sorted({s for s in secrets if s and len(s) >= 3}, key=len, reverse=True):
        out = out.replace(secret, "***")
    out = _USERINFO.sub(r"\1***@", out)
    return _SECRET_PARAMS.sub(lambda m: f"{m.group(1)}=***", out)


def source_secrets(url: str) -> list[str]:
    """The parts of a source URL that must never be shown: the URL itself, its password and user."""
    out = [url]
    with contextlib.suppress(ValueError):
        parts = urlsplit(url.split("ffmpeg:", 1)[-1])
        out += [p for p in (parts.password, parts.username) if p]
        out += [v for k, _, v in (q.partition("=") for q in parts.query.split("&"))
                if k.lower() in ("password", "pass", "pwd", "token") and v]
    return out


def fingerprint(source: str) -> str:
    """Whether a source changed, without keeping a second copy of it around in plain text."""
    return hashlib.sha256(source.encode()).hexdigest()[:16]


def stream_name(entity_id: str, quality: str) -> str:
    return f"{entity_id}:{quality}"


def parse_stream_name(name: str) -> tuple[str, str] | None:
    m = _STREAM_NAME.match(name or "")
    return (m.group(1), m.group(2)) if m else None


# -- options ----------------------------------------------------------------------

def _int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def webrtc_settings(options: dict[str, Any]) -> dict[str, Any]:
    """The ``webrtc`` section go2rtc should run with, from the entry's options.

    With a WebRTC address that is an IP, go2rtc also listens only there: otherwise it takes the port on every
    address the host has (Docker bridges, IPv6), none of which a phone can use.
    """
    port = _int(options.get("webrtc_port"), DEFAULT_WEBRTC_PORT)
    host = str(options.get("webrtc_host") or "").strip()
    bind = ""
    with contextlib.suppress(ValueError):
        bind = str(ipaddress.ip_address(host.strip("[]")))
    listen = (f"[{bind}]:{port}" if ":" in bind else f"{bind}:{port}") if bind else f":{port}"
    return {"listen": listen, "candidates": [f"{host}:{port}"] if host else []}


def camera_options(options: dict[str, Any], entity_id: str) -> dict[str, Any]:
    cams = options.get("cameras")
    cam = cams.get(entity_id) if isinstance(cams, dict) else None
    return dict(cam) if isinstance(cam, dict) else {}


def validate_options(patch: dict[str, Any]) -> dict[str, Any]:
    """A settings change, checked; raises ``ValueError`` with words for people."""
    out: dict[str, Any] = {}
    if "webrtc_host" in patch:
        host = str(patch["webrtc_host"] or "").strip()
        if host and not re.fullmatch(r"[A-Za-z0-9.\-:\[\]]{1,253}", host):
            raise ValueError("The WebRTC address must be a host name or an IP address")
        out["webrtc_host"] = host
    if "webrtc_port" in patch:
        port = _int(patch["webrtc_port"], -1)
        if not 1024 <= port <= 65535:
            raise ValueError("The WebRTC port must be between 1024 and 65535")
        out["webrtc_port"] = port
    if "default_quality" in patch:
        if patch["default_quality"] not in QUALITIES:
            raise ValueError("The default quality must be sub or main")
        out["default_quality"] = patch["default_quality"]
    if "cameras" in patch:
        cams = patch["cameras"]
        if not isinstance(cams, dict):
            raise ValueError("cameras must be an object of camera settings")
        clean: dict[str, Any] = {}
        for eid, cam in cams.items():
            if not re.fullmatch(r"camera\.[a-z0-9_]+", str(eid)):
                raise ValueError(f"{eid!r} is not a camera")
            if cam is None:
                clean[eid] = None
                continue
            if not isinstance(cam, dict):
                raise ValueError(f"The settings of {eid} must be an object")
            item: dict[str, Any] = {}
            if "quality" in cam:
                if cam["quality"] not in QUALITIES:
                    raise ValueError(f"{eid}: quality must be sub or main")
                item["quality"] = cam["quality"]
            for key in ("on_demand", "disabled", "transcode"):
                if key in cam:
                    item[key] = bool(cam[key])
            clean[eid] = item
        out["cameras"] = clean
    return out


def merge_options(current: dict[str, Any], change: dict[str, Any]) -> dict[str, Any]:
    """``current`` with a validated change applied (a camera set to None or {} loses its own settings)."""
    out = dict(current)
    for key, value in change.items():
        if key != "cameras":
            out[key] = value
            continue
        cams = dict(out.get("cameras") or {})
        for eid, cam in value.items():
            # A camera's settings are replaced whole: leaving a key out returns it to the default.
            if cam:
                cams[eid] = cam
            else:
                cams.pop(eid, None)
        out["cameras"] = cams
    return out


# -- go2rtc's API -------------------------------------------------------------------

class Go2rtcError(Exception):
    """go2rtc did not answer, or refused; the message is already redacted."""

    def __init__(self, message: str, status: int = 502) -> None:
        super().__init__(message)
        self.status = status


class Go2rtcClient:
    """The few go2rtc API calls the hub makes. ``secrets`` is consulted to redact every error."""

    def __init__(self, url: str, *, username: str = "", password: str = "") -> None:
        self.url = (url or DEFAULT_URL).rstrip("/")
        self._auth = aiohttp.BasicAuth(username, password) if username else None
        self._session: aiohttp.ClientSession | None = None
        self.secrets: list[str] = [password] if password else []

    def session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(auth=self._auth, cookie_jar=aiohttp.DummyCookieJar())
        return self._session

    async def close(self) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None

    @property
    def auth_header(self) -> dict[str, str]:
        return {"Authorization": self._auth.encode()} if self._auth is not None else {}

    async def request(self, method: str, path: str, *, params: dict[str, str] | None = None,
                      data: Any = None, headers: dict[str, str] | None = None,
                      timeout: aiohttp.ClientTimeout = _API_TIMEOUT) -> tuple[int, bytes, str]:
        try:
            async with self.session().request(method, f"{self.url}{path}", params=params, data=data,
                                              headers=headers, timeout=timeout) as resp:
                return resp.status, await resp.read(), resp.headers.get("Content-Type", "")
        except (TimeoutError, aiohttp.ClientError) as exc:
            raise Go2rtcError(f"go2rtc did not answer ({type(exc).__name__})") from None

    async def _ok(self, method: str, path: str, *, params: dict[str, str] | None = None, data: Any = None,
                  extra_secrets: list[str] | None = None) -> bytes:
        status, body, _ctype = await self.request(method, path, params=params, data=data)
        if status >= 400:
            text = redact(body.decode(errors="replace").strip()[:300], self.secrets + (extra_secrets or []))
            raise Go2rtcError(f"go2rtc answered HTTP {status}: {text}" if text else f"go2rtc answered HTTP {status}",
                              status)
        return body

    async def info(self) -> dict[str, Any]:
        body = await self._ok("GET", "/api")
        with contextlib.suppress(ValueError):
            data = json.loads(body or b"{}")
            if isinstance(data, dict):
                return {k: data.get(k) for k in ("version", "config_path") if k in data}
        return {}

    async def stream_names(self) -> set[str]:
        body = await self._ok("GET", "/api/streams")
        try:
            data = json.loads(body or b"{}")
        except ValueError:
            return set()
        return set(data) if isinstance(data, dict) else set()

    async def set_stream(self, name: str, source: str) -> None:
        # PATCH, not PUT: PUT also writes the source (with its password) to go2rtc's config file.
        await self._ok("PATCH", "/api/streams", params={"name": name, "src": source},
                       extra_secrets=source_secrets(source))

    async def delete_stream(self, name: str) -> None:
        await self._ok("DELETE", "/api/streams", params={"src": name})

    async def preloads(self) -> set[str]:
        status, body, _ = await self.request("GET", "/api/preload")
        if status != 200:
            return set()
        try:
            data = json.loads(body or b"{}")
        except ValueError:
            return set()
        return set(data) if isinstance(data, dict) else set()

    async def set_preload(self, name: str, on: bool) -> None:
        await self._ok("PUT" if on else "DELETE", "/api/preload", params={"src": name})

    async def config(self) -> dict[str, Any]:
        status, body, _ = await self.request("GET", "/api/config")
        if status != 200 or not body:
            return {}
        try:
            data = yaml.safe_load(body)
        except yaml.YAMLError:
            return {}
        return data if isinstance(data, dict) else {}

    async def patch_config(self, patch: dict[str, Any]) -> None:
        # JSON is YAML; go2rtc merges it into its config file.
        await self._ok("PATCH", "/api/config", data=json.dumps(patch))

    async def restart(self) -> None:
        await self._ok("POST", "/api/restart")


# -- the integration ---------------------------------------------------------------------

@dataclass
class CameraStreams:
    """What the hub knows about one camera's streams on go2rtc (no URLs)."""

    entity_id: str
    qualities: list[str] = field(default_factory=list)
    default: str = "sub"
    battery: bool = False
    on_demand: bool = True
    registered: dict[str, str] = field(default_factory=dict)  # quality -> fingerprint
    note: str | None = None


class Go2rtcDriver(Driver):
    """One go2rtc server. It owns no entities; it keeps go2rtc's streams in step with the cameras."""

    platform = DOMAIN
    #: Re-sync this often: cameras added or removed, a password changed, or go2rtc restarted.
    poll_interval = 60.0

    def __init__(self, hub: Hub, entry: Any) -> None:
        super().__init__(hub, entry)
        data = entry.data or {}
        self.client = Go2rtcClient(str(data.get("url") or DEFAULT_URL), username=str(data.get("username") or ""),
                                   password=str(data.get("password") or ""))
        self.cameras: dict[str, CameraStreams] = {}
        self.version: str | None = None
        self.last_error: str | None = None
        self._sync_lock = asyncio.Lock()

    # -- lifecycle ------------------------------------------------------------
    async def async_setup(self) -> None:
        try:
            info = await self.client.info()
        except Go2rtcError as exc:
            await self.client.close()
            raise DeviceUnavailable(str(exc)) from None
        self.version = info.get("version")
        await self.apply_webrtc_config()
        await self.sync()

    async def async_poll(self) -> None:
        await self.sync()

    async def async_unload(self) -> None:
        await self.client.close()

    # -- go2rtc's own settings ------------------------------------------------
    async def apply_webrtc_config(self) -> bool:
        """Make go2rtc's WebRTC port and address what the options say; restart it if they changed."""
        want = webrtc_settings(self.entry.options or {})
        try:
            current = (await self.client.config()).get("webrtc") or {}
        except Go2rtcError:
            current = {}
        have = {"listen": current.get("listen") or f":{DEFAULT_WEBRTC_PORT}",
                "candidates": list(current.get("candidates") or [])}
        if have == want:
            return False
        try:
            await self.client.patch_config({"webrtc": want})
            await self.client.restart()
        except Go2rtcError as exc:
            self.last_error = f"Could not change go2rtc's WebRTC settings: {exc}"
            self.log.warning("%s", self.last_error)
            return False
        self.log.info("go2rtc WebRTC set to %s with %d candidate(s); restarting it", want["listen"],
                      len(want["candidates"]))
        for c in self.cameras.values():
            c.registered.clear()  # a restart forgets every stream
        await self._wait_back()
        return True

    async def _wait_back(self) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + RESTART_WAIT_S
        await asyncio.sleep(0.5)
        while loop.time() < deadline:
            with contextlib.suppress(Go2rtcError):
                await self.client.info()
                return
            await asyncio.sleep(0.5)

    # -- streams --------------------------------------------------------------
    def _camera_entities(self) -> list[EntityEntry]:
        return sorted((e for e in self.hub.entities.values() if e.domain == "camera" and not e.disabled_by),
                      key=lambda e: e.entity_id)

    def _driver_for(self, ent: EntityEntry) -> Any:
        return self.hub.drivers.get(ent.config_entry_id or "")

    async def sources_for(self, ent: EntityEntry) -> tuple[dict[str, str], str | None]:
        """``({quality: source}, note)`` for one camera, from its driver. URLs stay in this process."""
        driver = self._driver_for(ent)
        cam_opts = camera_options(self.entry.options or {}, ent.entity_id)
        if driver is None:
            return {}, "its integration is not running"
        sources: dict[str, str] = {}
        if hasattr(driver, "async_camera_stream_sources"):
            got = await driver.async_camera_stream_sources(ent)
            sources = {q: u for q, u in (got or {}).items() if q in QUALITIES and u}
        elif hasattr(driver, "async_camera_stream_url"):
            url = await driver.async_camera_stream_url(ent)
            scheme = urlsplit(url).scheme.lower() if url else ""
            if scheme in _DIRECT_SCHEMES:
                sources = {"main": url}
            elif scheme in _HTTP_SCHEMES:
                if not cam_opts.get("transcode"):
                    return {}, "an MJPEG camera: shown through the hub's own MJPEG relay (turn on transcode to use go2rtc)"
                # Browsers cannot play MJPEG over WebRTC or MSE: go2rtc's ffmpeg makes H.264 of it.
                sources = {"main": f"ffmpeg:{url}#video=h264"}
        if not sources:
            return {}, "its integration has no video stream for go2rtc"
        return sources, None

    def _default_quality(self, ent: EntityEntry, qualities: list[str]) -> str:
        cam_opts = camera_options(self.entry.options or {}, ent.entity_id)
        own = ent.unique_id.rsplit("_", 1)[-1] if ent.platform == "reolink" else ""
        for q in (cam_opts.get("quality"), own, (self.entry.options or {}).get("default_quality"), "sub", "main"):
            if q in qualities:
                return str(q)
        return qualities[0] if qualities else "sub"

    def _battery(self, ent: EntityEntry) -> bool:
        driver = self._driver_for(ent)
        info = getattr(driver, "camera_info", None)
        if callable(info):
            with contextlib.suppress(Exception):
                return bool((info(ent) or {}).get("battery"))
        return False

    async def sync(self) -> None:
        """Register every camera's streams, re-register changed or lost ones, and drop stale ones."""
        async with self._sync_lock:
            try:
                live = await self.client.stream_names()
                preloaded = await self.client.preloads()
            except Go2rtcError as exc:
                self.last_error = str(exc)
                raise DeviceUnavailable(str(exc)) from None
            seen: set[str] = set()
            entities = self._camera_entities()
            for ent in entities:
                cam = await self._sync_camera(ent, live, preloaded)
                if cam is not None:
                    seen.update(stream_name(ent.entity_id, q) for q in cam.registered)
            current = {e.entity_id for e in entities}
            for eid in [e for e in self.cameras if e not in current]:
                del self.cameras[eid]
            for name in live - seen:
                if parse_stream_name(name) is None:
                    continue  # not one of the hub's: leave go2rtc's own streams alone
                with contextlib.suppress(Go2rtcError):
                    await self.client.delete_stream(name)
                    if name in preloaded:
                        await self.client.set_preload(name, False)
                    self.log.info("removed go2rtc stream %s (the camera is gone or turned off)", name)
            self.last_error = None

    async def _sync_camera(self, ent: EntityEntry, live: set[str], preloaded: set[str]) -> CameraStreams | None:
        cam_opts = camera_options(self.entry.options or {}, ent.entity_id)
        cam = self.cameras.get(ent.entity_id) or CameraStreams(ent.entity_id)
        self.cameras[ent.entity_id] = cam
        if cam_opts.get("disabled"):
            cam.qualities, cam.registered, cam.note = [], {}, "turned off in the live video settings"
            return cam
        try:
            sources, note = await self.sources_for(ent)
        except Exception as exc:  # a driver's error text can carry its URL
            sources, note = {}, f"its stream address could not be read ({type(exc).__name__})"
        cam.note = note
        cam.battery = self._battery(ent)
        cam.qualities = [q for q in QUALITIES if q in sources]
        cam.default = self._default_quality(ent, cam.qualities)
        # A battery camera held open by a preload would never sleep.
        cam.on_demand = bool(cam_opts.get("on_demand", True)) or cam.battery
        if cam_opts.get("on_demand") is False and cam.battery:
            cam.note = "a battery camera always streams on demand, so it can sleep"
        for quality in list(cam.registered):
            if quality not in sources:
                cam.registered.pop(quality)
        for quality, source in sources.items():
            name = stream_name(ent.entity_id, quality)
            fp = fingerprint(source)
            if cam.registered.get(quality) == fp and name in live:
                continue
            try:
                await self.client.set_stream(name, source)
            except Go2rtcError as exc:
                cam.registered.pop(quality, None)
                cam.note = f"go2rtc refused the {quality} stream: {exc}"
                self.log.warning("%s: go2rtc refused the %s stream: %s", ent.entity_id, quality, exc)
                continue
            cam.registered[quality] = fp
            live.add(name)
            self.log.info("%s: %s stream registered with go2rtc", ent.entity_id, quality)
        if not cam.registered:
            return cam
        want_preload = {stream_name(ent.entity_id, cam.default)} if not cam.on_demand else set()
        for quality in cam.registered:
            name = stream_name(ent.entity_id, quality)
            on = name in want_preload
            if on != (name in preloaded):
                with contextlib.suppress(Go2rtcError):
                    await self.client.set_preload(name, on)
        return cam

    async def ensure(self, entity_id: str, quality: str | None) -> str:
        """The go2rtc stream name to play for a camera, registering it first if needed."""
        ent = self.hub.entities.get(entity_id)
        if ent is None or ent.domain != "camera" or ent.disabled_by:
            raise LookupError("Entity not found.")
        cam = self.cameras.get(entity_id)
        if cam is None or not cam.registered:
            async with self._sync_lock:
                with contextlib.suppress(Go2rtcError):
                    live = await self.client.stream_names()
                    preloaded = await self.client.preloads()
                    cam = await self._sync_camera(ent, live, preloaded)
        if cam is None or not cam.registered:
            raise PermissionError((cam.note if cam is not None else None) or "No real-time video for this camera")
        q = quality if quality in cam.registered else cam.default if cam.default in cam.registered \
            else next(iter(cam.registered))
        return stream_name(entity_id, q)

    # -- what the camera list and the settings show -----------------------------
    def describe(self, entity_id: str) -> dict[str, Any] | None:
        cam = self.cameras.get(entity_id)
        if cam is None or not cam.registered:
            return None
        qualities = [q for q in QUALITIES if q in cam.registered]
        return {"modes": list(MODES), "qualities": qualities,
                "default_quality": cam.default if cam.default in qualities else qualities[0],
                "on_demand": cam.on_demand}

    def status(self) -> dict[str, Any]:
        return {"version": self.version, "error": self.last_error,
                "cameras": {eid: {"qualities": c.qualities, "registered": sorted(c.registered),
                                  "default_quality": c.default, "battery": c.battery, "on_demand": c.on_demand,
                                  "note": c.note} for eid, c in sorted(self.cameras.items())}}


def service(hub: Hub) -> Go2rtcDriver | None:
    """The running go2rtc integration, if there is one."""
    for driver in hub.drivers.values():
        if isinstance(driver, Go2rtcDriver):
            return driver
    return None


def camera_summary(hub: Hub, entity_id: str) -> dict[str, Any]:
    """Extra keys for a camera in ``/api/homehub/cameras``: ``realtime`` when go2rtc can play it."""
    svc = service(hub)
    info = svc.describe(entity_id) if svc is not None else None
    return {"realtime": info} if info else {}
